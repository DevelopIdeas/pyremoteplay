import logging
import queue
import socket
import threading
import time
from base64 import b64decode, b64encode
from enum import IntEnum
from struct import pack_into

import requests
from Cryptodome.Random import get_random_bytes
from pyps4_2ndscreen.helpers import Helper

from .av import AVReceiver
from .const import (OS_TYPE, RP_CRYPT_SIZE, RP_PORT, RP_VERSION, TYPE_PS4,
                    TYPE_PS5, USER_AGENT)
from .crypt import RPCipher
from .errors import RemotePlayError
from .feedback import Controller
from .keys import CTRL_KEY_0, CTRL_KEY_1
from .stream import RPStream
from .util import from_b, listener, log_bytes

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

RP_INIT_URL = "/sie/ps4/rp/sess/init"
RP_CTRL_URL = "/sie/ps4/rp/sess/ctrl"
REGIST_IP = "0.0.0.0"
BROADCAST_IP = "255.255.255.255"

DID_PREFIX = b'\x00\x18\x00\x00\x00\x07\x00\x40\x00\x80'

HEARTBEAT_RESPONSE = b"\x00\x00\x00\x00\x01\xfe\x00\x00"


def _check_host(host: str):
    """Return True if host is available."""
    helper = Helper()
    devices = helper.has_devices(host)
    if not devices:
        _LOGGER.error("Could not detect PS4 at: %s", host)
        return False
    return True


