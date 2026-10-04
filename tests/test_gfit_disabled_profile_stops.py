"""GFIT-CoWork: disabling a Profile stops its work, not only its logins.

Architecture review round 5, candidate 13. When the Admin disables (or
deletes) a Profile, its running turns stop through the Stop path and its
scheduled jobs pause; re-enabling resumes exactly the jobs the disable paused.
Another Profile's work is untouched.

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Agent's ``cron.jobs`` is stood in for by a module that keeps jobs in
``$HERMES_HOME/cron/jobs.json``, as the Agent does.
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

import api.routes as routes
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
ADMIN = "600001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


@pytest.fixture
def admin(srv):
    return srv.logged_in(ADMIN)


# ── Scheduled jobs ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_cron(monkeypatch):
    """Stand in for the Agent's ``cron.jobs``: pause and resume as the Agent does."""
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    calls = {"fail": False}

    def _path():
        return Path(os.environ["HERMES_HOME"]) / "cron" / "jobs.json"

    def _jobs():
        if calls["fail"]:
            raise OSError("cron store unreadable")
        return json.loads(_path().read_text()) if _path().exists() else []

    def _change(job_id, fields):
        jobs = _jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(fields)
                _path().write_text(json.dumps(jobs))
                return job
        return None

    def list_jobs(include_disabled=False):
        jobs = _jobs()
        return jobs if include_disabled else [j for j in jobs if j.get("enabled", True)]

    def pause_job(job_id, reason=None):
        return _change(job_id, {"enabled": False, "state": "paused", "paused_reason": reason})

    def resume_job(job_id):
        return _change(job_id, {"enabled": True, "state": "scheduled", "paused_reason": None})

    cron_jobs.list_jobs = list_jobs
    cron_jobs.pause_job = pause_job
    cron_jobs.resume_job = resume_job
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setattr(routes, "_ensure_agent_cron_import_path", lambda: None)
    return calls


def _store_jobs(srv, uid, *jobs):
    cron = srv.profile_home(uid) / "cron"
    cron.mkdir(parents=True, exist_ok=True)
    (cron / "jobs.json").write_text(json.dumps([
        {"name": f"job {job['id']}", "prompt": "report", "schedule": "every 1h",
         "enabled": True, "state": "scheduled", **job}
        for job in jobs
    ]))


def _jobs(srv, uid) -> dict:
    jobs = json.loads((srv.profile_home(uid) / "cron" / "jobs.json").read_text())
    return {job["id"]: job for job in jobs}


def _enabled(srv, uid) -> dict:
    return {job_id: job["enabled"] for job_id, job in _jobs(srv, uid).items()}


def test_disabling_a_profile_pauses_its_scheduled_jobs(srv, admin, fake_cron):
    _store_jobs(srv, ALICE, {"id": "daily"}, {"id": "hourly"},
                {"id": "own-pause", "enabled": False, "state": "paused", "paused_reason": "on leave"})
    _store_jobs(srv, BOB, {"id": "bob-daily"})

    status, body, _ = admin.post("/api/profile/disable", {"name": ALICE})

    assert status == 200, body
    assert _enabled(srv, ALICE) == {"daily": False, "hourly": False, "own-pause": False}
    assert _jobs(srv, ALICE)["own-pause"]["paused_reason"] == "on leave"
    assert _enabled(srv, BOB) == {"bob-daily": True}


def test_re_enabling_resumes_only_the_jobs_the_disable_paused(srv, admin, fake_cron):
    _store_jobs(srv, ALICE, {"id": "daily"}, {"id": "hourly"},
                {"id": "own-pause", "enabled": False, "state": "paused", "paused_reason": "on leave"})
    admin.post("/api/profile/disable", {"name": ALICE})

    status, body, _ = admin.post("/api/profile/enable", {"name": ALICE})

    assert status == 200, body
    assert _enabled(srv, ALICE) == {"daily": True, "hourly": True, "own-pause": False}
    # A second disable and enable pauses and resumes them again.
    admin.post("/api/profile/disable", {"name": ALICE})
    admin.post("/api/profile/enable", {"name": ALICE})
    assert _enabled(srv, ALICE) == {"daily": True, "hourly": True, "own-pause": False}


def test_a_job_paused_by_hand_while_disabled_stays_paused(srv, admin, fake_cron):
    # A job someone paused for their own reason after the disable is not the disable's to resume.
    _store_jobs(srv, ALICE, {"id": "daily"})
    admin.post("/api/profile/disable", {"name": ALICE})
    jobs = list(_jobs(srv, ALICE).values())
    jobs[0]["paused_reason"] = "paused by hand"
    (srv.profile_home(ALICE) / "cron" / "jobs.json").write_text(json.dumps(jobs))

    admin.post("/api/profile/enable", {"name": ALICE})

    assert _enabled(srv, ALICE) == {"daily": False}


def test_a_cron_store_that_cannot_be_read_does_not_stop_the_disable(srv, admin, fake_cron):
    _store_jobs(srv, ALICE, {"id": "daily"})
    fake_cron["fail"] = True

    status, body, _ = admin.post("/api/profile/disable", {"name": ALICE})

    assert status == 200, body
    assert body["profile"]["status"] == "disabled"
    status, _, _ = srv.client().login(ALICE)
    assert status == 403


def test_deleting_a_profile_pauses_its_jobs_first(srv, admin, fake_cron, monkeypatch):
    # A deletion that cannot finish leaves the Profile shut, and its jobs paused.
    from api import profiles

    _store_jobs(srv, ALICE, {"id": "daily"})

    def busy(*_args, **_kwargs):
        raise RuntimeError("Profile is busy")

    monkeypatch.setattr(profiles, "delete_profile_api", busy)
    status, body, _ = admin.post("/api/profile/delete", {"name": ALICE, "confirm": ALICE})

    assert status != 200, body
    assert _enabled(srv, ALICE) == {"daily": False}


# ── Running turns ────────────────────────────────────────────────────────────

def _new_session(client) -> str:
    status, payload, _ = client.post("/api/session/new", {})
    assert status == 200, payload
    return payload["session"]["session_id"]


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


def test_disabling_a_profile_stops_its_running_turn_and_no_other(srv, admin, running):
    alice_stream = running(_new_session(srv.logged_in(ALICE)))
    bob_stream = running(_new_session(srv.logged_in(BOB)))
    alice_flag = CANCEL_FLAGS[alice_stream]

    status, body, _ = admin.post("/api/profile/disable", {"name": ALICE})

    assert status == 200, body
    assert alice_flag.is_set()
    with STREAMS_LOCK:
        assert alice_stream not in STREAMS
        assert bob_stream in STREAMS
        assert not CANCEL_FLAGS[bob_stream].is_set()
