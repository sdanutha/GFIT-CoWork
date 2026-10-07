"""Read-only routing hints for externally compressed GFIT-CoWork sessions.

SQLite owns compression lineage; sidecar snapshot flags may predate a
Desktop/CLI rotation. Never reopen a sealed parent or mutate Agent state here.
"""
import inspect
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class LineageUnreadable(Exception):
    """The Agent's lineage read API is present, but reading the session's lineage failed.

    Whether the parent is sealed is not established by that read. A read-only
    caller may keep legacy behavior; a caller about to write uses
    :func:`compression_state_for_send`, which refuses when no read can tell.
    *state_db* is the database the read was for, when it was resolved.
    """

    def __init__(self, sid, state_db=None):
        super().__init__(sid)
        self.sid = sid
        self.state_db = state_db


def _is_local_interactive_persisted_source(source):
    return isinstance(source, str) and source in {
        "webui", "tui", "cli", "desktop", "acp",
    }


def _opens_read_only(session_db_cls, path) -> bool:
    """False if the Agent's SessionDB has no read-only open; raises when its signature cannot be read."""
    try:
        inspect.signature(session_db_cls).bind(path, read_only=True)
    except TypeError:
        return False
    return True


def _has_lookups(db, sid) -> bool:
    """False if the Agent lacks a lookup the lineage read needs; raises when a signature cannot be read."""
    for name in ('get_session', 'get_compression_tip'):
        method = getattr(db, name, None)
        if not callable(method):
            return False
        try:
            inspect.signature(method).bind(sid)
        except TypeError:
            return False
    return True


def _session_db_class():
    """The Agent's SessionDB, or None when the Agent has no hermes_state module."""
    try:
        from hermes_state import SessionDB
    except ModuleNotFoundError as exc:
        # Only hermes_state itself not being there is an absent capability; a
        # module it imports failing, or a hermes_state without SessionDB, is a
        # broken read.
        if exc.name == "hermes_state":
            return None
        raise
    return SessionDB


def _compression_seal(end_reason) -> bool:
    """True when *end_reason* is Hermes's compression seal; raises on a value that is neither NULL nor text."""
    if end_reason is not None and not isinstance(end_reason, str):
        raise TypeError(f"end_reason is {type(end_reason).__name__}, not text")
    return end_reason == "compression"


def _resumable_tip(db, sid, profile, parent):
    """The continuation a sealed parent may be resumed in, or None."""
    tip = db.get_compression_tip(sid)
    if not tip or tip == sid:
        return None
    child = db.get_session(tip)
    if not child or not _is_local_interactive_persisted_source(child.get("source")):
        return None
    for row in (parent, child):
        if row.get("profile_name") not in (None, "", profile):
            return None
    # Only the observed automatic idle closure is resumable here. Explicit
    # resets/closures and unknown future reasons must not become redirects.
    reason = child.get("end_reason")
    if reason not in (None, "", "idle_timeout"):
        return None
    if child.get("ended_at") is not None and reason != "idle_timeout":
        return None
    if db.get_compression_tip(sid) != tip:
        return None
    return tip


def durable_compression_continuation(session):
    """Return (sealed, resumable tip), without making a recovery write.

    A known sealed parent without a safe tip stays sealed (no sidecar fallback).
    No state.db, and an Agent without this read API (no hermes_state, no
    read-only open, no lookup methods), retain legacy behavior. Any other
    failure before the parent is known to be sealed (an ImportError inside
    hermes_state, a SQLite or OS error, a malformed row) raises
    :class:`LineageUnreadable`; the send path logs it.
    """
    from api.profiles import _PROFILE_ID_RE, _resolve_profile_home_for_name

    sid = str(getattr(session, "session_id", "") or "")
    profile = str(getattr(session, "profile", None) or "default")
    if not sid or (profile != "default" and not _PROFILE_ID_RE.fullmatch(profile)):
        return False, None
    db = None
    sealed = False
    path = None
    try:
        path = Path(_resolve_profile_home_for_name(profile)) / "state.db"
        if not path.is_file():
            return False, None
        # Establish the complete read API before accepting SQLite authority.
        # Old Agents must retain legacy sidecar recovery, not a sealed null tip.
        session_db_cls = _session_db_class()
        if session_db_cls is None or not _opens_read_only(session_db_cls, path):
            return False, None
        db = session_db_cls(path, read_only=True)
        if not _has_lookups(db, sid):
            return False, None
        parent = db.get_session(sid)
        if parent is None:
            return False, None
        if not isinstance(parent, dict) or "end_reason" not in parent:
            raise TypeError("get_session returned a malformed row")
        if not _compression_seal(parent["end_reason"]):
            return False, None
        sealed = True
        return True, _resumable_tip(db, sid, profile, parent)
    except Exception as exc:
        if sealed:
            # Known sealed: it stays sealed, with no continuation to offer.
            logger.debug("Could not resolve durable compression continuation", exc_info=True)
            return True, None
        logger.debug("Could not read the compression lineage of session %s", sid, exc_info=True)
        raise LineageUnreadable(sid, path) from exc
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.debug("Could not close the lineage SessionDB", exc_info=True)


# The one place the WebUI reads Hermes Agent's session schema itself: an
# intentional, minimal compatibility boundary (sessions.id, sessions.end_reason).
# The Agent's read-only open does no schema migration, so a state.db written by
# an older Agent fails its newer reads until a writer migrates it; the seal bit
# is still readable. Hermes's own predicate also requires ended_at, so reading
# end_reason alone can only refuse more, never less.
_SEAL_PROBE_SQL = "SELECT end_reason FROM sessions WHERE id = ?"
_SEAL_PROBE_TIMEOUT_S = 2.0


def _sealed_by_compression(state_db: Path, sid: str) -> bool:
    """True if *sid*'s row records the compression seal; False if it does not, or
    there is no row. Raises when the database cannot tell.

    Opens the file read-only through SQLite (``mode=ro``, as the Agent's own
    read-only open does): never creates the database, never migrates or writes.
    """
    conn = sqlite3.connect(
        f"{Path(state_db).resolve().as_uri()}?mode=ro",
        uri=True, timeout=_SEAL_PROBE_TIMEOUT_S, isolation_level=None,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(_SEAL_PROBE_SQL, (sid,)).fetchall()
    finally:
        conn.close()
    if len(rows) > 1:
        raise ValueError(f"{len(rows)} sessions rows for one id")
    return bool(rows) and _compression_seal(rows[0][0])


def compression_state_for_send(session):
    """(sealed, continuation) before a send writes to *session*.

    The Agent's lineage read decides when it works or when the Agent lacks it
    (legacy). When it fails, the seal bit is read directly: sealed refuses with
    no continuation; unsealed or no row lets the send through. When neither
    read can tell, raises :class:`LineageUnreadable`: the send is refused.
    """
    try:
        return durable_compression_continuation(session)
    except LineageUnreadable as exc:
        logger.warning(
            "Could not read the compression lineage of session %s from %s: %s",
            exc.sid, exc.state_db, exc.__cause__, exc_info=exc.__cause__,
        )
        if exc.state_db is None:
            raise
        try:
            sealed = _sealed_by_compression(exc.state_db, exc.sid)
        except Exception as probe_exc:
            logger.warning(
                "Could not read the seal state of session %s from %s either: %s. The send is refused.",
                exc.sid, exc.state_db, probe_exc, exc_info=True,
            )
            raise LineageUnreadable(exc.sid, exc.state_db) from probe_exc
        return sealed, None
