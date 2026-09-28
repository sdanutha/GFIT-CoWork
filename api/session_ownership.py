"""GFIT-CoWork -- one session ownership module answers "whose session is this?" for a request.

Each request has one session ownership adapter, chosen from the request's
Admission (:func:`request_session_ownership`), the same way as the Workspace
policy:

- the **User's adapter** (:class:`UserSessionOwnership`): owns exactly the
  sessions of the User's Profile (ADR 0002). Every other id, and every id it
  cannot place, is refused: unknown is not allowed;
- the **unconfined adapter** (:data:`UNCONFINED`): the Admin, and requests with
  no Admission (login turned off, worker threads). Today's rules: a session of
  another, known Profile is refused with that Profile named (the 409 the client
  uses to offer a switch), and an id it cannot find passes to the route;
- the **refusing answer** (:data:`REFUSING`): a Directory session with no
  recorded Admission, or an Admission this module does not understand. It owns
  nothing.

Callers ask: is this session id mine (:meth:`refuse_session`; for a session
known only from its listed row, :meth:`refuse_listed_session`), is this stream
id mine (:meth:`refuse_stream`), may this session-list event go to me
(:meth:`may_receive_event`) and may this listed row go to me
(:meth:`may_list_row`). A refusal (:class:`Refusal`) writes its own answer:
404 "Session not found", or 409 naming the owning Profile, which only the
unconfined adapter gives. For a User, another Profile's session and a session
that does not exist get exactly the same answer.

The User's adapter looks in the WebUI session record, then in the Profile's own
agent state (``state.db``: CLI, messaging, cron and gateway sessions). It never
looks in another Profile's state or in the server account's home. Only the
unconfined adapter lets a Profile-less row (Claude Code, Codex) through.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from api.helpers import bad, j

logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "Session not found"


@dataclass(frozen=True)
class Refusal:
    """A session the caller does not own.

    *owner* names the owning Profile; only the unconfined adapter sets it, and
    then the answer is the 409 the client uses to offer a switch.
    """

    owner: str | None = None

    def answer(self, handler, session_id) -> bool:
        """Write this refusal's answer for *session_id*: 409 with the owner, else 404."""
        if self.owner:
            return j(handler, {
                "error": "Session belongs to a different profile",
                "code": "session_profile_mismatch",
                "session_id": session_id,
                "profile": self.owner,
            }, status=409)
        return self.answer_not_found(handler)

    def answer_not_found(self, handler) -> bool:
        """Write 404 "Session not found", for a route whose answer never names an owner."""
        return bad(handler, NOT_FOUND_MESSAGE, 404)


NOT_FOUND = Refusal()


def _names_nothing(session_id) -> bool:
    return session_id is None or session_id == ""


def stream_owner(stream_id) -> str | None:
    """The session that owns *stream_id*: the active-run registry, then the owner registry, then the run journal."""
    from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK, stream_owner_session_id
    from api.models import is_safe_session_id
    from api.run_journal import find_run_summary

    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return None
    try:
        with ACTIVE_RUNS_LOCK:
            raw = (ACTIVE_RUNS or {}).get(stream_id)
        if isinstance(raw, dict):
            owner = str(raw.get("session_id") or "").strip()
            if owner:
                return owner
    except Exception:
        logger.debug("Failed reading ACTIVE_RUNS owner for stream %s", stream_id, exc_info=True)
    try:
        owner = stream_owner_session_id(stream_id)
        if owner:
            return owner
    except Exception:
        logger.debug("Failed reading registered owner for stream %s", stream_id, exc_info=True)
    if not is_safe_session_id(stream_id):
        return None
    try:
        summary = find_run_summary(stream_id)
        if isinstance(summary, dict):
            owner = str(summary.get("session_id") or "").strip()
            return owner or None
    except Exception:
        logger.debug("Failed reading run summary for stream %s", stream_id, exc_info=True)
    return None


def _is_profile_less_row(row) -> bool:
    """A row scanned from outside every Profile (Claude Code, Codex): it carries no Profile."""
    from api.models import CLAUDE_CODE_SOURCE

    if not isinstance(row, dict) or row.get("profile"):
        return False
    sources = {
        str(row.get("source_tag") or "").strip().lower(),
        str(row.get("raw_source") or "").strip().lower(),
    }
    profile_less = {CLAUDE_CODE_SOURCE}
    try:
        from api.codex_sessions import CODEX_SOURCE

        profile_less.add(CODEX_SOURCE)
    except ImportError:
        pass
    return bool(sources & profile_less)