def _get_headers(host: str, regist_key: str) -> dict:
    """Return headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "close",
        "Content-Length": "0",
        "RP-Registkey": regist_key,
        "Rp-Version": RP_VERSION,
    }
    return headers


def _get_ctrl_headers(host: str, auth: str, did: str, os_type: str, bitrate: str):
    """Return Connect Headers."""
    headers = {
        "Host": f"{host}:{RP_PORT}",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
        "Content-Length": "0",
        "RP-Auth": auth,
        "RP-Version": RP_VERSION,
        "RP-Did": did,
        "RP-ControllerType": "3",
        "RP-ClientType": "11",
        "RP-OSType": os_type,
        "RP-ConPath": "1",
        "RP-StartBitrate": bitrate,
    }
    _LOGGER.debug("CTRL Headers: %s", headers)
    return headers


def _get_rp_nonce(nonce: bytes) -> bytes:
    """Return RP nonce."""
    rp_nonce = bytearray(RP_CRYPT_SIZE)
    key = CTRL_KEY_0[((nonce[0] >> 3) * 112):]
    for index in range(0, RP_CRYPT_SIZE):
        shift = nonce[index] + 54 + index
        shift ^= key[index]
        rp_nonce[index] = shift % 256
    rp_nonce = bytes(rp_nonce)
    log_bytes("RP Nonce", rp_nonce)
    return rp_nonce


def _get_aes_key(nonce: bytes, rp_key: bytes) -> bytes:
    """Return AES key."""
    aes_key = bytearray(16)
    key = CTRL_KEY_1[((nonce[7] >> 3) * 112):]
    for index in range(0, RP_CRYPT_SIZE):
        shift = (key[index] ^ rp_key[index]) + 33 + index
        shift ^= nonce[index]
        aes_key[index] = shift % 256
    aes_key = bytes(aes_key)
    log_bytes("AES Key", aes_key)
    return aes_key


def _gen_did() -> bytes:
    """Generate Device ID."""
    did = b"".join([DID_PREFIX, get_random_bytes(16), bytes(6)])
    log_bytes("Device ID", did)
    return did


class CTRL():
    """Controller for RP Session."""
    STATE_INIT = "init"
    STATE_READY = "ready"
    HEADER_LENGTH = 8

    class MessageType(IntEnum):
        """Enum for Message Types."""
        LOGIN_PIN_REQUEST = 0x04
        LOGIN_PIN_RESPONSE = 0x8004
        LOGIN = 0x05
        SESSION_ID = 0x33
        HEARTBEAT_REQUEST = 0xfe
        HEARTBEAT_RESPONSE = 0x1fe
        STANDBY = 0x50
        KEYBOARD_ENABLE_TOGGLE = 0x20
        KEYBOARD_OPEN = 0x21
        KEYBOARD_CLOSE_REMOTE = 0x22
        KEYBOARD_TEXT_CHANGE_REQ = 0x23
        KEYBOARD_TEXT_CHANGE_RES = 0x24
        KEYBOARD_CLOSE_REQ = 0x25

    class Error(IntEnum):
        """Enum for errors."""
        REGIST_FAILED = 0x80108b09
        INVALID_PSN_ID = 0x80108b02
        RP_IN_USE = 0x80108b10
        CRASH = 0x80108b15
        RP_VERSION_MISMATCH = 0x80108b11
        UNKNOWN = 0x80108bff

    def __init__(self, host: str, regist_data: dict, cb_start=None):
        self._host = host
        self._regist_data = regist_data
        self._session_id = b''
        self._type = ""
        self._mac_address = ""
        self._name = ""
        self._regist_key = None
        self._rp_key = None
        self._sock = None
        self._send_buf = queue.Queue()
        self._stop_event = threading.Event()
        self._hb_last = 0
        self._cipher = None
        self._state = CTRL.STATE_INIT
        self._stream = None
        self._cb_start = cb_start
        self.controller = None
        self.av_receiver = AVReceiver()

        self._init_attrs()

    def _init_attrs(self):
        """Init Class attrs."""
        _regist_str = "".join(list(self._regist_data.keys()))
        if TYPE_PS4 in _regist_str:
            self._type = TYPE_PS4
        elif TYPE_PS5 in _regist_str:
            self._type = TYPE_PS5

        self._mac_address = self._regist_data[f"{self.type}-Mac"]
        self._name = self._regist_data[f"{self.type}-Nickname"]
        self._regist_key = self._regist_data[f"{self.type}-RegistKey"]
        self._rp_key = bytes.fromhex(self._regist_data["RP-Key"])

    def _init_ctrl(self) -> requests.models.Response:
        """Init Connect."""
        response = None
        headers = _get_headers(self.host, self._regist_key)
        url = f"http://{self.host}:{RP_PORT}{RP_INIT_URL}"
        response = requests.get(url, headers=headers)
        return response

    def _parse_init(self, response: requests.models.Response) -> bytes:
        """Parse init response."""
        nonce = None
        _LOGGER.debug(response.headers)
        if response.status_code != 200:
            reason = response.headers.get("RP-Application-Reason")
            reason = int.from_bytes(bytes.fromhex(reason), "big")
            _LOGGER.error(
                "Failed to Init CTRL; Reason: %s",
                CTRL.Error(reason).name,
            )
        nonce = response.headers.get("RP-Nonce")
        if nonce is not None:
            nonce = b64decode(nonce.encode())
            log_bytes("Nonce", nonce)
        return nonce

    def _get_ctrl_headers(self, nonce: bytes) -> dict:
        """Return CTRL headers."""
        rp_nonce = _get_rp_nonce(nonce)
        aes_key = _get_aes_key(nonce, self._rp_key)
        self._cipher = RPCipher(aes_key, rp_nonce, counter=0)

        regist_key = b''.join([bytes.fromhex(self._regist_key), bytes(8)])
        auth = b64encode(self._cipher.encrypt(regist_key)).decode()
        did = b64encode(self._cipher.encrypt(_gen_did())).decode()
        os_type = b64encode(self._cipher.encrypt(OS_TYPE.encode().ljust(10, b'\x00'))).decode()
        bitrate = b64encode(self._cipher.encrypt(bytes(4))).decode()
        return _get_ctrl_headers(self.host, auth, did, os_type, bitrate)

    def _send_auth(self, headers: dict) -> bool:
        """Send CTRL Auth."""
        url = f"http://{self.host}:{RP_PORT}{RP_CTRL_URL}"
        response = requests.get(url, headers=headers, stream=True)
        _LOGGER.debug("CTRL Auth Headers: %s", response.headers)
        server_type = response.headers.get("RP-Server-Type")
        if response.status_code != 200 or server_type is None:
            return False
        self._server_type = from_b(
            self._cipher.decrypt(b64decode(server_type)), 'little')
        _LOGGER.debug("Server Type: %s", self._server_type)
        self._sock = socket.fromfd(
            response.raw.fileno(), socket.AF_INET, socket.SOCK_STREAM)
        return True

    def _handle(self, data: bytes):
        """Handle Data."""
        payload = data[8:]
        if payload:
            payload = self._cipher.decrypt(payload)
            log_bytes("CTRL PAYLOAD", payload)
        try:
            msg_type = CTRL.MessageType(data[5])
            _LOGGER.debug("RECV %s", CTRL.MessageType(msg_type).name)
        except ValueError:
            _LOGGER.warning("CTRL RECV invalid Message Type: %s", data[5])
            return
        if msg_type == CTRL.MessageType.HEARTBEAT_REQUEST:
            self._hb_last = time.time()
            self._send_hb_response()
        if msg_type == CTRL.MessageType.HEARTBEAT_RESPONSE:
            self._hb_last = time.time()
        elif msg_type == CTRL.MessageType.SESSION_ID:
            session_id = payload[2:]
            log_bytes("Session ID", session_id)
            try:
                session_id.decode()
            except UnicodeDecodeError:
                _LOGGER.warning("CTRL RECV Malformed Session ID")
                #self.send_disconnect()
                return
            self._session_id = session_id
            if self._cb_start is not None:
                self._cb_start(self)
            else:
                self.start_stream()

        if time.time() - self._hb_last > 5:
            _LOGGER.info("CTRL HB Timeout. Sending HB")
            self._send_hb_request()

    def _build_msg(self, msg_type: int, payload=b'') -> bytes:
        """Return Message."""
        payload_size = len(payload)
        self._cipher.encrypt(payload)
        buf = bytearray(CTRL.HEADER_LENGTH + payload_size)
        pack_into(f"!IHxx{payload_size}s", buf, 0, payload_size, msg_type, payload)
        return bytes(buf)

    def _send_hb_response(self):
        msg = self._build_msg(CTRL.MessageType.HEARTBEAT_RESPONSE, HEARTBEAT_RESPONSE)
        self._sock.send(msg)

    def _send_hb_request(self):
        msg = self._build_msg(CTRL.MessageType.HEARTBEAT_REQUEST)
        self._sock.send(msg)

    def _send_standby(self):
        msg = self._build_msg(CTRL.MessageType.STANDBY)
        self._sock.send(msg)

    def send(self):
        if not self._send_buf.empty():
            data_send = self._send_buf.get_nowait()
            self._sock.send(data_send)
            log_bytes(f"CTRL Send", data_send)

    def start(self):
        """Start CTRL/RP Session."""
        if not _check_host(self.host):
            return False
        if not self.connect():
            _LOGGER.error("CTRL Failed Auth")
            return False
        _LOGGER.info("CTRL Auth Success")
        self._state = CTRL.STATE_READY
        self._worker = threading.Thread(
            target=listener,
            args=("CTRL", self._sock, self._handle, self.send, self._stop_event),
        )
        self._worker.start()

    def connect(self) -> bool:
        """Connect to Host."""
        response = self._init_ctrl()
        nonce = self._parse_init(response)
        if nonce is None:
            return False
        headers = self._get_ctrl_headers(nonce)
        return self._send_auth(headers)

    def _cb_stop_test(self):
        """Stop test and get MTU and RTT and start stream."""
        mtu = self._stream.mtu
        rtt = self._stream.rtt
        _LOGGER.info("Tested network and got MTU: %s; RTT: %sms", mtu, rtt * 1000)
        self._stream = None
        self.start_stream(test=False, mtu=mtu, rtt=rtt)

    def start_stream(self, test=True, mtu=None, rtt=None):
        """Start Stream."""
        if not self.session_id:
            _LOGGER.error("Session ID not received")
            return
        stop_event = self._stop_event if not test else threading.Event()
        cb_stop = self._cb_stop_test if test else None
        self._stream = RPStream(self._host, stop_event, self, is_test=test, cb_stop=cb_stop, mtu=mtu, rtt=rtt)
        self._stream.connect()

    def init_controller(self):
        self.controller = Controller(self._stream, self._stop_event)
        self.controller.start()

    @property
    def host(self) -> str:
        """Return host address."""
        return self._host

    @property
    def type(self) -> str:
        """Return host type."""
        return self._type

    @property
    def name(self) -> str:
        """Return host name."""
        return self._name

    @property
    def mac_address(self) -> str:
        """Return host MAC Adddress."""
        return self._mac_address

    @property
    def state(self) -> str:
        """Return State."""
        return self._state

    @property
    def session_id(self) -> bytes:
        """Return Session ID."""
        return self._session_id
