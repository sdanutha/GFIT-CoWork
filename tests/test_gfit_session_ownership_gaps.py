"""GFIT-CoWork session ownership, step (a): the gaps, closed on today's code.

- A User's session-list events stream carries only their own Profile's events
  (ticket 01).
- A User cannot read or answer another Profile's pending approvals or clarify
  questions, whatever kind of session they belong to (ticket 02).
- A User is not shown, and cannot open, the server account's Claude Code
  sessions, which belong to no Profile (ticket 03).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
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


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB],
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
    clients = {uid: srv.logged_in(uid) for uid in (ALICE, BOB)}
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

    # Each change reaches its owner before the next one starts, so nothing is
    # coalesced into an unscoped nudge on the way to Alice.
    _rename(clients[BOB], bob_sid, "Bob's plan")
    stream[BOB].wait_for(lambda e: e.get("session_id") == bob_sid)

    _rename(clients[ALICE], alice_sid, "Alice's plan")
    stream[ALICE].wait_for(lambda e: e.get("session_id") == alice_sid)

    # A nudge that names no Profile and no session still reaches everyone.
    publish_session_list_changed("attention_pending")
    for uid in (ALICE, BOB):
        stream[uid].wait_for(lambda e: e.get("reason") == "attention_pending")

    leaked = [e for e in stream[ALICE].events
              if _names(e, BOB) or _names(e, bob_sid)]
    assert leaked == []
    assert not [e for e in stream[BOB].events if _names(e, ALICE) or _names(e, alice_sid)]


def test_a_users_events_stream_drops_an_event_about_a_session_they_do_not_own(streams):
    clients, stream = streams
    bob_sid = _new_session(clients[BOB])
    _rename(clients[BOB], bob_sid, "Bob's plan")
    stream[BOB].wait_for(lambda e: e.get("session_id") == bob_sid)

    # Named with Alice's Profile, but the session is Bob's; and an id nobody owns.
    # Events reach each stream in the order published: once Alice has the
    # nudge published after them, she would have had these too.
    publish_session_list_changed("session_rename", profile=ALICE, session_id=bob_sid)
    publish_session_list_changed("session_rename", session_id="no-such-session")
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
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id TEXT, role TEXT, content TEXT, timestamp REAL)"
        )
        now = time.time()
        conn.execute(
            "INSERT INTO sessions (id, source, title, started_at, message_count) VALUES (?, ?, ?, ?, 2)",
            (sid, source, f"{source} chat", now),
        )
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            [(sid, "user", "hello", now), (sid, "assistant", "hi", now + 1)],
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


# ── Ticket 03: sessions that belong to no Profile ────────────────────────────

CLAUDE_CODE_TEXT = "the server account's private Claude Code history"


@pytest.fixture
def claude_code(srv, tmp_path, monkeypatch):
    """A Claude Code transcript in the server account's home, and the settings that show it."""
    projects = tmp_path / "server-home" / ".claude" / "projects"
    transcript = projects / "some-project" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"summary": "Server-side Claude Code"},
        {"timestamp": "2026-09-01T12:00:01Z", "message": {"role": "user", "content": CLAUDE_CODE_TEXT}},
        {"timestamp": "2026-09-01T12:00:02Z", "message": {"role": "assistant", "content": "ok"}},
    ]) + "\n")
    monkeypatch.setenv("HERMES_WEBUI_CLAUDE_PROJECTS_DIR", str(projects))
    alice = srv.logged_in(ALICE)
    status, body, _ = alice.post("/api/settings", {"show_cli_sessions": True,
                                                   "show_claude_code_sessions": True})
    assert status == 200, body
    from api.models import _claude_code_session_id, clear_cli_sessions_cache
    clear_cli_sessions_cache()
    return alice, _claude_code_session_id(transcript)


def _listed_ids(client) -> set[str]:
    status, body, _ = client.get("/api/sessions")
    assert status == 200, body
    return {row["session_id"] for row in body["sessions"]}


def test_a_user_is_not_shown_or_given_claude_code_sessions(srv, claude_code):
    alice, cc_sid = claude_code
    status, body, _ = alice.get("/api/sessions")
    assert status == 200, body
    assert not [row for row in body["sessions"] if row.get("source_tag") == "claude_code"]
    assert CLAUDE_CODE_TEXT not in json.dumps(body)

    theirs = alice.get(f"/api/session?session_id={cc_sid}")[:2]
    missing = alice.get(f"/api/session?session_id={MISSING}")[:2]
    assert theirs[0] == 404, theirs
    assert (theirs[0], str(theirs[1]).replace(cc_sid, "<sid>")) == \
        (missing[0], str(missing[1]).replace(MISSING, "<sid>"))

    status, body, _ = alice.post("/api/session/import_cli", {"session_id": cc_sid})
    assert status == 404, body
    assert CLAUDE_CODE_TEXT not in json.dumps(body)
    assert cc_sid not in _listed_ids(alice)


def test_a_users_own_cli_sessions_still_show(srv, claude_code):
    _state_db_session(srv, ALICE, "alice-cli-1", source="cli")
    alice = srv.logged_in(ALICE)
    assert "alice-cli-1" in _listed_ids(alice)
    status, body, _ = alice.get("/api/session?session_id=alice-cli-1")
    assert status == 200, body

