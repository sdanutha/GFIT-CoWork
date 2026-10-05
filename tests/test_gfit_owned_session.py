"""GFIT-CoWork: a route loads the session its request names under session ownership.

Architecture review round 6, candidate 2. Routes load a request-named session
through ``session_ownership.load_owned_session``, and a route whose row names
a stream has the stream's owning session checked by the dispatch guard. A User
gets the same 404 for another User's session or stream as for a missing one.
"""
from __future__ import annotations

import pytest

from api.config import register_stream_owner, unregister_stream_owner
from tests._gfit_server import gfit_server as _gfit_server

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


def _new_session(client) -> str:
    status, payload, _ = client.post("/api/session/new", {})
    assert status == 200, payload
    return payload["session"]["session_id"]


@pytest.fixture
def bobs_stream(srv):
    bob = srv.logged_in(BOB)
    sid = _new_session(bob)
    stream_id = "gfit-owned-session-bobs-stream"
    register_stream_owner(stream_id, sid)
    yield bob, sid, stream_id
    unregister_stream_owner(stream_id)


@pytest.mark.parametrize("path", ["/api/chat/stream/status", "/api/chat/cancel"])
def test_another_users_stream_is_a_missing_stream(srv, bobs_stream, path):
    _bob, _sid, stream_id = bobs_stream
    alice = srv.logged_in(ALICE)
    theirs = alice.get(f"{path}?stream_id={stream_id}")[:2]
    missing = alice.get(f"{path}?stream_id=gfit-no-such-stream")[:2]
    assert theirs == missing == (404, {"error": "Session not found"})


def test_the_owner_reads_their_own_stream_status(srv, bobs_stream):
    bob, _sid, stream_id = bobs_stream
    status, payload, _ = bob.get(f"/api/chat/stream/status?stream_id={stream_id}")
    assert status == 200 and payload["stream_id"] == stream_id


def test_an_upload_into_another_users_session_is_a_missing_session(srv):
    bob_sid = _new_session(srv.logged_in(BOB))
    alice = srv.logged_in(ALICE)
    theirs = alice.post_file("/api/upload", {"session_id": bob_sid}, "note.txt", b"hello")[:2]
    missing = alice.post_file("/api/upload", {"session_id": "gfit-no-such-session"}, "note.txt", b"hello")[:2]
    assert theirs == missing == (404, {"error": "Session not found"})


@pytest.mark.parametrize("path", ["/api/session/export", "/api/session/status"])
def test_another_users_session_read_is_a_missing_session(srv, path):
    bob_sid = _new_session(srv.logged_in(BOB))
    alice = srv.logged_in(ALICE)
    theirs = alice.get(f"{path}?session_id={bob_sid}")[:2]
    missing = alice.get(f"{path}?session_id=gfit-no-such-session")[:2]
    assert theirs == missing == (404, {"error": "Session not found"})


def test_a_users_own_session_exports(srv):
    alice = srv.logged_in(ALICE)
    sid = _new_session(alice)
    status, _payload, _ = alice.get(f"/api/session/export?session_id={sid}")
    assert status == 200
