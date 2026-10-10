"""GFIT-CoWork: the Operator's command line and the server's roster watcher (ADR 0006).

The Operator manages Profiles with ``python3 -m api.operator_cli`` on the
server. The command line changes durable state only: the roster, the Hermes
Profile and the Profile's scheduled jobs. The running server notices a disable
(``api.roster_watch``) and ends that Profile's logins and running turns, which
live only in the server process. Another Profile's work is untouched.

The command line is driven in-process through ``operator_cli.run`` against an
in-process server (``tests/_gfit_server.py``); ``roster_watch.check()`` is one
poll of the watcher, so the tests need no sleeping.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import types
from pathlib import Path

import pytest

import api.auth as auth
import api.routes as routes
from api import operator_cli, roster, roster_watch
from api.config import (
    ACTIVE_RUNS,
    ACTIVE_RUNS_LOCK,
    CANCEL_FLAGS,
    STREAMS,
    STREAMS_LOCK,
    register_stream_owner,
    unregister_stream_owner,
)
from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
NEWCOMER = "700001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", NEWCOMER: "Newcomer"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB]) as s:
        yield s


def cli(*argv) -> int:
    return operator_cli.run(list(argv))


@pytest.fixture
def fake_cron(monkeypatch):
    """Stand in for the Agent's ``cron.jobs``: jobs in ``$HERMES_HOME/cron/jobs.json``."""
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def _path():
        return Path(os.environ["HERMES_HOME"]) / "cron" / "jobs.json"

    def _jobs():
        return json.loads(_path().read_text()) if _path().exists() else []

    def _change(job_id, fields):
        jobs = _jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(fields)
                _path().write_text(json.dumps(jobs))
                return job
        return None

    cron_jobs.list_jobs = lambda include_disabled=False: [
        j for j in _jobs() if include_disabled or j.get("enabled", True)]
    cron_jobs.pause_job = lambda job_id, reason=None: _change(
        job_id, {"enabled": False, "state": "paused", "paused_reason": reason})
    cron_jobs.resume_job = lambda job_id: _change(
        job_id, {"enabled": True, "state": "scheduled", "paused_reason": None})
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setattr(routes, "_ensure_agent_cron_import_path", lambda: None)


def _store_jobs(srv, uid, *job_ids):
    cron = srv.profile_home(uid) / "cron"
    cron.mkdir(parents=True, exist_ok=True)
    (cron / "jobs.json").write_text(json.dumps([
        {"id": job_id, "name": job_id, "enabled": True, "state": "scheduled"} for job_id in job_ids
    ]))


def _enabled(srv, uid) -> dict:
    jobs = json.loads((srv.profile_home(uid) / "cron" / "jobs.json").read_text())
    return {job["id"]: job["enabled"] for job in jobs}


@pytest.fixture
def running():
    """Register a running turn for a session, as chat start does; clean up after."""
    stream_ids = []

    def start(session_id) -> str:
        stream_id = f"stream-{session_id}"
        with STREAMS_LOCK:
            STREAMS[stream_id] = queue.Queue()
            CANCEL_FLAGS[stream_id] = threading.Event()
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS[stream_id] = {"session_id": session_id, "phase": "running"}
        register_stream_owner(stream_id, session_id)
        stream_ids.append(stream_id)
        return stream_id

    yield start
    for stream_id in stream_ids:
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)
            CANCEL_FLAGS.pop(stream_id, None)
        with ACTIVE_RUNS_LOCK:
            ACTIVE_RUNS.pop(stream_id, None)
        unregister_stream_owner(stream_id)


def _new_session(client) -> str:
    status, payload, _ = client.post("/api/session/new", {})
    assert status == 200, payload
    return payload["session"]["session_id"]


def _logins(profile) -> int:
    return sum(1 for record in auth._sessions.values() if record.get("bound_profile") == profile)


# ── The command line ─────────────────────────────────────────────────────────

def test_create_makes_a_profile_its_user_can_log_in_to(srv, capsys):
    assert cli("create", NEWCOMER, "--display-name", "Somsri J.") == 0

    assert srv.profile_home(NEWCOMER).is_dir()
    view = roster.view(NEWCOMER)
    assert (view["status"], view["display_name"]) == ("active", "Somsri J.")
    assert NEWCOMER in capsys.readouterr().out
    srv.logged_in(NEWCOMER)


def test_create_refuses_an_existing_profile(srv, capsys):
    assert cli("create", ALICE) == 1
    assert "already exists" in capsys.readouterr().err


def test_list_shows_each_profile_with_its_status(srv, capsys):
    cli("disable", BOB)
    capsys.readouterr()

    assert cli("list") == 0

    lines = {line.split("\t")[0]: line.split("\t")[1] for line in capsys.readouterr().out.splitlines()}
    assert lines == {ALICE: "active", BOB: "disabled"}


def test_disable_refuses_login_and_pauses_only_that_profiles_jobs(srv, fake_cron):
    _store_jobs(srv, ALICE, "daily")
    _store_jobs(srv, BOB, "bob-daily")

    assert cli("disable", ALICE) == 0

    assert srv.client().login(ALICE)[0] == 403
    assert _enabled(srv, ALICE) == {"daily": False}
    assert _enabled(srv, BOB) == {"bob-daily": True}


