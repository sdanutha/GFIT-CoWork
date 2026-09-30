"""GFIT-CoWork cross-Profile reads, step (a): the gaps, closed on today's code.

- A User's insights count only the sessions they own (ticket 01).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Admin keeps today's behaviour in each case.
"""
from __future__ import annotations

import json
import time

import pytest

import api.routes as routes
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


# ── Ticket 01: insights ──────────────────────────────────────────────────────

def _index_row(sid, profile, model, *, ts, messages, tokens_in, tokens_out, cost):
    return {
        "session_id": sid, "title": sid, "profile": profile, "model": model,
        "created_at": ts, "updated_at": ts, "message_count": messages,
        "input_tokens": tokens_in, "output_tokens": tokens_out,
        "cache_read_tokens": 0, "estimated_cost": cost,
    }


@pytest.fixture
def usage_index(monkeypatch, tmp_path):
    """A session index holding Alice's and Bob's sessions, with usage."""
    now = time.time()
    alice_ts = now - 3600
    # Bob's session sits on a different day and hour from Alice's, so every
    # activity chart would show it if it were counted.
    bob_ts = now - 86400 - 7200
    rows = [
        _index_row("alice_one", ALICE, "alice-model", ts=alice_ts,
                   messages=4, tokens_in=100, tokens_out=50, cost=0.25),
        _index_row("alice_two", ALICE, "alice-model", ts=alice_ts,
                   messages=2, tokens_in=10, tokens_out=5, cost=0.05),
        _index_row("bob_one", BOB, "bob-model", ts=bob_ts,
                   messages=40, tokens_in=9000, tokens_out=7000, cost=12.5),
    ]
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "_index.json").write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(routes, "SESSION_DIR", session_dir)
    return {"alice_ts": alice_ts, "bob_ts": bob_ts}


def _insights(client) -> dict:
    status, body, _ = client.get("/api/insights?days=7")
    assert status == 200, body
    return body


def _activity(body) -> tuple[int, int, int]:
    return (
        sum(d["sessions"] for d in body["daily_tokens"]),
        sum(d["sessions"] for d in body["activity_by_day"]),
        sum(h["sessions"] for h in body["activity_by_hour"]),
    )


def test_a_users_insights_count_only_their_own_sessions(srv, usage_index):
    body = _insights(srv.logged_in(ALICE))

    assert body["total_sessions"] == 2
    assert body["total_messages"] == 6
    assert body["total_input_tokens"] == 110
    assert body["total_output_tokens"] == 55
    assert body["total_cost"] == pytest.approx(0.30)
    assert [m["model"] for m in body["models"]] == ["alice-model"]
    assert body["models"][0]["session_share"] == 100
    assert body["models"][0]["token_share"] == 100
    assert body["models"][0]["cost_share"] == 100
    assert _activity(body) == (2, 2, 2)
    assert "bob-model" not in json.dumps(body)


def test_each_user_sees_only_their_own_figures(srv, usage_index):
    body = _insights(srv.logged_in(BOB))

    assert body["total_sessions"] == 1
    assert body["total_messages"] == 40
    assert [m["model"] for m in body["models"]] == ["bob-model"]
    assert _activity(body) == (1, 1, 1)
    assert "alice-model" not in json.dumps(body)


def test_the_admins_insights_count_every_profile(srv, usage_index):
    body = _insights(srv.logged_in(ADMIN))

    assert body["total_sessions"] == 3
    assert body["total_messages"] == 46
    assert sorted(m["model"] for m in body["models"]) == ["alice-model", "bob-model"]
    assert _activity(body) == (3, 3, 3)


# ── Step (b), ticket 06: the session list and search ask the question ────────

