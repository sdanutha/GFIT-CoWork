"""GFIT-CoWork -- the Profile roster: who a Profile belongs to, and whether they may log in.

One record per Profile (keyed by the Profile name, which is the employee ID):
the display name, the status (``active`` or ``disabled``) and the last login
time. It lives in the GFIT-CoWork state directory, not in the Hermes Profile
config, so Hermes Agent never sees it.

A Profile with no record is active and shown by its ID alone, so Profiles made
before the roster existed (or outside GFIT-CoWork) keep working. An unreadable
roster fails closed: every Profile counts as disabled until it is fixed.

Disabling a Profile ends its sessions straight away; its data stays. The Hermes
Profile itself is created and deleted through ``api.profiles``; this module
only keeps the roster in step.
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


def record_login(name: str) -> None:
    _set(name, last_login=time.time())


def disable(name: str) -> None:
    """Disable Profile *name* and end its sessions now. Its data stays."""
    _set(name, status=STATUS_DISABLED)
    from api.auth import invalidate_sessions_for_profile

    invalidate_sessions_for_profile(name)


def enable(name: str) -> None:
    _set(name, status=STATUS_ACTIVE)


def remove(name: str) -> None:
    """End Profile *name*'s sessions and forget it (after it is deleted)."""
    from api.auth import invalidate_sessions_for_profile

    invalidate_sessions_for_profile(name)
    _write(lambda records: records.pop(name, None))
