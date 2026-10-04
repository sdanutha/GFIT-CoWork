"""GFIT-CoWork: the Admin reads another Profile's session in place, read-only.

The Admin stays in ``default`` (ADR 0004). For a session of another Profile:

- every route the read/write table calls a read is answered, with no 409 and
  no switch;
- every route it calls a write answers 403 ``session_read_only`` naming the
  owning Profile, and the session is unchanged;
- the detail load marks the session read-only, with the reason and owner.

A User's answers are unchanged (another Profile's session is "not found"),
and with login turned off the 409 naming the owner stays.

HTTP tests against an in-process server (see ``tests/_gfit_server.py``),
naming the session the way each route does (``test_gfit_session_route_answers``).
"""
from __future__ import annotations

import json

import pytest

from api.session_ownership import READ, SESSION_ROUTE_KINDS, WRITE
from tests._gfit_server import gfit_server as _gfit_server
from tests.test_gfit_session_route_answers import (
    SESSION_ROUTES,
    Stream,
    _ask,
    _bob_session,
    _bob_stream,
    _bob_view,
    _forget_stream,
)

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


@pytest.fixture
def bob(srv):
    return srv.logged_in(BOB)


@pytest.fixture
def admin(srv):
    return srv.logged_in(ADMIN)


def _active(client) -> str:
    status, body, _ = client.get("/api/profile/active")
    assert status == 200, body
    return body["name"]


def _names_bob_workspace_files(srv):
    workspace = srv.profile_home(BOB) / "workspace"
    (workspace / "sub").mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("Bob's notes")


def test_the_admin_opens_another_profiles_session_read_only(srv, bob, admin):
    sid = _bob_session(bob)

    status, body, _ = admin.get(f"/api/session?session_id={sid}")

    assert status == 200, body
    session = body["session"]
    assert session["session_id"] == sid
    assert session["read_only"] is True
    assert session["read_only_reason"] == "other_profile"
    assert session["owner_profile"] == BOB
    assert _active(admin) == "default"


def test_the_admins_own_session_is_not_read_only(admin):
    status, body, _ = admin.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    admin.post("/api/session/rename", {"session_id": sid, "title": "Admin's plan"})

    status, body, _ = admin.get(f"/api/session?session_id={sid}")

    assert status == 200, body
    assert not body["session"].get("read_only")
    assert "read_only_reason" not in body["session"]


READS = sorted(route for route, kind in SESSION_ROUTE_KINDS.items() if kind == READ)
WRITES = sorted(route for route, kind in SESSION_ROUTE_KINDS.items() if kind == WRITE)


def _ask_about_bobs_session(srv, bob, admin, method, entry):
    how = SESSION_ROUTES[(method, entry)]
    sid = _bob_session(bob)
    _names_bob_workspace_files(srv)
    named = _bob_stream(sid) if isinstance(how, Stream) else sid
    before = _bob_view(srv, bob, sid)
    try:
        answer = _ask(admin, method, entry, how, named)
    finally:
        if isinstance(how, Stream):
            _forget_stream(named)
    return answer, before, _bob_view(srv, bob, sid)


@pytest.mark.parametrize("method,entry", READS)
def test_the_admin_reads_another_profiles_session(srv, bob, admin, method, entry):
    (status, text), _before, _after = _ask_about_bobs_session(srv, bob, admin, method, entry)

    assert status != 409, text
    assert "session_read_only" not in text
    assert "session_profile_mismatch" not in text
    assert _active(admin) == "default"


# A new session only borrows the previous session's Workspace when the caller
# may use that session; otherwise it ignores it (tested below).
REFUSED_WRITES = [route for route in WRITES if route != ("POST", "/api/session/new")]


@pytest.mark.parametrize("method,entry", REFUSED_WRITES)
def test_the_admin_cannot_write_another_profiles_session(srv, bob, admin, method, entry):
    (status, text), before, after = _ask_about_bobs_session(srv, bob, admin, method, entry)

    assert status == 403, text
    body = json.loads(text)
    assert body["code"] == "session_read_only"
    assert body["profile"] == BOB
    assert after == before


def test_a_users_answer_for_another_profiles_session_is_unchanged(srv, bob):
    sid = _bob_session(bob)

    status, body, _ = srv.logged_in(ALICE).get(f"/api/session?session_id={sid}")

    assert status == 404, body


def test_with_login_off_another_profiles_session_still_names_its_owner(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={}, profile_names=[ALICE], directory="") as srv:
        client = srv.client()
        client.cookies["hermes_profile"] = ALICE
        status, body, _ = client.post("/api/session/new", {})
        assert status == 200, body
        sid = body["session"]["session_id"]
        client.post("/api/session/rename", {"session_id": sid, "title": "Alice's plan"})
        client.cookies.pop("hermes_profile")

        status, body, _ = client.get(f"/api/session?session_id={sid}")

        assert status == 409, body
        assert body["code"] == "session_profile_mismatch"
        assert body["profile"] == ALICE


def test_a_new_session_after_another_profiles_session_does_not_take_its_workspace(srv, bob, admin):
    (status, text), before, after = _ask_about_bobs_session(srv, bob, admin, "POST", "/api/session/new")

    assert status == 200, text
    workspace = json.loads(text)["session"]["workspace"]
    assert str(srv.profile_home(BOB)) not in str(workspace)
    assert after == before
