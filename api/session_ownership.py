"""GFIT-CoWork -- one session ownership module answers "whose session is this?" for a request.

Each request has one session ownership adapter, chosen from the request's
Admission (:func:`request_session_ownership`), the same way as the Workspace
policy:

- the **User's adapter** (:class:`UserSessionOwnership`): owns exactly the
  sessions of the User's Profile (ADR 0002). Every other id, and every id it
  cannot place, is refused: unknown is not allowed;
- the **unconfined adapter** (:data:`UNCONFINED`): code with no caller, which
  never answers HTTP (worker threads, startup, the session-list cache
  builder). Upstream's rules: a session of another, known Profile is refused,
  and an id it cannot find passes;
- the **refusing answer** (:data:`REFUSING`): an HTTP request with no
  Admission (a public route, a session not admitted), or an Admission this
  module does not understand. It owns nothing.

A User's request is **Bound** to their Profile: besides owning only that
Profile's sessions, it may name no other Profile (:meth:`may_name_profile`).

A route loads the session its request names through :func:`load_owned_session`:
it asks session ownership first and writes the refusal (or the 404) itself.

Callers ask: is this session id mine (:meth:`refuse_session`; for a session
the route has already found, a record or a listed row,
:meth:`refuse_found_session`; for one the detail load knows only from its
listed row, :meth:`refuse_listed_session`), is this stream
id mine (:meth:`refuse_stream`), may this session-list event go to me
(:meth:`may_receive_event`), may this listed row go to me
(:meth:`may_list_row`), and which Profiles may this request read: in a view,
which follows Upstream's isolated profile mode (:meth:`profile_reach`), and
at all, which a caller alone confines (:meth:`caller_reach`). Both answer a
:class:`ProfileReach`. A refusal (:class:`Refusal`) writes its own answer:
404 "Session not found". It never names the owning Profile (ticket 07):
another Profile's session and a session that does not exist get exactly the
same answer.

The User's adapter looks in the WebUI session record, then in the Profile's own
agent state (``state.db``: CLI, messaging, cron and gateway sessions). It never
looks in another Profile's state or in the server account's home. Only the
unconfined adapter lets a Profile-less row (Claude Code) through, and
only on the detail load. Only the unconfined adapter keeps Upstream's own route
rules on top (:meth:`keeps_upstream_rules`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from api.helpers import bad

logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "Session not found"


@dataclass(frozen=True)
class Refusal:
    """A session the caller does not own. It carries nothing: its answer never says whose it is."""

    def answer(self, handler, session_id=None, *, not_found: str = NOT_FOUND_MESSAGE) -> bool:
        """Write this refusal's answer: 404 with *not_found*, whoever owns the session."""
        return bad(handler, not_found, 404)


NOT_FOUND = Refusal()


@dataclass(frozen=True)
class ProfileReach:
    """A request's Profile reach: the Profiles it may read (``CONTEXT.md``).

    The answer to :meth:`profile_reach` (a view) and :meth:`caller_reach` (the
    caller). *every_profile* is true when every Profile may be read; otherwise
    *profiles* names the ones that may. There is no all-Profiles view (ADR
    0006): a view is always one Profile's.
    """

    every_profile: bool
    profiles: frozenset = frozenset()

    def __post_init__(self):
        if self.every_profile and self.profiles:
            raise ValueError("a reach of every Profile names no Profiles")

    def includes(self, profile) -> bool:
        """May the request read data of *profile*? A missing Profile is the root Profile's."""
        from api.profiles import _profiles_match

        if self.every_profile:
            return True
        return any(_profiles_match(profile, readable) for readable in self.profiles)


EVERY_PROFILE = ProfileReach(every_profile=True)
NO_PROFILE = ProfileReach(every_profile=False)


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


def _field(found, name):
    """A field of a found session: a listed row (dict) or a session record."""
    if isinstance(found, dict):
        return found.get(name)
    return getattr(found, name, None)


def _profile_of(found) -> str | None:
    """The Profile a found session names, or None (missing, legacy, or not a name)."""
    profile = _field(found, "profile") if found is not None else None
    return profile if isinstance(profile, str) and profile else None


