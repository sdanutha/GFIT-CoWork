"""GFIT-CoWork session ownership, step (a): the gaps, closed on today's code.

- A User's session-list events stream carries only their own Profile's events
  (ticket 01).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Admin keeps today's behaviour in each case.
"""
from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

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
