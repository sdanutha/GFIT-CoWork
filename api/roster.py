"""GFIT-CoWork -- the Profile roster: who a Profile belongs to, and whether they may log in.

One record per Profile (keyed by the Profile name, which is the employee ID):
the display name, the status (``active`` or ``disabled``) and the last login
time. It lives in the GFIT-CoWork state directory, not in the Hermes Profile
config, so Hermes Agent never sees it.

A Profile with no record is active and shown by its ID alone, so Profiles made
before the roster existed (or outside GFIT-CoWork) keep working. An unreadable
roster fails closed: every Profile counts as disabled until it is fixed.

This module owns the Profile lifecycle: each action on a Profile is one
function here (``create_profile``, ``disable_profile``, ``enable_profile``,
``delete_profile``) that checks the action is allowed, keeps the Hermes Profile (``api.profiles``)
and its record in step, returns the Profile's roster view, and raises
ProfileRefused (a message for the Operator and its kind) when it refuses. The
steps run in an order where a failure part way leaves the Profile shut, never
open: create writes the record disabled before the Hermes Profile and makes it
active only once the Profile exists, and delete refuses a Profile that is not
already disabled. Disabling a
Profile pauses its scheduled jobs (re-enabling resumes the ones it paused) and
keeps its data; the running server notices the change and ends its logins and
running turns (``api.roster_watch``), which live only in the server process.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from api import config as _config


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
REFUSED_FORBIDDEN = "forbidden"
REFUSED_NOT_FOUND = "not_found"
REFUSED_CONFLICT = "conflict"
REFUSED_SERVER_FAULT = "server_fault"


class ProfileRefused(Exception):
    """An Operator action on a Profile was refused: ``str()`` is the message for the Operator."""

    def __init__(self, message: str, kind: str = REFUSED_BAD_REQUEST):
        super().__init__(message)
        self.kind = kind


def _path() -> Path:
    return Path(_config.STATE_DIR) / ROSTER_FILENAME


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


def version() -> tuple | None:
    """A token that changes whenever the roster file changes (None when there is no file)."""
    try:
        st = _path().stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def disabled_names() -> list[str] | None:
    """The Profiles the roster marks disabled, or None when the roster is unreadable."""
    with _LOCK:
        try:
            records = _load()
        except RosterUnreadable:
            return None
    return sorted(name for name, record in records.items() if record.get("status") == STATUS_DISABLED)


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
    fallback) does not replace the name the Operator gave.
    """
    fields = {"last_login": time.time()}
    display_name = directory_name(display_name, name)
    if display_name:
        fields["display_name"] = display_name
    _set(name, **fields)


def _check_existing_user_profile(name: str) -> None:
    """Refuse unless *name* is an existing Profile that is not the built-in one."""
    from api.profiles import named_profile_exists

    _check_name(name)
    if not named_profile_exists(name):
        raise ProfileRefused(f"Profile '{name}' does not exist.", REFUSED_NOT_FOUND)


def _refusal_from_hermes(exc: Exception, message: str, busy_kind: str) -> ProfileRefused:
    """A ProfileRefused for a failure of the Hermes Profile layer, with its reason.

    *busy_kind* is the kind for a RuntimeError (for example, an agent is running).
    """
    if isinstance(exc, PermissionError):
        kind = REFUSED_FORBIDDEN
    elif isinstance(exc, RuntimeError):
        kind = busy_kind
    elif isinstance(exc, (ValueError, FileExistsError, FileNotFoundError)):
        kind = REFUSED_BAD_REQUEST
    else:
        kind = REFUSED_SERVER_FAULT
    return ProfileRefused(f"{message}: {exc}", kind)


def _refuse_isolated_mode(action: str) -> None:
    """Refuse before anything changes when Profiles cannot be created or deleted here."""
    from api.profiles import _is_isolated_profile_mode

    if _is_isolated_profile_mode():
        raise ProfileRefused(f"Profile {action} is not allowed in isolated profile mode.", REFUSED_FORBIDDEN)


def _check_name(name: str, field: str = "profile name") -> None:
    """Refuse a name that breaks the Profile-name rule (``default`` included).

    The rule is the Hermes Profile layer's; the message is in the Operator's terms.
    """
    from api.profiles import _validate_profile_name

    try:
        _validate_profile_name(name)
    except ValueError as exc:
        if name == "default":
            raise ProfileRefused(str(exc)) from exc
        raise ProfileRefused(
            f"Invalid {field}: name it after the employee ID "
            "(lowercase letters, numbers, hyphens, underscores; up to 64 characters)",
        ) from exc