def test_enable_lets_the_user_back_in_and_resumes_the_paused_jobs(srv, fake_cron):
    _store_jobs(srv, ALICE, "daily")
    cli("disable", ALICE)

    assert cli("enable", ALICE) == 0

    srv.logged_in(ALICE)
    assert _enabled(srv, ALICE) == {"daily": True}


def test_disable_or_enable_of_an_unknown_profile_is_refused(srv, capsys):
    assert cli("disable", NEWCOMER) == 1
    assert cli("enable", NEWCOMER) == 1
    assert "does not exist" in capsys.readouterr().err


def test_delete_refuses_an_active_profile(srv, capsys):
    assert cli("delete", ALICE, "--confirm", ALICE) == 1

    assert "disable it first" in capsys.readouterr().err
    assert srv.profile_home(ALICE).is_dir()


def test_delete_needs_the_name_repeated(srv):
    cli("disable", ALICE)
    assert cli("delete", ALICE, "--confirm", BOB) == 2
    assert srv.profile_home(ALICE).is_dir()


def test_delete_removes_a_disabled_profile_and_its_record(srv):
    cli("disable", ALICE)

    assert cli("delete", ALICE, "--confirm", ALICE) == 0

    assert not srv.profile_home(ALICE).exists()
    assert ALICE not in json.loads((srv.state / "gfit_roster.json").read_text())


def test_delete_refuses_when_the_roster_cannot_be_read(srv):
    # Unknown is not disabled: the Profile might still be active.
    (srv.state / "gfit_roster.json").write_text("{not json")
    assert cli("delete", ALICE, "--confirm", ALICE) == 1
    assert srv.profile_home(ALICE).is_dir()


# ── The server notices a disable ─────────────────────────────────────────────

def test_the_command_line_leaves_live_work_to_the_server(srv, running):
    # Logins and running turns live only in the server; another process must not touch them.
    alice = srv.logged_in(ALICE)
    stream = running(_new_session(alice))
    roster_watch.check()

    cli("disable", ALICE)

    assert _logins(ALICE) == 1
    assert not CANCEL_FLAGS[stream].is_set()


def test_the_server_stops_a_disabled_profiles_turn_and_logins_and_no_other(srv, running):
    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)
    alice_stream = running(_new_session(alice))
    bob_stream = running(_new_session(bob))
    alice_flag = CANCEL_FLAGS[alice_stream]
    roster_watch.check()
    cli("disable", ALICE)

    assert roster_watch.check() == [ALICE]

    assert alice_flag.is_set()
    assert _logins(ALICE) == 0
    with STREAMS_LOCK:
        assert alice_stream not in STREAMS
        assert bob_stream in STREAMS
        assert not CANCEL_FLAGS[bob_stream].is_set()
    assert _logins(BOB) == 1


def test_a_re_enabled_profile_does_not_revive_old_logins(srv):
    alice = srv.logged_in(ALICE)
    cli("disable", ALICE)
    roster_watch.check()

    cli("enable", ALICE)

    assert alice.get("/api/profile/active")[0] == 401
    srv.logged_in(ALICE)


def test_the_watcher_settles_only_when_the_roster_changes(srv):
    cli("disable", ALICE)
    assert roster_watch.check() == [ALICE]
    assert roster_watch.check() == []


def test_on_start_the_watcher_settles_profiles_disabled_while_it_was_down(srv):
    srv.logged_in(ALICE)
    assert cli("disable", ALICE) == 0  # the Operator disables Alice while the server is stopped
    roster_watch.reset()

    assert roster_watch.check() == [ALICE]
    assert _logins(ALICE) == 0


def test_an_unreadable_roster_settles_nothing_until_it_is_fixed(srv):
    srv.logged_in(BOB)
    roster_path = srv.state / "gfit_roster.json"
    roster_path.write_text("{not json")

    assert roster_watch.check() == []
    assert _logins(BOB) == 1

    roster_path.write_text(json.dumps({BOB: {"status": "disabled"}}))
    assert roster_watch.check() == [BOB]
    assert _logins(BOB) == 0


# ── Session-store maintenance (was the Admin's web routes) ───────────────────

@pytest.fixture
def session_dir(monkeypatch, tmp_path):
    import api.config as config

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "SESSION_DIR", sessions)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", sessions / "_index.json")
    return sessions


def test_sessions_cleanup_deletes_empty_untitled_sessions_and_keeps_the_rest(session_dir, capsys):
    from api.models import Session

    Session(session_id="emptyone1234", title="Untitled", messages=[]).save()
    Session(session_id="realone12345", title="Real", messages=[{"role": "user", "content": "hi"}]).save()
    capsys.readouterr()

    assert cli("sessions-cleanup") == 0

    assert json.loads(capsys.readouterr().out) == {"cleaned": 1}
    assert not (session_dir / "emptyone1234.json").exists()
    assert (session_dir / "realone12345.json").exists()


def test_sessions_audit_reports_without_changing_anything(session_dir, capsys):
    before = sorted(p.name for p in session_dir.iterdir())

    assert cli("sessions-audit") == 0

    assert isinstance(json.loads(capsys.readouterr().out), dict)
    assert sorted(p.name for p in session_dir.iterdir()) == before


def test_sessions_repair_on_a_clean_store_succeeds(session_dir, capsys):
    assert cli("sessions-repair") == 0
    assert json.loads(capsys.readouterr().out).get("clean") is True
