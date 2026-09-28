"""GFIT-CoWork session ownership, step (a): the gaps, closed on today's code.

- A User's session-list events stream carries only their own Profile's events
  (ticket 01).
- A User cannot read or answer another Profile's pending approvals or clarify
  questions, whatever kind of session they belong to (ticket 02).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Admin keeps today's behaviour in each case.
"""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
import time

import pytest

from api import clarify
from api import route_approvals
from api.session_events import publish_session_list_changed
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


def _new_session(client, **body) -> str:
    status, payload, _ = client.post("/api/session/new", body)
    assert status == 200, payload
    return payload["session"]["session_id"]


def _rename(client, sid, title):
    status, payload, _ = client.post("/api/session/rename", {"session_id": sid, "title": title})
    assert status == 200, payload


# ── Ticket 01: the session-list events stream ────────────────────────────────

class EventStream:
    """The session-list events stream as one logged-in browser tab sees it."""

    def __init__(self, client):
        self.events: list[dict] = []
        self._conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=30)
        cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        self._conn.request("GET", "/api/sessions/events", headers={"Cookie": cookie})
        self._resp = self._conn.getresponse()
        assert self._resp.status == 200, self._resp.status
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        data = []
        try:
            while True:
                line = self._resp.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data: "):
                    data.append(line[6:])
                elif line == "" and data:
                    self.events.append(json.loads("\n".join(data)))
                    data = []
        except (OSError, ValueError):
            return

    def wait_for(self, predicate, timeout=5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in list(self.events):
                if predicate(event):
                    return event
            time.sleep(0.02)
        raise AssertionError(f"no matching event; got {self.events}")

    def close(self):
        self._conn.close()


@pytest.fixture
def streams(srv):
    clients = {uid: srv.logged_in(uid) for uid in (ALICE, BOB, ADMIN)}
    opened = {uid: EventStream(client) for uid, client in clients.items()}
    try:
        yield clients, opened
    finally:
        for stream in opened.values():
            stream.close()


def _names(event, uid_or_sid) -> bool:
    return uid_or_sid in json.dumps(event)


def test_a_users_events_stream_carries_only_their_own_profile(streams):
    clients, stream = streams
    alice_sid = _new_session(clients[ALICE])
    bob_sid = _new_session(clients[BOB])
    admin_sid = _new_session(clients[ADMIN])

    # Each change reaches its owner and the Admin before the next one starts,
    # so nothing is coalesced into an unscoped nudge on the way to Alice.
    _rename(clients[BOB], bob_sid, "Bob's plan")
    stream[BOB].wait_for(lambda e: e.get("session_id") == bob_sid)
    stream[ADMIN].wait_for(lambda e: e.get("session_id") == bob_sid and e.get("profile") == BOB)

    _rename(clients[ADMIN], admin_sid, "Admin's plan")
    stream[ADMIN].wait_for(lambda e: e.get("session_id") == admin_sid)

    _rename(clients[ALICE], alice_sid, "Alice's plan")
    stream[ALICE].wait_for(lambda e: e.get("session_id") == alice_sid)
    stream[ADMIN].wait_for(lambda e: e.get("session_id") == alice_sid)

    # A nudge that names no Profile and no session still reaches everyone.
    publish_session_list_changed("attention_pending")
    for uid in (ALICE, BOB, ADMIN):
        stream[uid].wait_for(lambda e: e.get("reason") == "attention_pending")

    leaked = [e for e in stream[ALICE].events
              if _names(e, BOB) or _names(e, bob_sid) or _names(e, admin_sid)]
    assert leaked == []
    assert not [e for e in stream[BOB].events if _names(e, ALICE) or _names(e, alice_sid)]


def test_a_users_events_stream_drops_an_event_about_a_session_they_do_not_own(streams):
    clients, stream = streams
    bob_sid = _new_session(clients[BOB])
    _rename(clients[BOB], bob_sid, "Bob's plan")
    stream[BOB].wait_for(lambda e: e.get("session_id") == bob_sid)

    # Named with Alice's Profile, but the session is Bob's; and an id nobody owns.
    publish_session_list_changed("session_rename", profile=ALICE, session_id=bob_sid)
    stream[ADMIN].wait_for(lambda e: e.get("session_id") == bob_sid and e.get("profile") == ALICE)
    publish_session_list_changed("session_rename", session_id="no-such-session")
    stream[ADMIN].wait_for(lambda e: e.get("session_id") == "no-such-session")
    publish_session_list_changed("attention_pending")
    stream[ALICE].wait_for(lambda e: e.get("reason") == "attention_pending")

    assert not [e for e in stream[ALICE].events
                if _names(e, bob_sid) or _names(e, "no-such-session")]


# ── Ticket 02: approvals and clarify questions ───────────────────────────────

MISSING = "no-such-session"


def _state_db_session(srv, uid, sid, source="telegram"):
    """A session in *uid*'s Profile state with no WebUI record (gateway, CLI, cron)."""
    conn = sqlite3.connect(srv.profile_home(uid) / "state.db")
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT,"
            " model TEXT, cwd TEXT, started_at REAL, ended_at REAL, end_reason TEXT,"
            " parent_session_id TEXT, message_count INTEGER)"
        )
        conn.execute(
            "INSERT INTO sessions (id, source, title, started_at, message_count) VALUES (?, ?, ?, ?, 1)",
            (sid, source, f"{source} chat", time.time()),
        )
    conn.close()
    return sid


