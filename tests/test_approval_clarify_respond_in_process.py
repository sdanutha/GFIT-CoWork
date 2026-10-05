"""A pending approval or clarify prompt is answered over HTTP and then cleared.

These used to inject the pending entry through Admin-only test routes
(``/api/approval/inject_test``, ``/api/clarify/inject_test``) on the shared
test server. Those routes are gone with the Admin (ADR 0006), so the server
runs in this process (``tests/_gfit_server.py``) and the entry is submitted
directly, as the agent does, for a session the logged-in User owns.
"""
from __future__ import annotations

import pytest

import api.routes as routes
from tests._gfit_server import gfit_server as _gfit_server

USER = "521740"


@pytest.fixture
def user(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={USER: "User"}, profile_names=[USER]) as s:
        yield s.logged_in(USER)


def _own_session(client) -> str:
    status, body, _ = client.post("/api/session/new", {})
    assert status == 200, body
    return body["session"]["session_id"]


def _pending(client, kind, sid):
    status, body, _ = client.get(f"/api/{kind}/pending?session_id={sid}")
    assert status == 200, body
    return body["pending"]


@pytest.mark.parametrize("choice", ["deny", "session"])
def test_answering_a_pending_approval_clears_it(user, choice):
    sid = _own_session(user)
    routes.submit_pending(sid, {
        "command": "rm -rf /tmp/testdir", "pattern_key": "recursive_delete",
        "pattern_keys": ["recursive_delete"], "description": "test pattern",
    })
    assert _pending(user, "approval", sid)["command"] == "rm -rf /tmp/testdir"

    status, body, _ = user.post("/api/approval/respond", {"session_id": sid, "choice": choice})

    assert status == 200, body
    assert (body["ok"], body["choice"]) == (True, choice)
    assert _pending(user, "approval", sid) is None


def test_answering_a_pending_clarify_prompt_clears_it(user):
    sid = _own_session(user)
    routes.submit_clarify_pending(sid, {
        "question": "Pick the better option", "choices_offered": ["A"], "session_id": sid, "kind": "clarify",
    })
    assert _pending(user, "clarify", sid) is not None

    status, body, _ = user.post("/api/clarify/respond", {"session_id": sid, "response": "B"})

    assert status == 200, body
    assert body["ok"] is True
    assert _pending(user, "clarify", sid) is None


def test_a_user_cannot_switch_on_yolo_through_an_approval_answer(user):
    # Session YOLO went with the Admin (ADR 0006); the answer route was a way round it.
    sid = _own_session(user)
    routes.submit_pending(sid, {
        "command": "rm -rf /tmp/testdir", "pattern_key": "recursive_delete",
        "pattern_keys": ["recursive_delete"], "description": "test pattern",
    })

    status, body, _ = user.post("/api/approval/respond", {"session_id": sid, "choice": "once", "yolo": True})

    assert status == 400, body
    assert "YOLO" in body["error"]
    assert _pending(user, "approval", sid) is not None
