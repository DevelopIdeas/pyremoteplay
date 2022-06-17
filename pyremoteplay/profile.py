"""Collections for User Profiles."""
from __future__ import annotations
from collections import UserDict

from .util import get_profiles, write_profiles, get_users, add_regist_data


class HostProfile(UserDict):
    """Host Profile for User."""

    def __init__(self, name: str, data: dict):
        if not name or not isinstance(name, str):
            raise ValueError("Name must be a non-blank string")
        self.__name = name
        self.__type = data["type"]
        super().__init__(data["data"])
        self._verify()

    def _verify(self):
        assert self.name, "Attribute 'name' cannot be empty"
        assert self.regist_key, "Attribute 'regist_key' cannot be empty"
        assert self.rp_key, "Attribute 'rp_key' cannot be empty"

    @property
    def name(self) -> str:
        """Return Name / Mac Address."""
        return self.__name

    @property
    def type(self) -> str:
        """Return type."""
        return self.__type

    @property
    def regist_key(self) -> str:
        """Return Regist Key."""
        return self.data["RegistKey"]

    @property
    def rp_key(self) -> str:
        """Return RP Key."""
        return self.data["RP-Key"]


class UserProfile(UserDict):
    """PSN User Profile. Collection of Host Profiles for user."""

    def __init__(self, name: str, data: dict):
        if not name or not isinstance(name, str):
            raise ValueError("Name must be a non-blank string")
        self.__name = name
        super().__init__(data)
        self._verify()

    def _verify(self):
        assert self.name, "Attribute 'name' cannot be empty"
        assert self.id, "Attribute 'id' cannot be empty"

    def update_host(self, host: HostProfile):
        """Update host profile.

        :param: host: Host Profile
        """
        if not isinstance(host, HostProfile):
            raise ValueError(f"Expected instance of {HostProfile}. Got {type(host)}")
        # pylint: disable=protected-access
        host._verify()
        self[host.name] = host.data

    def add_regist_data(self, host_status: dict, data: dict):
        """Add regist data to user profile."""
        add_regist_data(self.data, host_status, data)

    @property
    def name(self) -> str:
        """Return PSN Username."""
        return self.__name

    # pylint: disable=invalid-name
    @property
    def id(self) -> str:
        """Return Base64 encoded User ID."""
        return self.data["id"]

    @property
    def hosts(self) -> list[HostProfile]:
        """Return Host profiles."""
        hosts = self.data.get("hosts")
        if not hosts:
            return []
        return [HostProfile(name, data) for name, data in hosts.items()]


class Profiles(UserDict):
    """Collection of User Profiles."""

    __DEFAULT_PATH: str = ""

    @classmethod
    def set_default_path(cls, path: str):
        """Set default path for loading and saving.

        :param path: Path to file.
        """
        cls.__DEFAULT_PATH = path

    @classmethod
    def default_path(cls) -> str:
        """Return default path."""
        return cls.__DEFAULT_PATH

    @classmethod
    def load(cls, path: str = "") -> Profiles:
        """Load profiles from file.

        :param path: Path to file. If not given will use default path.
            File will be created automatically if it does not exist.
        """
        path = cls.__DEFAULT_PATH if not path else path
        return cls(get_profiles(path))

    def update_user(self, user: UserProfile):
        """Update stored User Profile.

        :param user: User Profile
        """
        if not isinstance(user, UserProfile):
            raise ValueError(f"Expected instance of {UserProfile}. Got {type(user)}")
        # pylint: disable=protected-access
        user._verify()
        self[user.name] = user.data

    def update_host(self, user: UserProfile, host: HostProfile):
        """Update host in User Profile.

        :param user: User Profile
        :param host: Host Profile
        """
        user.update_host(host)
        self.update_user(user)

    def save(self, path: str = ""):
        """Save profiles to file.

        :param path: Path to file. If not given will use default path.
        """
        write_profiles(self.data, path)

    def get_users(self, device_id: str) -> list[str]:
        """Return all users that are registered with a device.

        :param device_id: Device ID / Device Mac Address
        """
        return get_users(device_id, self)

    def get_user_profile(self, user: str) -> UserProfile:
        """Return User Profile for user.

        :param user: PSN ID / Username
        """
        profile = None
        for _profile in self.users:
            if _profile.name == user:
                profile = _profile
                break
        return profile

    @property
    def usernames(self) -> list[str]:
        """Return list of user names."""
        return [name for name in self.data]

    @property
    def users(self) -> list[UserProfile]:
        """Return User Profiles."""
        return [UserProfile(name, data) for name, data in self.data.items()]