def _session_with_a_message(client, text) -> str:
    """Create a session and seed one exchange (empty sessions are not listed)."""
    from api.models import get_session

    status, body, _ = client.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    session = get_session(sid)
    session.messages = [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
    session.title = f"chat {text}"
    session.save()
    return sid


@pytest.fixture
def three_sessions(srv):
    clients = {uid: srv.logged_in(uid) for uid in (ALICE, BOB, ADMIN)}
    sids = {uid: _session_with_a_message(c, f"zebra-{uid}") for uid, c in clients.items()}
    return clients, sids


def _only_own(ids, sids, uid) -> bool:
    """*uid*'s session is listed and nobody else's from this test is.

    The WebUI session store is shared across tests, so a Profile's sessions
    from earlier tests may be listed too; they are that Profile's own.
    """
    others = {sid for owner, sid in sids.items() if owner != uid}
    return sids[uid] in ids and not ids & others


def _listed(client, path) -> tuple[set, dict]:
    status, body, _ = client.get(path)
    assert status == 200, body
    return {s["session_id"] for s in body["sessions"]}, body


def test_the_admin_counts_and_lists_other_profiles_sessions(three_sessions):
    clients, sids = three_sessions

    ids, body = _listed(clients[ADMIN], "/api/sessions")
    assert _only_own(ids, sids, ADMIN)
    assert body["all_profiles"] is False
    assert body["other_profile_count"] >= 2

    ids, body = _listed(clients[ADMIN], "/api/sessions?all_profiles=1")
    assert set(sids.values()) <= ids
    assert body["all_profiles"] is True
    assert body["other_profile_count"] == 0


def test_a_users_list_counts_no_other_profile_after_the_admin_filled_the_cache(three_sessions):
    clients, sids = three_sessions
    _listed(clients[ADMIN], "/api/sessions")

    for path in ("/api/sessions", "/api/sessions?all_profiles=1"):
        ids, body = _listed(clients[ALICE], path)
        assert _only_own(ids, sids, ALICE)
        assert body["all_profiles"] is False
        assert body["other_profile_count"] == 0


def test_session_search_reads_only_the_profiles_a_request_may_read(three_sessions):
    clients, sids = three_sessions

    for path in ("/api/sessions/search?q=zebra", "/api/sessions/search?q=zebra&all_profiles=1"):
        ids, _ = _listed(clients[ALICE], path)
        assert _only_own(ids, sids, ALICE)

    ids, _ = _listed(clients[ADMIN], "/api/sessions/search?q=zebra")
    assert _only_own(ids, sids, ADMIN)
    ids, _ = _listed(clients[ADMIN], "/api/sessions/search?q=zebra&all_profiles=1")
    assert set(sids.values()) <= ids


# ── Step (b), ticket 08: the Profile list and CLI import ask the question ────

def test_the_profile_list_shows_what_the_request_may_read(srv):
    status, body, _ = srv.logged_in(ALICE).get("/api/profiles")
    assert status == 200, body
    assert [p["name"] for p in body["profiles"]] == [ALICE]
    assert body["single_profile_mode"] is True

    status, body, _ = srv.logged_in(ADMIN).get("/api/profiles")
    assert status == 200, body
    assert {ALICE, BOB} <= {p["name"] for p in body["profiles"]}
    assert body["single_profile_mode"] is False


def _cli_session_in(srv, uid, sid):
    """A CLI session in *uid*'s Profile state, with no WebUI record."""
    import sqlite3

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
            "INSERT INTO sessions (id, source, title, started_at, message_count) VALUES (?, 'cli', 'cli chat', ?, 2)",
            (sid, now),
        )
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            [(sid, "user", "hello", now), (sid, "assistant", "hi", now + 1)],
        )
    conn.close()
    return sid


def test_all_profiles_cli_import_finds_nothing_in_another_users_profile(srv):
    import uuid

    from api.models import Session

    sid = _cli_session_in(srv, BOB, f"bob_cli_{uuid.uuid4().hex[:10]}")
    alice = srv.logged_in(ALICE)

    for body in ({"session_id": sid, "all_profiles": True, "profile": ALICE},
                 {"session_id": sid, "all_profiles": True}):
        status, payload, _ = alice.post("/api/session/import_cli", body)
        assert status in (403, 404), payload
    assert Session.load(sid) is None

    status, payload, _ = srv.logged_in(ADMIN).post(
        "/api/session/import_cli", {"session_id": sid, "all_profiles": True, "profile": BOB},
    )
    assert status == 200, payload
    assert payload["session"]["profile"] == BOB
