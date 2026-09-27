"""GFIT-CoWork -- the Profile roster: who a Profile belongs to, and whether they may log in.

One record per Profile (keyed by the Profile name, which is the employee ID):
the display name, the status (``active`` or ``disabled``) and the last login
time. It lives in the GFIT-CoWork state directory, not in the Hermes Profile
config, so Hermes Agent never sees it.

A Profile with no record is active and shown by its ID alone, so Profiles made
before the roster existed (or outside GFIT-CoWork) keep working. An unreadable
roster fails closed: every Profile counts as disabled until it is fixed.

This module owns the Profile lifecycle: each Admin action on a Profile is one
function here (``disable_profile``, ``enable_profile``) that checks the action
is allowed, keeps the roster in step, returns the Profile's roster view, and
raises ProfileRefused (a message for the Admin and its kind) when it refuses.
Disabling a Profile ends its sessions straight away; its data stays. Creating
and deleting the Hermes Profile still go through ``api.profiles`` from the
HTTP handlers.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from api.config import STATE_DIR

logger = logging.getLogger(__name__)

ROSTER_FILENAME = "gfit_roster.json"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
DISPLAY_NAME_MAX = 200

_LOCK = threading.Lock()
# (path, mtime_ns, size) -> records, so the per-request disabled check does not
# re-read the file each time.
_cache: tuple[tuple, dict[str, dict]] | None = None


class RosterUnreadable(Exception):
    """The roster file exists but cannot be read or parsed."""


# The kinds of ProfileRefused.
REFUSED_BAD_REQUEST = "bad_request"
REFUSED_NOT_FOUND = "not_found"
REFUSED_CONFLICT = "conflict"
REFUSED_SERVER_FAULT = "server_fault"


class ProfileRefused(Exception):
    """An Admin action on a Profile was refused: ``str()`` is the message for the Admin."""

    def __init__(self, message: str, kind: str = REFUSED_BAD_REQUEST):
        super().__init__(message)
        self.kind = kind


def _path() -> Path:
    return Path(STATE_DIR) / ROSTER_FILENAME


def _load() -> dict[str, dict]:
    """Return the roster records. Raises RosterUnreadable. Call with _LOCK held."""
    global _cache
    path = _path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RosterUnreadable(type(exc).__name__) from exc
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _cache is not None and _cache[0] == key:
        return {name: dict(record) for name, record in _cache[1].items()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RosterUnreadable(type(exc).__name__) from exc
    if not isinstance(data, dict):
        raise RosterUnreadable("not a JSON object")
    records = {k: v for k, v in data.items() if isinstance(v, dict)}
    _cache = (key, records)
    return {name: dict(record) for name, record in records.items()}


def _save(records: dict[str, dict]) -> None:
    """Write the records atomically. Call with _LOCK held."""
    global _cache
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".gfit_roster.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    st = path.stat()
    _cache = ((str(path), st.st_mtime_ns, st.st_size), {k: dict(v) for k, v in records.items()})


def _record(name: str) -> dict | None:
    """The record for *name* ({} when there is none), or None when the roster is unreadable."""
    with _LOCK:
        try:
            return _load().get(name, {})
        except RosterUnreadable as exc:
            logger.warning("Profile roster %s is unreadable (%s); treating Profiles as disabled", _path(), exc)
            return None


def clean_display_name(value) -> str:
    """Return *value* as a display name: a trimmed single line, or ``''``."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:DISPLAY_NAME_MAX]


def directory_name(value, employee_id: str) -> str:
    """A display name from the Directory, or ``''`` when it is only the employee ID.

    The Directory falls back to the ID when a person has no name, and that is
    no name at all.
    """
    display_name = clean_display_name(value)
    return "" if display_name == employee_id else display_name


