"""GFIT-CoWork ticket 03: a Member cannot reach another Profile.

Every request from a Member runs in the Profile bound to their session,
whatever Profile the client names. HTTP tests against an in-process server
(see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

import api.routes as routes
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB]) as s:
        yield s


@pytest.fixture
def alice(srv):
    return srv.logged_in(ALICE)


@pytest.fixture
def bob(srv):
    return srv.logged_in(BOB)


def _active_profile(client):
    status, body, _ = client.get("/api/profile/active")
    assert status == 200, body
    return body["name"]


def test_profile_cookie_naming_another_profile_is_ignored(alice):
    alice.cookies["hermes_profile"] = BOB
    assert _active_profile(alice) == ALICE
    status, body, _ = alice.get("/api/memory")
    assert status == 200


def test_a_profile_cookie_from_before_naming_another_profile_is_ignored(alice):
    # The Profile cookie went with Profile switching (ADR 0006); an old one is ignored.
    alice.cookies["hermes_profile"] = BOB
    assert _active_profile(alice) == ALICE


@pytest.mark.parametrize("path", [
    f"/api/sessions?profile={BOB}",
    f"/api/memory?profile={BOB}",
    f"/api/skills?profile={BOB}",
])
def test_query_string_naming_another_profile_is_refused(alice, path):
    status, body, _ = alice.get(path)
    assert status == 403, body


def test_query_string_naming_own_profile_is_allowed(alice):
    status, body, _ = alice.get(f"/api/sessions?profile={ALICE}")
    assert status == 200, body


def test_body_naming_another_profile_is_refused(alice):
    status, body, _ = alice.post("/api/session/new", {"profile": BOB})
    assert status == 403, body


def test_body_naming_own_profile_is_allowed(alice):
    status, body, _ = alice.post("/api/session/new", {"profile": ALICE})
    assert status == 200, body
    assert body["session"]["profile"] == ALICE


def test_profile_switch_is_403(alice):
    for name in (BOB, ALICE, "default"):
        status, body, _ = alice.post("/api/profile/switch", {"name": name})
        assert status == 403, (name, body)
    assert _active_profile(alice) == ALICE


def test_profile_list_shows_only_own_profile_in_single_profile_mode(alice):
    status, body, _ = alice.get("/api/profiles")
    assert status == 200, body
    assert [p["name"] for p in body["profiles"]] == [ALICE]
    assert body["single_profile_mode"] is True


def _new_session_with_a_message(client) -> str:
    """Create a session and seed one exchange (empty sessions are not listed)."""
    from api.models import get_session

    status, body, _ = client.post("/api/session/new", {})
    assert status == 200, body
    sid = body["session"]["session_id"]
    session = get_session(sid)
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    session.title = f"chat {sid}"
    session.save()
    return sid


def test_member_a_cannot_see_member_b_sessions(alice, bob):
    bob_sid = _new_session_with_a_message(bob)
    alice_sid = _new_session_with_a_message(alice)

    for path in ("/api/sessions", "/api/sessions?all_profiles=1"):
        status, body, _ = alice.get(path)
        assert status == 200, body
        ids = {s["session_id"] for s in body["sessions"]}
        assert bob_sid not in ids
        assert body.get("other_profile_count", 0) == 0

    status, body, _ = alice.get(f"/api/session?session_id={bob_sid}")
    assert status in (403, 404), body
    status, body, _ = alice.post("/api/session/rename", {"session_id": bob_sid, "title": "pwned"})
    assert status in (403, 404), body

    status, body, _ = bob.get("/api/sessions")
    assert bob_sid in {s["session_id"] for s in body["sessions"]}
    assert alice_sid not in {s["session_id"] for s in body["sessions"]}


def test_member_sees_only_own_memory(srv, alice):
    for uid in (ALICE, BOB):
        mem = srv.profile_home(uid) / "memories"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text(f"memory of {uid}")
    status, body, _ = alice.get("/api/memory")
    assert status == 200, body
    assert f"memory of {ALICE}" in json.dumps(body)
    assert f"memory of {BOB}" not in json.dumps(body)


def test_member_sees_only_own_skills(srv, alice):
    pytest.importorskip("tools.skills_tool", reason="the skills list needs hermes-agent modules")
    for uid in (ALICE, BOB):
        skill = srv.profile_home(uid) / "skills" / f"skill-of-{uid}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: skill-of-{uid}\ndescription: a skill\n---\nbody\n"
        )
    status, body, _ = alice.get("/api/skills")
    assert status == 200, body
    text = json.dumps(body)
    assert f"skill-of-{ALICE}" in text
    assert f"skill-of-{BOB}" not in text


@pytest.fixture
def fake_cron(monkeypatch):
    """Stand in for the agent's ``cron.jobs``: jobs live in $HERMES_HOME/cron/jobs.json."""
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def list_jobs(include_disabled=True):
        path = Path(os.environ["HERMES_HOME"]) / "cron" / "jobs.json"
        return json.loads(path.read_text()) if path.exists() else []

    cron_jobs.list_jobs = list_jobs
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setattr(routes, "_ensure_agent_cron_import_path", lambda: None)


def test_member_sees_only_own_cron_jobs(srv, alice, fake_cron):
    for uid in (ALICE, BOB):
        cron = srv.profile_home(uid) / "cron"
        cron.mkdir(parents=True)
        (cron / "jobs.json").write_text(json.dumps([{"id": f"job-{uid}", "name": f"job of {uid}"}]))
    for path in ("/api/crons", "/api/crons?all_profiles=1"):
        status, body, _ = alice.get(path)
        assert status == 200, body
        assert [j["id"] for j in body["jobs"]] == [f"job-{ALICE}"]
        assert body["other_profile_count"] == 0


def test_member_b_cannot_clean_up_member_a_empty_sessions(alice, bob):
    status, body, _ = alice.post("/api/session/new", {})
    assert status == 200, body
    alice_sid = body["session"]["session_id"]
    status, body, _ = alice.post("/api/session/rename", {"session_id": alice_sid, "title": "Plans"})
    assert status == 200, body  # now on disk, still with no messages

    status, body, _ = bob.post("/api/sessions/cleanup_zero_message", {})
    assert status == 403, body

    status, body, _ = alice.get(f"/api/session?session_id={alice_sid}")
    assert status == 200, body
