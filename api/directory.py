"""GFIT-CoWork -- the Directory: who is this person, and is the password right?

The Directory has one job: given a username and a password, return an
:class:`Identity` (the normalised employee ID and a display name) or ``None``
when authentication fails. Everything else in the login path sees only this
interface, so the in-memory Directory (tests, local development) and the LDAP
Directory (company AD, ticket 08) are interchangeable.

The implementation is chosen with ``HERMES_WEBUI_DIRECTORY``:

* unset -- Directory login is off.
* ``memory`` -- :class:`InMemoryDirectory`, loaded from the JSON file named by
  ``HERMES_WEBUI_DIRECTORY_USERS``: ``{"521740": {"password": "...",
  "display_name": "..."}}``. It stands in for AD, so it holds mock passwords.
* anything else -- Directory login is on but refuses everyone (fail closed), so
  a typo in the config never opens the door.

GFIT-CoWork never stores or logs a User's password; it is only handed to the
Directory.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, Protocol

logger = logging.getLogger(__name__)

DIRECTORY_ENV = "HERMES_WEBUI_DIRECTORY"
DIRECTORY_USERS_ENV = "HERMES_WEBUI_DIRECTORY_USERS"


class Identity(NamedTuple):
    """A person the Directory vouched for."""

    employee_id: str
    display_name: str


class Directory(Protocol):
    def authenticate(self, username: str, password: str) -> Identity | None:
        """Return the Identity when *password* is right for *username*, else None."""


def normalize_username(raw) -> str | None:
    """Turn ``GFIT\\521740``, ``521740@gfit.co.th`` or ``521740`` into ``521740``.

    Strips a ``DOMAIN\\`` prefix and an ``@domain`` suffix, then lowercases. The
    result is a Profile name, so it must follow the Profile name rules; any name
    that does not (including the built-in ``default``) is refused with ``None``.
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if name.count("\\") > 1 or name.count("@") > 1:
        return None
    name = name.split("\\", 1)[-1]
    name = name.split("@", 1)[0]
    name = name.lower()
    from api.profiles import _validate_profile_name

    try:
        _validate_profile_name(name)
    except ValueError:
        return None
    return name


class InMemoryDirectory:
    """A Directory backed by a dict, standing in for AD in tests and development."""

    def __init__(self, users: Mapping[str, Mapping]):
        self._users: dict[str, tuple[str, str]] = {}
        for raw_name, entry in users.items():
            name = normalize_username(raw_name)
            if name is None or not isinstance(entry, Mapping):
                raise ValueError(f"invalid Directory entry {raw_name!r}")
            password = entry.get("password")
            if not isinstance(password, str) or not password:
                raise ValueError(f"Directory entry {raw_name!r} has no password")
            display_name = str(entry.get("display_name") or "").strip() or name
            self._users[name] = (password, display_name)

    @classmethod
    def from_file(cls, path: Path) -> "InMemoryDirectory":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Directory users file must hold a JSON object")
        return cls(data)

    def authenticate(self, username, password) -> Identity | None:
        name = normalize_username(username)
        if name is None or not isinstance(password, str) or not password:
            return None
        stored = self._users.get(name)
        # Compare even for an unknown user, so timing does not reveal who exists.
        expected = stored[0] if stored else "\x00" + password
        if not hmac.compare_digest(password.encode(), expected.encode()) or stored is None:
            return None
        return Identity(name, stored[1])


class _RefuseAll:
    """The fail-closed Directory for a broken or unknown configuration."""

    def authenticate(self, username, password) -> Identity | None:
        return None


def is_directory_enabled() -> bool:
    return bool(os.getenv(DIRECTORY_ENV, "").strip())


def get_directory() -> Directory | None:
    """Return the configured Directory, or None when Directory login is off."""
    kind = os.getenv(DIRECTORY_ENV, "").strip().lower()
    if not kind:
        return None
    if kind == "memory":
        path = os.getenv(DIRECTORY_USERS_ENV, "").strip()
        try:
            return InMemoryDirectory.from_file(Path(path).expanduser())
        except (OSError, ValueError) as exc:
            logger.warning(
                "In-memory Directory users file %r is unusable (%s); refusing every login",
                path, type(exc).__name__,
            )
            return _RefuseAll()
    logger.warning("Unknown %s=%r; refusing every login", DIRECTORY_ENV, kind)
    return _RefuseAll()