def _stream_head(client, path) -> tuple[int, str]:
    """Status and the first event (or the whole body) of a GET that may be an SSE stream."""
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    conn.request("GET", path, headers={"Cookie": cookie})
    resp = conn.getresponse()
    if "text/event-stream" not in (resp.headers.get("Content-Type") or ""):
        text = resp.read().decode("utf-8", "replace")
    else:
        lines = []
        while True:
            line = resp.readline().decode("utf-8", "replace")
            if not line.strip() and lines:
                break
            lines.append(line)
        text = "".join(lines)
    conn.close()
    return resp.status, text


def _pending_approval(sid) -> str:
    route_approvals.submit_pending(sid, {"command": f"rm -rf {sid}", "pattern_key": "k",
                                         "pattern_keys": ["k"], "description": "test"})
    with route_approvals._lock:
        return route_approvals._pending[sid][-1]["approval_id"]


def _approval_ids(sid) -> list[str]:
    with route_approvals._lock:
        queue = route_approvals._pending.get(sid) or []
        return [entry["approval_id"] for entry in queue]


def _pending_clarify(sid) -> str:
    return clarify.submit_pending(sid, {"question": f"Which one for {sid}?",
                                        "choices_offered": ["a", "b"]}).clarify_id


def _clarify_ids(sid) -> list[str]:
    with clarify._lock:
        return [entry.clarify_id for entry in clarify._gateway_queues.get(sid) or []]


@pytest.fixture
def pending(srv):
    """Pending approvals and clarify questions in Alice's and Bob's sessions of every kind."""
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)
    sids = {
        "alice-webui": _new_session(alice),
        "alice-gateway": _state_db_session(srv, ALICE, "alice-gateway-1"),
        "bob-webui": _new_session(bob),
        "bob-gateway": _state_db_session(srv, BOB, "bob-gateway-1"),
        "bob-cli": _state_db_session(srv, BOB, "bob-cli-1", source="cli"),
    }
    # The dispatch guard answers a WebUI session only once it has a saved record.
    for kind in ("alice-webui", "bob-webui"):
        _rename(alice if kind.startswith("alice") else bob, sids[kind], kind)
    items = {kind: (_pending_approval(sid), _pending_clarify(sid)) for kind, sid in sids.items()}
    try:
        yield alice, bob, sids, items
    finally:
        for sid in [*sids.values(), MISSING]:
            with route_approvals._lock:
                route_approvals._pending.pop(sid, None)
            clarify.clear_pending(sid)