def _is_profile_less_row(row) -> bool:
    """A row scanned from outside every Profile (Claude Code): it carries no Profile."""
    from api.models import CLAUDE_CODE_SOURCE

    if row is None or _field(row, "profile"):
        return False
    sources = {
        str(_field(row, "source_tag") or "").strip().lower(),
        str(_field(row, "raw_source") or "").strip().lower(),
    }
    return CLAUDE_CODE_SOURCE in sources


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

    def may_name_profile(self, name) -> bool:
        """Bound: the User's request may name only the User's own Profile."""
        from api.profiles import _profiles_match

        return isinstance(name, str) and _profiles_match(name, self.profile)

    def sees_profile_less_sessions(self) -> bool:
        """Never: Claude Code rows come from the server account's home."""
        return False

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

    def may_list_row(self, row, *, active_profile=None) -> bool:
        """A row of the User's Profile; never a Profile-less one.

        *active_profile* is an unconfined view's; the User's own Profile wins.
        """
        from api.profiles import _profiles_match

        if not isinstance(row, dict) or _is_profile_less_row(row):
            return False
        profile = row.get("profile")
        return isinstance(profile, str) and bool(profile) and _profiles_match(profile, self.profile)

    def keeps_upstream_rules(self) -> bool:
        """No: a User's request is answered by this adapter alone, never by a route's own rules."""
        return False

    def profile_reach(self, active_profile=None) -> ProfileReach:
        """Exactly the User's own Profile, whatever was asked."""
        return self.caller_reach()

    def caller_reach(self) -> ProfileReach:
        """The User's own Profile."""
        return ProfileReach(every_profile=False, profiles=frozenset({self.profile}))

    def refuse_found_session(self, session_id, found) -> Refusal | None:
        """A session the route has already found (a record, or a listed row): the User's own, else 404."""
        from api.profiles import _profiles_match

        profile = _profile_of(found)
        if profile and _profiles_match(profile, self.profile):
            return None
        return NOT_FOUND

    def refuse_listed_session(self, session_id, row) -> Refusal | None:
        """A session known only from its listed row: as any found session (never a Profile-less one)."""
        return self.refuse_found_session(session_id, row)


class _UnconfinedSessionOwnership:
    """No caller's: today's rules against the request's active Profile."""

    def may_name_profile(self, name) -> bool:
        return True

    def sees_profile_less_sessions(self) -> bool:
        """Claude Code rows, under the setting that shows them."""
        return True

    def keeps_upstream_rules(self) -> bool:
        """Yes: routes keep Upstream's own rules (the detail-load and import exemptions,
        chat start's placeholder retag) on top of this adapter's answers."""
        return True

    def profile_reach(self, active_profile=None) -> ProfileReach:
        """The active Profile (a view is always one Profile's)."""
        from api.profiles import get_active_profile_name

        return ProfileReach(every_profile=False, profiles=frozenset({active_profile or get_active_profile_name()}))

    def caller_reach(self) -> ProfileReach:
        """Every Profile: no caller confines this request, whatever Upstream's posture.

        For reads of stores shared by every Profile (the session index, the
        running cron jobs, a Profile-home lookup), which Upstream never scoped.
        """
        return EVERY_PROFILE

    def refuse_session(self, session_id) -> Refusal | None:
        """Another known Profile's session is refused; an id it cannot find passes."""
        from api.models import get_session, is_safe_session_id

        if not isinstance(session_id, str) or not session_id or not is_safe_session_id(session_id):
            return None
        try:
            session = get_session(session_id, metadata_only=True)
        except KeyError:
            return None
        return self.refuse_found_session(session_id, session)

    def refuse_stream(self, stream_id) -> Refusal | None:
        owner = stream_owner(stream_id)
        return None if owner is None else self.refuse_session(owner)

    def may_receive_event(self, event) -> bool:
        return True

    def may_list_row(self, row, *, active_profile=None) -> bool:
        """The active Profile's rows. A row with no Profile counts as the root Profile's."""
        from api.profiles import _profiles_match, get_active_profile_name

        profile = row.get("profile") if isinstance(row, dict) else None
        return _profiles_match(profile, active_profile or get_active_profile_name())

    def refuse_found_session(self, session_id, found) -> Refusal | None:
        """A session the route has already found: a session record, or a listed row.

        The active Profile's session passes; any other is refused, without
        saying whose it is.
        """
        from api.profiles import _profiles_match, get_active_profile_name

        profile = _profile_of(found)
        if _profiles_match(profile, get_active_profile_name()):
            return None
        return NOT_FOUND

    def refuse_listed_session(self, session_id, row) -> Refusal | None:
        """A session known only from its listed row, opened by the detail load.

        A Profile-less row (Claude Code) belongs to no Profile and opens
        under any; otherwise as any found session.
        """
        if _is_profile_less_row(row):
            return None
        return self.refuse_found_session(session_id, row)


