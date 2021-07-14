"""Utility Methods."""
import inspect
import json
import logging
import pathlib
import select
import time
from binascii import hexlify

from .const import OPTIONS_FILE, PROFILE_DIR, PROFILE_FILE, MAPPING_FILE

_LOGGER = logging.getLogger(__name__)


def check_dir() -> pathlib.Path:
    """Return path. Check file dir and create dir if not exists."""
    dir_path = pathlib.Path.home() / PROFILE_DIR
    if not dir_path.is_dir():
        dir_path.mkdir()
    return dir_path


def check_file(path: pathlib.Path):
    """Check if file exists and create."""
    if not path.is_file():
        with open(path, "w") as _file:
            json.dump({}, _file)

def get_mapping() -> dict:
    """Return dict of key mapping."""
    data = {}
    dir_path = check_dir()
    path = dir_path / MAPPING_FILE
    check_file(path)
    with open(path, "r") as _file:
        data = json.load(_file)
    return data


def write_mapping(mapping: dict):
    """Write mapping."""
    path = pathlib.Path.home() / PROFILE_DIR / MAPPING_FILE
    with open(path, "w") as _file:
        json.dump(mapping, _file)


def get_options() -> dict:
    """Return dict of options."""
    data = {}
    dir_path = check_dir()
    path = dir_path / OPTIONS_FILE
    check_file(path)
    with open(path, "r") as _file:
        data = json.load(_file)
    return data


def write_options(options: dict):
    """Write options."""
    path = pathlib.Path.home() / PROFILE_DIR / OPTIONS_FILE
    with open(path, "w") as _file:
        json.dump(options, _file)


def get_profiles() -> list:
    """Return Profiles."""
    data = []
    dir_path = check_dir()
    path = dir_path / PROFILE_FILE
    check_file(path)

    if not path.is_file():
        print("File not found")
        return data
    with open(path, "r") as _file:
        data = json.load(_file)
    return data


def write_profiles(profiles: dict):
    """Write profile data."""
    path = pathlib.Path.home() / PROFILE_DIR / PROFILE_FILE

    with open(path, "w") as _file:
        json.dump(profiles, _file)


def add_profile(profiles: dict, user_data: dict) -> dict:
    """Add profile to profiles and return profiles."""
    user_id = user_data.get("user_rpid")
    if not isinstance(user_id, str) and not user_id:
        _LOGGER.error("Invalid user id or user id not found")
        return dict()
    name = user_data["online_id"]
    profile = {
        name: {
            "id": user_id,
            "hosts": {},
        }
    }
    profiles.update(profile)
    return profiles


def log_bytes(name: str, data: bytes):
    """Log bytes."""
    mod = inspect.getmodulename(inspect.stack()[1].filename)
    logging.getLogger(f"{__package__}.{mod}").debug(
        "Length: %s, %s: %s", len(data), name, hexlify(data))


def from_b(_bytes: bytes, order="big") -> int:
    """Return int from hex bytes."""
    return int.from_bytes(_bytes, order)


def to_b(_int: int, length: int = 2, order="big") -> bytes:
    """Return hex bytes from int."""
    return int.to_bytes(_int, length, order)


def listener(name: str, sock, handle, stop_event):
    """Worker for socket."""
    _LOGGER.debug("Thread Started: %s", name)
    stop_event.clear()
    while not stop_event.is_set():
        available, _, _ = select.select([sock], [], [], 0.01)
        if sock in available:
            data = sock.recv(4096)
            # log_bytes(f"{name} RECV", data)
            if len(data) > 0:
                handle(data)
            else:
                stop_event.set()
        time.sleep(0.001)

    sock.close()
    _LOGGER.info(f"{name} Stopped")


def timeit(func):
    """Time Function."""
    def inner(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        elapsed = round(end - start, 8)
        _LOGGER.debug("Timed %s.%s at %s seconds", func.__module__, func.__name__, elapsed)
        return result
    return inner
