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