class _RefusingSessionOwnership:
    """The refusing answer: owns nothing."""

    def may_name_profile(self, name) -> bool:
        return False

    def sees_profile_less_sessions(self) -> bool:
        return False

    def refuse_session(self, session_id) -> Refusal:
        return NOT_FOUND

    def refuse_stream(self, stream_id) -> Refusal:
        return NOT_FOUND

    def may_receive_event(self, event) -> bool:
        return False

    def may_list_row(self, row, **_kwargs) -> bool:
        return False

    def keeps_upstream_rules(self) -> bool:
        return False

    def profile_reach(self, active_profile=None) -> ProfileReach:
        return NO_PROFILE

    def caller_reach(self) -> ProfileReach:
        return NO_PROFILE

    def refuse_found_session(self, session_id, found) -> Refusal:
        return NOT_FOUND

    def refuse_listed_session(self, session_id, row) -> Refusal:
        return NOT_FOUND


UNCONFINED = _UnconfinedSessionOwnership()
REFUSING = _RefusingSessionOwnership()


def ownership_for(admission, *, directory_session: bool, serving: bool = False):
    """The session ownership adapter for *admission*: the one mapping from Admission to adapter.

    A User's Admission gives that User's adapter. No Admission is unconfined
    only for code with no caller (a worker thread, startup): an HTTP request
    being *served* or a Directory session with none is refused, as is a role
    or Profile this module does not understand.
    """
    from api.access import ROLE_USER
    from api.profiles import _resolve_named_profile_home

    if admission is None:
        # Unknown is not allowed: a request (served, or a Directory session)
        # with no Admission is refused; only code with no caller is unconfined.
        return REFUSING if directory_session or serving else UNCONFINED
    if admission.role == ROLE_USER and admission.profile:
        try:
            _resolve_named_profile_home(admission.profile)
        except ValueError:
            return REFUSING
        return UserSessionOwnership(admission.profile)
    return REFUSING


def request_session_ownership():
    """This request's session ownership adapter, from the request's Admission."""
    from api.access import request_admission, request_has_directory_session, request_is_served

    return ownership_for(
        request_admission(), directory_session=request_has_directory_session(), serving=request_is_served(),
    )


def request_profile_reach(active_profile=None) -> ProfileReach:
    """This request's Profile reach in a view: one Profile, the caller's."""
    return request_session_ownership().profile_reach(active_profile)


def request_caller_reach() -> ProfileReach:
    """The Profiles this request's caller may read at all, whatever the view."""
    return request_session_ownership().caller_reach()


def load_owned_session(
    handler, session_id, *, load, not_found: str = NOT_FOUND_MESSAGE, **load_options
):
    """The session the request names, when the request owns it; else None, its answer written.

    Session ownership answers first (as a read or a write, by the request's
    route): a refusal writes 404 *not_found*, never naming the owner. Then
    *load* (the caller's session loader) loads it with *load_options*; a load
    that raises ``KeyError`` writes 404 *not_found*. Other errors pass to the
    caller. For a session id the request chose, never one the server chose.
    """
    refusal = request_session_ownership().refuse_session(session_id)
    if refusal is not None:
        refusal.answer(handler, session_id, not_found=not_found)
        return None
    try:
        return load(session_id, **load_options)
    except KeyError:
        bad(handler, not_found, 404)
        return None
