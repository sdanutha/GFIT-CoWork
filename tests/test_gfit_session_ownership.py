"""GFIT-CoWork: one session ownership module answers "whose session is this?" for a request.

A User's adapter owns exactly the sessions of the User's Profile and refuses
every other id, including ids it cannot place. The unconfined adapter (the
Admin, and requests with no Admission) keeps today's rules, including the 409
that names the owning Profile. A Directory session with no recorded Admission
gets the refusing answer.

Tested from temporary Profile folders and in-memory session records alone: no
server and no request thread. Each case is a row in a table.
"""
from __future__ import annotations

import collections
import io
import json
import sqlite3

import pytest

import api.access as access
import api.models as models
import api.profiles as profiles
from api.access import ROLE_ADMIN, ROLE_MEMBER, Admitted
from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK
from api.session_ownership import (
    ADMIN,
    REFUSING,
    UNCONFINED,
    Refusal,
    UserSessionOwnership,
    ownership_for,
    request_session_ownership,
)

ALICE = "521740"
BOB = "671278"

OWNED = "owned"
NOT_FOUND = "404"
OWNER_BOB = "409 bob"
PASSES = "passes"  # an id the unconfined adapter cannot find is left to the route


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Alice's and Bob's Profiles, with WebUI sessions, state.db sessions and runs."""
    hermes = tmp_path / "hermes"
    for uid in (ALICE, BOB):
        home = hermes / "profiles" / uid
        home.mkdir(parents=True)
        conn = sqlite3.connect(home / "state.db")
        with conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
            conn.execute("INSERT INTO sessions VALUES (?, 'telegram')", (f"{uid}-gateway",))
        conn.close()
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes)
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()
    # The Admin's request runs in the root Profile.
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    sessions = collections.OrderedDict()
    monkeypatch.setattr(models, "SESSIONS", sessions)
    for sid, profile in (("alice-webui", ALICE), ("bob-webui", BOB), ("root-webui", "default")):
        sessions[sid] = models.Session(session_id=sid, profile=profile, workspace=str(tmp_path))

    runs = {"alice-run": "alice-webui", "bob-run": "bob-webui", "alice-gateway-run": f"{ALICE}-gateway"}
    with ACTIVE_RUNS_LOCK:
        for stream_id, sid in runs.items():
            ACTIVE_RUNS[stream_id] = {"session_id": sid}
    yield
    with ACTIVE_RUNS_LOCK:
        for stream_id in runs:
            ACTIVE_RUNS.pop(stream_id, None)
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()


def _outcome(refusal) -> str:
    if refusal is None:
        return OWNED
    assert isinstance(refusal, Refusal)
    return f"409 {'bob' if refusal.owner == BOB else refusal.owner}" if refusal.owner else NOT_FOUND


SESSION_IDS = {
    "own WebUI session": "alice-webui",
    "own gateway session": f"{ALICE}-gateway",
    "another Profile's WebUI session": "bob-webui",
    "another Profile's gateway session": f"{BOB}-gateway",
    "the root Profile's session": "root-webui",
    "Profile-less row": "claude_code_0123456789abcdef01234567",
    "missing id": "no-such-session",
    "malformed id": "../../etc/passwd",
    "a number": 42,
}

# case -> (User Alice, unconfined in the root Profile, refusing)
SESSION_TABLE = {
    "own WebUI session": (OWNED, "409 521740", NOT_FOUND),
    "own gateway session": (OWNED, OWNED, NOT_FOUND),
    "another Profile's WebUI session": (NOT_FOUND, OWNER_BOB, NOT_FOUND),
    "another Profile's gateway session": (NOT_FOUND, OWNED, NOT_FOUND),
    "the root Profile's session": (NOT_FOUND, OWNED, NOT_FOUND),
    "Profile-less row": (NOT_FOUND, OWNED, NOT_FOUND),
    "missing id": (NOT_FOUND, OWNED, NOT_FOUND),
    "malformed id": (NOT_FOUND, OWNED, NOT_FOUND),
    "a number": (NOT_FOUND, OWNED, NOT_FOUND),
}

ADAPTERS = {
    "user": lambda: UserSessionOwnership(ALICE),
    "unconfined": lambda: UNCONFINED,
    "refusing": lambda: REFUSING,
}


@pytest.mark.parametrize("case", sorted(SESSION_TABLE))
@pytest.mark.parametrize("adapter", list(ADAPTERS))
def test_is_this_session_id_mine(world, case, adapter):
    expected = dict(zip(ADAPTERS, SESSION_TABLE[case], strict=True))[adapter]
    outcome = _outcome(ADAPTERS[adapter]().refuse_session(SESSION_IDS[case]))
    # The unconfined adapter's "owned" for an id it cannot find means "left to the route".
    assert outcome == expected


@pytest.mark.parametrize("adapter", ["user", "unconfined"])
@pytest.mark.parametrize("session_id", [None, ""])
def test_naming_no_session_is_not_a_refusal(world, adapter, session_id):
    assert ADAPTERS[adapter]().refuse_session(session_id) is None


STREAM_TABLE = {
    "own run": ("alice-run", OWNED, "409 521740"),
    "own gateway session's run": ("alice-gateway-run", OWNED, OWNED),
    "another Profile's run": ("bob-run", NOT_FOUND, OWNER_BOB),
    "unknown stream": ("no-such-run", NOT_FOUND, OWNED),
    "no stream": ("", NOT_FOUND, OWNED),
}


@pytest.mark.parametrize("case", sorted(STREAM_TABLE))
def test_is_this_stream_id_mine(world, case):
    stream_id, user, unconfined = STREAM_TABLE[case]
    assert _outcome(UserSessionOwnership(ALICE).refuse_stream(stream_id)) == user
    assert _outcome(UNCONFINED.refuse_stream(stream_id)) == unconfined
    assert _outcome(REFUSING.refuse_stream(stream_id)) == NOT_FOUND


EVENT_TABLE = [
    # (event, User Alice may receive it)
    ({"reason": "attention_pending"}, True),
    ({"profile": ALICE}, True),
    ({"profile": ALICE, "session_id": "alice-webui"}, True),
    ({"profile": ALICE, "session_id": f"{ALICE}-gateway"}, True),
    ({"profile": BOB}, False),
    ({"profile": BOB, "session_id": "bob-webui"}, False),
    ({"profile": ALICE, "session_id": "bob-webui"}, False),
    ({"profile": ALICE, "session_id": "no-such-session"}, False),
    ({"session_id": "root-webui"}, False),
    ({"session_id": "alice-webui"}, False),
    (None, True),
]


@pytest.mark.parametrize("event,user", EVENT_TABLE)
def test_may_this_event_go_to_me(world, event, user):
    assert UserSessionOwnership(ALICE).may_receive_event(event) is user
    assert UNCONFINED.may_receive_event(event) is True
    assert REFUSING.may_receive_event(event) is False


ROW_TABLE = [
    # (row, User Alice, unconfined in the root Profile, unconfined in Bob's, all Profiles)
    ({"session_id": "alice-webui", "profile": ALICE}, True, False, False, True),
    ({"session_id": f"{ALICE}-gateway", "profile": ALICE, "source_tag": "telegram"}, True, False, False, True),
    ({"session_id": "bob-webui", "profile": BOB}, False, False, True, True),
    ({"session_id": "root-webui", "profile": "default"}, False, True, False, True),
    ({"session_id": "legacy", "profile": None}, False, True, False, True),
    ({"session_id": "claude_code_x", "profile": None, "source_tag": "claude_code"}, False, True, False, True),
    ("not a row", False, True, False, True),
]


@pytest.mark.parametrize("row,user,root,bobs,every", ROW_TABLE)
def test_may_this_row_go_to_me(world, row, user, root, bobs, every):
    assert UserSessionOwnership(ALICE).may_list_row(row) is user
    # The User's own Profile wins over any view the request asks for.
    assert UserSessionOwnership(ALICE).may_list_row(row, active_profile=BOB, all_profiles=True) is user
    assert UNCONFINED.may_list_row(row) is root
    assert UNCONFINED.may_list_row(row, active_profile=BOB) is bobs
    assert UNCONFINED.may_list_row(row, all_profiles=True) is every
    assert REFUSING.may_list_row(row) is False


LISTED_TABLE = [
    # (a session the route has already found, User Alice, unconfined in the root Profile)
    ({"session_id": f"{ALICE}-gateway", "profile": ALICE}, OWNED, "409 521740"),
    ({"session_id": f"{BOB}-gateway", "profile": BOB}, NOT_FOUND, OWNER_BOB),
    ({"session_id": "cli-root", "profile": "default"}, NOT_FOUND, OWNED),
    ({"session_id": "claude_code_x", "profile": None, "source_tag": "claude_code"}, NOT_FOUND, OWNED),
    ({}, NOT_FOUND, OWNED),
    (models.Session(session_id="rec-alice", profile=ALICE), OWNED, "409 521740"),
    (models.Session(session_id="rec-bob", profile=BOB), NOT_FOUND, OWNER_BOB),
    (None, NOT_FOUND, OWNED),
]


@pytest.mark.parametrize("row,user,unconfined", LISTED_TABLE)
def test_is_this_found_session_mine(world, row, user, unconfined):
    sid = (row.get("session_id") if isinstance(row, dict) else getattr(row, "session_id", None)) or "missing"
    assert _outcome(UserSessionOwnership(ALICE).refuse_found_session(sid, row)) == user
    assert _outcome(UNCONFINED.refuse_found_session(sid, row)) == unconfined
    assert _outcome(REFUSING.refuse_found_session(sid, row)) == NOT_FOUND


def test_a_profile_less_row_opens_under_a_named_profile_only_from_the_listing(world, monkeypatch):
    # The Admin in Bob's Profile: the detail load opens a Claude Code row it
    # knows from the listing; any other route that found it refuses it.
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: BOB)
    row = {"session_id": "claude_code_x", "profile": None, "source_tag": "claude_code"}
    assert UNCONFINED.refuse_listed_session("claude_code_x", row) is None
    assert _outcome(UNCONFINED.refuse_found_session("claude_code_x", row)) == NOT_FOUND
    legacy = {"session_id": "legacy", "profile": None, "source_tag": "cli"}
    assert _outcome(UNCONFINED.refuse_listed_session("legacy", legacy)) == NOT_FOUND
    assert _outcome(UserSessionOwnership(ALICE).refuse_listed_session("claude_code_x", row)) == NOT_FOUND
    assert _outcome(REFUSING.refuse_listed_session("claude_code_x", row)) == NOT_FOUND


class _Handler:
    def __init__(self):
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue())


def test_bound_names_only_its_own_profile(world):
    user = UserSessionOwnership(ALICE)
    assert user.may_name_profile(ALICE) is True
    assert [user.may_name_profile(name) for name in (BOB, "default", "", None, 42)] == [False] * 5
    assert user.may_switch_profile() is False
    assert user.sees_profile_less_sessions() is False
    assert user.keeps_upstream_rules() is False
    assert UNCONFINED.keeps_upstream_rules() is True
    assert REFUSING.keeps_upstream_rules() is False
    assert UNCONFINED.may_name_profile(BOB) and UNCONFINED.may_switch_profile()
    assert UNCONFINED.sees_profile_less_sessions() is True
    assert not REFUSING.may_name_profile(ALICE) and not REFUSING.may_switch_profile()
    assert REFUSING.sees_profile_less_sessions() is False


def test_a_refusal_writes_its_own_answer():
    handler = _Handler()
    Refusal().answer(handler, "sid")
    assert (handler.status, handler.body()) == (404, {"error": "Session not found"})

    handler = _Handler()
    Refusal(owner=BOB).answer(handler, "sid")
    assert handler.status == 409
    assert handler.body() == {"error": "Session belongs to a different profile",
                              "code": "session_profile_mismatch", "session_id": "sid", "profile": BOB}

    handler = _Handler()
    Refusal(owner=BOB).answer_not_found(handler)
    assert (handler.status, handler.body()) == (404, {"error": "Session not found"})

    handler = _Handler()
    Refusal(owner=BOB, session_id="owner-sid").answer(handler)
    assert handler.body()["session_id"] == "owner-sid"

    handler = _Handler()
    Refusal().answer(handler, "sid", not_found="Session not found in CLI store")
    assert (handler.status, handler.body()) == (404, {"error": "Session not found in CLI store"})


# ── The choice of adapter ────────────────────────────────────────────────────

CHOICE_TABLE = [
    # (admission, has a Directory session, adapter)
    (Admitted(ROLE_MEMBER, ALICE), True, "user"),
    (Admitted(ROLE_ADMIN, "default"), True, "admin"),
    (None, False, "unconfined"),
    (None, True, "refusing"),
    (Admitted("superuser", ALICE), True, "refusing"),
    (Admitted(ROLE_MEMBER, ""), True, "refusing"),
    (Admitted(ROLE_MEMBER, "../escape"), True, "refusing"),
]


def _kind(adapter) -> str:
    if isinstance(adapter, UserSessionOwnership):
        assert adapter.profile == ALICE
        return "user"
    return {id(UNCONFINED): "unconfined", id(ADMIN): "admin", id(REFUSING): "refusing"}[id(adapter)]


@pytest.mark.parametrize("admission,directory_session,expected", CHOICE_TABLE)
def test_the_adapter_is_chosen_from_the_admission(world, admission, directory_session, expected):
    assert _kind(ownership_for(admission, directory_session=directory_session)) == expected


def test_the_request_asks_with_its_own_admission(world, monkeypatch):
    monkeypatch.setattr(access._request, "admission", Admitted(ROLE_MEMBER, ALICE), raising=False)
    monkeypatch.setattr(access._request, "directory_session", True, raising=False)
    assert _kind(request_session_ownership()) == "user"
    access.clear_request_admission()
    assert _kind(request_session_ownership()) == "unconfined"