class UserSessionOwnership:
    """A User's adapter: the sessions of the User's Profile, and nothing else."""

    def __init__(self, profile: str):
        self.profile = profile

    def _owns(self, session_id) -> bool:
        from api.models import get_session, is_safe_session_id, state_db_has_session
        from api.profiles import _profiles_match

        if not isinstance(session_id, str) or not is_safe_session_id(session_id):
            return False
        try:
            session = get_session(session_id, metadata_only=True)
        except KeyError:
            return state_db_has_session(session_id, profile=self.profile)
        except Exception:
            return False
        session_profile = getattr(session, "profile", None)
        return isinstance(session_profile, str) and _profiles_match(session_profile, self.profile)

    def refuse_session(self, session_id) -> Refusal | None:
        """None when *session_id* names nothing or a session of the User's Profile, else 404."""
        if _names_nothing(session_id) or self._owns(session_id):
            return None
        return NOT_FOUND

    def refuse_stream(self, stream_id) -> Refusal | None:
        """None for a run of one of the User's sessions; an unknown stream is refused."""
        owner = stream_owner(stream_id)
        if owner is None:
            return NOT_FOUND
        return self.refuse_session(owner)

    def may_receive_event(self, event) -> bool:
        """An event that names no Profile and no session, or only the User's own."""
        from api.profiles import _profiles_match

        event = event if isinstance(event, dict) else {}
        profile = str(event.get("profile") or "").strip()
        session_id = str(event.get("session_id") or "").strip()
        if not profile and not session_id:
            return True
        if not profile or not _profiles_match(profile, self.profile):
            return False
        return not session_id or self._owns(session_id)

    def may_list_row(self, row, *, active_profile=None, all_profiles: bool = False) -> bool:
        """A row of the User's Profile; never a Profile-less one.

        *active_profile* and *all_profiles* are the Admin's view; the User's
        own Profile wins.
        """
        from api.profiles import _profiles_match

        if not isinstance(row, dict) or _is_profile_less_row(row):
            return False
        profile = row.get("profile")
        return isinstance(profile, str) and bool(profile) and _profiles_match(profile, self.profile)

    def refuse_listed_session(self, session_id, row) -> Refusal | None:
        """A session known only from its listed *row* (no WebUI record): the User's own, else 404."""
        return None if self.may_list_row(row) else NOT_FOUND


class _UnconfinedSessionOwnership:
    """The Admin's, and no caller's: today's rules against the request's active Profile."""

    def refuse_session(self, session_id) -> Refusal | None:
        """Another known Profile's session names its owner; an id it cannot find passes."""
        from api.models import get_session, is_safe_session_id
        from api.profiles import _profiles_match, get_active_profile_name

        if not isinstance(session_id, str) or not session_id or not is_safe_session_id(session_id):
            return None
        try:
            session = get_session(session_id, metadata_only=True)
        except KeyError:
            return None
        session_profile = getattr(session, "profile", None) or None
        if not isinstance(session_profile, str):
            session_profile = None
        if _profiles_match(session_profile, get_active_profile_name()):
            return None
        # A legacy session with no Profile keeps the plain 404, so the client's
        # self-heal for a missing session still fires.
        return Refusal(owner=session_profile)

    def refuse_stream(self, stream_id) -> Refusal | None:
        owner = stream_owner(stream_id)
        return None if owner is None else self.refuse_session(owner)

    def may_receive_event(self, event) -> bool:
        return True

    def may_list_row(self, row, *, active_profile=None, all_profiles: bool = False) -> bool:
        """Every Profile's rows in the all-Profiles view, else the active Profile's.

        A row with no Profile counts as the root Profile's.
        """
        from api.profiles import _profiles_match, get_active_profile_name

        if all_profiles:
            return True
        profile = row.get("profile") if isinstance(row, dict) else None
        return _profiles_match(profile, active_profile or get_active_profile_name())

    def refuse_listed_session(self, session_id, row) -> Refusal | None:
        """A session known only from its listed *row* (no WebUI record).

        A Profile-less row (Claude Code, Codex) belongs to no Profile and opens
        under any. Another known Profile's row names its owner; a missing or
        legacy row with no Profile is 404, so the client's self-heal fires.
        """
        from api.profiles import _profiles_match, get_active_profile_name

        row = row if isinstance(row, dict) else {}
        if _is_profile_less_row(row):
            return None
        profile = row.get("profile") or None
        if _profiles_match(profile, get_active_profile_name()):
            return None
        return Refusal(owner=profile if isinstance(profile, str) else None)


class _RefusingSessionOwnership:
    """The refusing answer: owns nothing."""

    def refuse_session(self, session_id) -> Refusal:
        return NOT_FOUND

    def refuse_stream(self, stream_id) -> Refusal:
        return NOT_FOUND

    def may_receive_event(self, event) -> bool:
        return False

    def may_list_row(self, row, **_kwargs) -> bool:
        return False

    def refuse_listed_session(self, session_id, row) -> Refusal:
        return NOT_FOUND


UNCONFINED = _UnconfinedSessionOwnership()
REFUSING = _RefusingSessionOwnership()


def ownership_for(admission, *, directory_session: bool):
    """The session ownership adapter for *admission*: the one mapping from Admission to adapter.

    A User's Admission gives that User's adapter, the Admin's gives the
    unconfined one. No Admission is unconfined only when there is no Directory
    session (login turned off, a worker thread); a Directory session with none
    is refused, as is a role or Profile this module does not understand.
    """
    from api.access import ROLE_ADMIN, ROLE_MEMBER
    from api.profiles import _resolve_named_profile_home

    if admission is None:
        return REFUSING if directory_session else UNCONFINED
    if admission.role == ROLE_ADMIN:
        return UNCONFINED
    if admission.role == ROLE_MEMBER and admission.profile:
        try:
            _resolve_named_profile_home(admission.profile)
        except ValueError:
            return REFUSING
        return UserSessionOwnership(admission.profile)
    return REFUSING


def request_session_ownership():
    """This request's session ownership adapter, from the request's Admission."""
    from api.access import request_admission, request_has_directory_session

    return ownership_for(request_admission(), directory_session=request_has_directory_session())