def create_profile(name: str, display_name: str = "", **hermes_options) -> dict:
    """The Operator creates Profile *name*, active, with *display_name*.

    The record is written disabled before the Hermes Profile is created and
    made active only once it exists, so a failure at any step leaves the
    Profile shut, never without a record (which would count as active).
    *hermes_options* go to ``api.profiles.create_profile_api`` (clone and model
    options). Returns the new Profile's row for the Profile list.
    """
    from api import profiles

    _refuse_isolated_mode("creation")
    _check_name(name)
    # create_profile_api checks clone_from too, but only after the record is written.
    clone_from = hermes_options.get("clone_from")
    if clone_from is not None and not profiles._is_root_profile(clone_from):
        _check_name(clone_from, "clone_from name")
    if profiles.named_profile_exists(name):
        raise ProfileRefused(f"Profile '{name}' already exists.")
    try:
        _write(lambda records: records.__setitem__(
            name, {"display_name": clean_display_name(display_name), "status": STATUS_DISABLED}))
    except (OSError, RosterUnreadable) as exc:
        logger.warning("Profile roster %s could not be written (%s)", _path(), exc)
        raise ProfileRefused(
            f"Profile '{name}' was not created: the Profile roster could not be written.",
            REFUSED_SERVER_FAULT,
        ) from exc
    try:
        result = profiles.create_profile_api(name, **hermes_options)
    except Exception as exc:
        if profiles.named_profile_exists(name) and not isinstance(exc, FileExistsError):
            # Made in part: its record keeps it shut until the Operator deletes it.
            profiles._invalidate_list_profiles_cache()
            raise _refusal_from_hermes(
                exc,
                f"Profile '{name}' was created only in part and is disabled; delete it and create it again",
                REFUSED_BAD_REQUEST,
            ) from exc
        _drop_record(name)
        raise _refusal_from_hermes(exc, f"Profile '{name}' was not created", REFUSED_BAD_REQUEST) from exc
    try:
        _set(name, status=STATUS_ACTIVE)
    except (OSError, RosterUnreadable) as exc:
        logger.warning("Profile roster %s could not be written (%s)", _path(), exc)
        raise ProfileRefused(
            f"Profile '{name}' was created but is disabled: the Profile roster could not be written. "
            "Enable it once the roster is fixed.",
            REFUSED_SERVER_FAULT,
        ) from exc
    return {**result, **view(name)}


def _drop_record(name: str) -> None:
    """Forget Profile *name*'s record. A leftover is harmless: Admission needs the Profile to exist."""
    try:
        _write(lambda records: records.pop(name, None))
    except (OSError, RosterUnreadable):
        logger.warning("The roster record of Profile %s could not be removed", name, exc_info=True)


def _set_status(name: str, status: str) -> None:
    """Record *status* for Profile *name*; refuse when the roster cannot be written."""
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


# The reason a disable gives the jobs it pauses; enable resumes only jobs still
# paused for it, so a job someone paused for their own reason stays paused.
PAUSED_BY_DISABLE = "Profile disabled"


@contextlib.contextmanager
def _profile_cron_jobs(name: str):
    """The Agent's ``cron.jobs``, working in Profile *name*'s own cron store."""
    from api.profiles import cron_profile_context_for_home, get_hermes_home_for_profile
    from api.routes import _ensure_agent_cron_import_path

    _ensure_agent_cron_import_path()
    jobs = importlib.import_module("cron.jobs")
    with cron_profile_context_for_home(get_hermes_home_for_profile(name)):
        yield jobs


def _pause_jobs(name: str) -> None:
    """Pause Profile *name*'s enabled scheduled jobs, recording which ones."""
    try:
        with _profile_cron_jobs(name) as jobs:
            ids = [
                job["id"] for job in jobs.list_jobs(include_disabled=True)
                if isinstance(job, dict) and job.get("id") and job.get("enabled", True)
            ]
            if not ids:
                return
            # Recorded before pausing, so a failure part way still resumes what was paused.
            _set(name, paused_jobs=sorted(set(_paused_jobs(name)) | set(ids)))
            for job_id in ids:
                jobs.pause_job(job_id, reason=PAUSED_BY_DISABLE)
    except Exception:
        logger.warning("The scheduled jobs of Profile %s could not all be paused", name, exc_info=True)