def view(name: str) -> dict:
    """The roster view of Profile *name*: name, display name, label, status, last login."""
    record = _record(name)
    disabled = record is None or record.get("status") == STATUS_DISABLED
    record = record or {}
    display_name = clean_display_name(record.get("display_name"))
    last_login = record.get("last_login")
    return {
        "name": name,
        "display_name": display_name,
        "label": f"{display_name} ({name})" if display_name else name,
        "status": STATUS_DISABLED if disabled else STATUS_ACTIVE,
        "last_login": last_login if isinstance(last_login, (int, float)) else None,
    }


def label_rows(profile_rows: list) -> list:
    """Add the roster view to each named Profile row of the Profile list."""
    return [
        row if row.get("is_default") or not isinstance(row.get("name"), str)
        else {**row, **view(row["name"])}
        for row in profile_rows
    ]


def is_disabled(name: str) -> bool:
    """True when Profile *name* may not log in. Fails closed on an unreadable roster."""
    record = _record(name)
    return record is None or record.get("status") == STATUS_DISABLED


def _write(change) -> None:
    """Apply *change* to the records and save. Refuses to overwrite an unreadable roster."""
    with _LOCK:
        records = _load()
        change(records)
        _save(records)


def add(name: str, display_name="") -> None:
    """Start a fresh, active record for a newly created Profile."""
    def change(records):
        records[name] = {"display_name": clean_display_name(display_name), "status": STATUS_ACTIVE}
    _write(change)


def _set(name: str, **fields) -> None:
    _write(lambda records: records.setdefault(name, {}).update(fields))


def record_login(name: str, display_name="") -> None:
    """Record a login to Profile *name*, taking the display name from the Directory.

    A Directory name that is empty or just the employee ID (the Directory's
    fallback) does not replace the name the Admin typed.
    """
    fields = {"last_login": time.time()}
    display_name = directory_name(display_name, name)
    if display_name:
        fields["display_name"] = display_name
    _set(name, **fields)


def _check_existing_member_profile(name: str) -> None:
    """Refuse unless *name* is an existing Profile that is not the built-in one or an Admin's."""
    from api.access import is_admin
    from api.profiles import _validate_profile_name, named_profile_exists

    try:
        _validate_profile_name(name)
    except ValueError as exc:
        raise ProfileRefused(str(exc)) from exc
    if not named_profile_exists(name):
        raise ProfileRefused(f"Profile '{name}' does not exist.", REFUSED_NOT_FOUND)
    if is_admin(name):
        # An Admin logs in to `default`, so this Profile's status would not shut them out.
        raise ProfileRefused(f"{name} is an Admin; remove them from HERMES_WEBUI_ADMIN_USERS instead.")


def _set_status(name: str, status: str) -> None:
    """Record *status* for Profile *name*; refuse, with nothing changed, when the roster cannot be written."""
    try:
        _set(name, status=status)
    except (OSError, RosterUnreadable) as exc:
        logger.warning("Profile roster %s could not be written (%s)", _path(), exc)
        raise ProfileRefused(
            f"Profile '{name}' was not changed: the Profile roster could not be written.",
            REFUSED_SERVER_FAULT,
        ) from exc


def _end_sessions(name: str) -> None:
    from api.auth import invalidate_sessions_for_profile

    invalidate_sessions_for_profile(name)


def disable(name: str) -> None:
    """Mark Profile *name*'s record disabled and end its sessions now, with no guards."""
    _set(name, status=STATUS_DISABLED)
    _end_sessions(name)


def enable(name: str) -> None:
    """Mark Profile *name*'s record active, with no guards."""
    _set(name, status=STATUS_ACTIVE)


def disable_profile(name: str) -> dict:
    """The Admin disables Profile *name*: its sessions end now and its data stays."""
    _check_existing_member_profile(name)
    _set_status(name, STATUS_DISABLED)
    _end_sessions(name)
    return view(name)


def enable_profile(name: str) -> dict:
    """The Admin re-enables Profile *name*, so its User can log in again."""
    _check_existing_member_profile(name)
    _set_status(name, STATUS_ACTIVE)
    return view(name)


def remove(name: str) -> None:
    """End Profile *name*'s sessions and forget it (after it is deleted)."""
    _end_sessions(name)
    _write(lambda records: records.pop(name, None))