def _attention_answers(client, sid, approval_id, clarify_id) -> list[tuple[int, str]]:
    """What *client* sees reading and answering the pending items of *sid*, with the id masked."""
    answers = [
        client.get(f"/api/approval/pending?session_id={sid}")[:2],
        _stream_head(client, f"/api/approval/stream?session_id={sid}"),
        client.post("/api/approval/respond", {"session_id": sid, "approval_id": approval_id,
                                              "choice": "deny"})[:2],
        client.get(f"/api/clarify/pending?session_id={sid}")[:2],
        _stream_head(client, f"/api/clarify/stream?session_id={sid}"),
        client.post("/api/clarify/respond", {"session_id": sid, "clarify_id": clarify_id,
                                             "response": "a"})[:2],
    ]
    return [(status, str(body).replace(sid, "<sid>")) for status, body in answers]


@pytest.mark.parametrize("kind", ["bob-webui", "bob-gateway", "bob-cli"])
def test_another_profiles_approvals_and_questions_are_the_same_as_a_missing_session(pending, kind):
    alice, _bob, sids, items = pending
    approval_id, clarify_id = items[kind]
    theirs = _attention_answers(alice, sids[kind], approval_id, clarify_id)
    missing = _attention_answers(alice, MISSING, approval_id, clarify_id)
    assert theirs == missing
    assert all(status == 404 for status, _ in theirs), theirs
    assert _approval_ids(sids[kind]) == [approval_id]
    assert _clarify_ids(sids[kind]) == [clarify_id]


@pytest.mark.parametrize("kind", ["alice-webui", "alice-gateway"])
def test_a_user_reads_and_answers_their_own_approvals_and_questions(pending, kind):
    alice, _bob, sids, items = pending
    sid = sids[kind]
    approval_id, clarify_id = items[kind]

    status, body, _ = alice.get(f"/api/approval/pending?session_id={sid}")
    assert status == 200 and body["pending"]["approval_id"] == approval_id, body
    status, text = _stream_head(alice, f"/api/approval/stream?session_id={sid}")
    assert status == 200 and approval_id in text, text
    status, body, _ = alice.post("/api/approval/respond", {"session_id": sid, "approval_id": approval_id,
                                                           "choice": "deny"})
    assert status == 200, body
    assert _approval_ids(sid) == []

    status, body, _ = alice.get(f"/api/clarify/pending?session_id={sid}")
    assert status == 200 and body["pending"]["clarify_id"] == clarify_id, body
    status, text = _stream_head(alice, f"/api/clarify/stream?session_id={sid}")
    assert status == 200 and clarify_id in text, text
    status, body, _ = alice.post("/api/clarify/respond", {"session_id": sid, "clarify_id": clarify_id,
                                                          "response": "a"})
    assert status == 200, body
    assert _clarify_ids(sid) == []


@pytest.mark.parametrize("kind", ["alice-gateway", "bob-gateway", "bob-cli"])
def test_the_admin_reads_and_answers_any_profiles_state_session_as_today(srv, pending, kind):
    # A session with no WebUI record is not placed for the Admin: today's pass-through.
    _alice, _bob, sids, items = pending
    admin = srv.logged_in(ADMIN)
    sid = sids[kind]
    approval_id, clarify_id = items[kind]
    status, body, _ = admin.get(f"/api/approval/pending?session_id={sid}")
    assert status == 200 and body["pending"]["approval_id"] == approval_id, body
    status, body, _ = admin.post("/api/approval/respond", {"session_id": sid, "approval_id": approval_id,
                                                           "choice": "deny"})
    assert status == 200, body
    assert _approval_ids(sid) == []
    status, body, _ = admin.get(f"/api/clarify/pending?session_id={sid}")
    assert status == 200 and body["pending"]["clarify_id"] == clarify_id, body
    status, body, _ = admin.post("/api/clarify/respond", {"session_id": sid, "clarify_id": clarify_id,
                                                          "response": "a"})
    assert status == 200, body
    assert _clarify_ids(sid) == []


def test_the_admin_on_another_profiles_webui_session_answers_as_today(srv, pending):
    _alice, _bob, sids, items = pending
    admin = srv.logged_in(ADMIN)
    status, body, _ = admin.get(f"/api/approval/pending?session_id={sids['bob-webui']}")
    assert status == 409 and body["profile"] == BOB, body