def _paused_jobs(name: str) -> list:
    record = _record(name) or {}
    paused = record.get("paused_jobs")
    return [job_id for job_id in paused if isinstance(job_id, str)] if isinstance(paused, list) else []


def _resume_jobs(name: str) -> None:
    """Resume the jobs Profile *name*'s disable paused that are still paused for it."""
    ids = _paused_jobs(name)
    if not ids:
        return
    try:
        with _profile_cron_jobs(name) as jobs:
            for job in jobs.list_jobs(include_disabled=True):
                if (
                    isinstance(job, dict) and job.get("id") in ids
                    and not job.get("enabled", True)
                    and job.get("paused_reason") == PAUSED_BY_DISABLE
                ):
                    jobs.resume_job(job["id"])
        _set(name, paused_jobs=[])
    except Exception:
        logger.warning("The scheduled jobs of Profile %s could not all be resumed", name, exc_info=True)


def _cancel_runs(name: str) -> None:
    """Stop Profile *name*'s running turns through the Stop path."""
    from api import run_registry
    from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK
    from api.session_ownership import UserSessionOwnership
    from api.streaming import cancel_stream

    stream_ids = set(run_registry.live_stream_ids())
    with ACTIVE_RUNS_LOCK:
        stream_ids |= set(ACTIVE_RUNS)
    owner = UserSessionOwnership(name)
    for stream_id in sorted(stream_ids):
        try:
            if owner.refuse_stream(stream_id) is None:
                cancel_stream(stream_id)
        except Exception:
            logger.warning("Run %s of Profile %s could not be stopped", stream_id, name, exc_info=True)


def stop_live_work(name: str) -> None:
    """End Profile *name*'s logins and stop its running turns.

    Both live only in the server process, so only the server calls this
    (``api.roster_watch``), never the command line. A failure is logged and
    never undoes the disable.
    """
    try:
        _end_sessions(name)
    except Exception:
        logger.warning("The logins of Profile %s could not all be ended", name, exc_info=True)
    _cancel_runs(name)


def disable(name: str) -> None:
    """Mark Profile *name*'s record disabled and pause its jobs, with no guards."""
    _set(name, status=STATUS_DISABLED)
    _pause_jobs(name)


def enable(name: str) -> None:
    """Mark Profile *name*'s record active and resume its paused jobs, with no guards."""
    _set(name, status=STATUS_ACTIVE)
    _resume_jobs(name)


def disable_profile(name: str) -> dict:
    """Disable Profile *name*: its scheduled jobs pause now and its data stays.

    The running server notices the roster change and ends the Profile's logins
    and running turns (``api.roster_watch``).
    """
    _check_existing_user_profile(name)
    _set_status(name, STATUS_DISABLED)
    _pause_jobs(name)
    return view(name)


def enable_profile(name: str) -> dict:
    """The Operator re-enables Profile *name*, so its User can log in again and the jobs
    the disable paused run again."""
    _check_existing_user_profile(name)
    _set_status(name, STATUS_ACTIVE)
    _resume_jobs(name)
    return view(name)


def delete_profile(name: str) -> dict:
    """Delete Profile *name*, which must already be disabled, then forget its record.

    Disabling first gives the running server time to end the Profile's logins
    and stop its turns before its files go. Returns ``{'ok': True, 'name': name}``.
    """
    from api import profiles

    _refuse_isolated_mode("deletion")
    if name == "default":
        raise ProfileRefused("The built-in default Profile cannot be deleted.")
    _check_name(name)
    if not profiles.named_profile_exists(name):
        raise ProfileRefused(f"Profile '{name}' does not exist.")
    record = _record(name)
    if record is None:
        # Unknown is not disabled: an unreadable roster cannot show the Profile is shut.
        raise ProfileRefused(
            f"Profile '{name}' was not deleted: the Profile roster could not be read.", REFUSED_SERVER_FAULT)
    if record.get("status") != STATUS_DISABLED:
        raise ProfileRefused(f"Profile '{name}' is active: disable it first, then delete it.", REFUSED_CONFLICT)
    try:
        result = profiles.delete_profile_api(name)
    except Exception as exc:
        raise _refusal_from_hermes(
            exc, f"Profile '{name}' is now disabled but was not deleted", REFUSED_CONFLICT,
        ) from exc
    _drop_record(name)
    return result
