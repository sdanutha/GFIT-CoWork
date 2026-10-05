"""GFIT-CoWork cross-Profile reads, step (a): the gaps, closed on today's code.

- A User's insights count only the sessions they own (ticket 01).
- A User's cron job works only in a folder the Workspace policy lets them use
  (ticket 03).
- A User's cron status shows only their own running jobs (ticket 04).
- A User's project dashboard reads only inside their Workspace (ticket 02).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Admin keeps today's behaviour in each case.
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

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
    monkeypatch.setattr("api.config.SESSION_DIR", session_dir)
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

    # The Admin stays in default and never works in a User's Profile (ADR 0004):
    # importing into Bob's Profile is refused too (request-profile review).
    status, payload, _ = srv.logged_in(ADMIN).post(
        "/api/session/import_cli", {"session_id": sid, "all_profiles": True, "profile": BOB},
    )
    assert status == 403, payload
    assert Session.load(sid) is None


# ── Ticket 03: a cron job's working folder ──────────────────────────────────

@pytest.fixture
def fake_cron(monkeypatch):
    """Stand in for the Agent's ``cron.jobs``: jobs live in $HERMES_HOME/cron/jobs.json.

    ``update_job`` merges the updates into the stored job, as the Agent does.
    """
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def _path():
        return Path(os.environ["HERMES_HOME"]) / "cron" / "jobs.json"

    def list_jobs(include_disabled=True):
        return json.loads(_path().read_text()) if _path().exists() else []

    def update_job(job_id, updates):
        jobs = list_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(updates)
                _path().write_text(json.dumps(jobs))
                return job
        return None

    cron_jobs.list_jobs = list_jobs
    cron_jobs.update_job = update_job
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setattr(routes, "_ensure_agent_cron_import_path", lambda: None)


def _cron_job(srv, uid, job_id, **fields) -> dict:
    cron = srv.profile_home(uid) / "cron"
    cron.mkdir(parents=True, exist_ok=True)
    jobs_file = cron / "jobs.json"
    jobs = json.loads(jobs_file.read_text()) if jobs_file.exists() else []
    job = {"id": job_id, "name": f"job of {uid}", "prompt": "report", "schedule": "every 1h", **fields}
    jobs.append(job)
    jobs_file.write_text(json.dumps(jobs))
    return job


def _stored_job(srv, uid, job_id) -> dict:
    jobs = json.loads((srv.profile_home(uid) / "cron" / "jobs.json").read_text())
    return next(job for job in jobs if job["id"] == job_id)


@pytest.fixture
def workspaces(srv, tmp_path):
    folders = {
        "alice": srv.profile_home(ALICE) / "workspace" / "project",
        "bob": srv.profile_home(BOB) / "workspace" / "project",
        "outside": tmp_path / "outside-every-profile",
    }
    for folder in folders.values():
        folder.mkdir(parents=True)
    return folders


@pytest.mark.parametrize("where", ["bob", "outside"])
def test_a_user_cannot_set_a_cron_working_folder_outside_their_workspace(srv, fake_cron, workspaces, where):
    before = _cron_job(srv, ALICE, "alice-job", workdir=str(workspaces["alice"]))
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/crons/update", {
        "job_id": "alice-job", "workdir": str(workspaces[where]), "name": "renamed",
    })

    assert status == 400, body
    assert body["error"] == "That path is outside your Workspace."
    assert _stored_job(srv, ALICE, "alice-job") == before


def test_a_user_sets_and_clears_a_cron_working_folder_inside_their_workspace(srv, fake_cron, workspaces):
    _cron_job(srv, ALICE, "alice-job")
    alice = srv.logged_in(ALICE)

    status, body, _ = alice.post("/api/crons/update", {"job_id": "alice-job", "workdir": str(workspaces["alice"])})
    assert status == 200, body
    assert _stored_job(srv, ALICE, "alice-job")["workdir"] == str(workspaces["alice"])

    status, body, _ = alice.post("/api/crons/update", {"job_id": "alice-job", "workdir": ""})
    assert status == 200, body


def test_the_admin_sets_any_cron_working_folder(srv, fake_cron, workspaces):
    admin_home = srv.hermes_home  # the Admin's request runs in the root Profile
    (admin_home / "cron").mkdir(parents=True, exist_ok=True)
    (admin_home / "cron" / "jobs.json").write_text(json.dumps([{"id": "admin-job", "name": "a"}]))

    status, body, _ = srv.logged_in(ADMIN).post(
        "/api/crons/update", {"job_id": "admin-job", "workdir": str(workspaces["outside"])},
    )

    assert status == 200, body
    stored = json.loads((admin_home / "cron" / "jobs.json").read_text())[0]
    assert stored["workdir"] == str(workspaces["outside"])


# ── Ticket 04: cron status ───────────────────────────────────────────────────

@pytest.fixture
def running_jobs(srv, fake_cron):
    """Alice's and Bob's jobs, both running, marked through the record a real run uses."""
    ids = {ALICE: "alice-running", BOB: "bob-running"}
    for uid, job_id in ids.items():
        _cron_job(srv, uid, job_id)
        routes._mark_cron_running(job_id)
    yield ids
    for job_id in ids.values():
        routes._mark_cron_done(job_id)


def _status(client, query="") -> dict:
    status, body, _ = client.get(f"/api/crons/status{query}")
    assert status == 200, body
    return body


def test_a_users_cron_status_shows_only_their_own_running_jobs(srv, running_jobs):
    alice = srv.logged_in(ALICE)

    running = _status(alice)["running"]
    assert running_jobs[ALICE] in running
    assert running_jobs[BOB] not in running

    own = _status(alice, f"?job_id={running_jobs[ALICE]}")
    assert own["running"] is True
    bobs = _status(alice, f"?job_id={running_jobs[BOB]}")
    unknown = _status(alice, "?job_id=no-such-job")
    assert bobs == {**unknown, "job_id": running_jobs[BOB]}
    assert bobs["running"] is False


def test_the_admins_cron_status_shows_every_running_job(srv, running_jobs):
    admin = srv.logged_in(ADMIN)

    running = _status(admin)["running"]
    assert {running_jobs[ALICE], running_jobs[BOB]} <= set(running)
    assert _status(admin, f"?job_id={running_jobs[BOB]}")["running"] is True


# ── Step (b), ticket 07: projects and cron ask the question ──────────────────

def _project(client, name) -> dict:
    status, body, _ = client.post("/api/projects/create", {"name": name})
    assert status == 200, body
    return body["project"]


def _project_ids(client, path) -> tuple[set, dict]:
    status, body, _ = client.get(path)
    assert status == 200, body
    return {p["project_id"] for p in body["projects"]}, body


def test_a_users_project_list_shows_only_their_own_projects(srv):
    clients = {uid: srv.logged_in(uid) for uid in (ALICE, BOB, ADMIN)}
    projects = {uid: _project(c, f"project of {uid}")["project_id"] for uid, c in clients.items()}
    others = {projects[BOB], projects[ADMIN]}

    for path in ("/api/projects", "/api/projects?all_profiles=1"):
        ids, body = _project_ids(clients[ALICE], path)
        assert projects[ALICE] in ids and not ids & others
        assert body["all_profiles"] is False
        assert body["other_profile_count"] == 0

    ids, body = _project_ids(clients[ADMIN], "/api/projects")
    assert projects[ADMIN] in ids and not ids & {projects[ALICE], projects[BOB]}
    assert body["other_profile_count"] >= 2
    ids, body = _project_ids(clients[ADMIN], "/api/projects?all_profiles=1")
    assert set(projects.values()) <= ids
    assert body["all_profiles"] is True


def test_another_profiles_project_is_not_found(srv):
    from api.models import load_projects

    alice, bob = srv.logged_in(ALICE), srv.logged_in(BOB)
    bobs = _project(bob, "Bob's plan")
    missing = alice.post("/api/projects/rename", {"project_id": "no-such-project", "name": "x"})[:2]

    assert alice.post("/api/projects/rename", {"project_id": bobs["project_id"], "name": "pwned"})[:2] == missing
    assert alice.post("/api/projects/delete", {"project_id": bobs["project_id"]})[:2] == (
        alice.post("/api/projects/delete", {"project_id": "no-such-project"})[:2]
    )
    stored = next(p for p in load_projects() if p["project_id"] == bobs["project_id"])
    assert stored["name"] == "Bob's plan"


@pytest.mark.parametrize("profile", ["default", BOB])
def test_a_user_cannot_point_a_cron_job_at_another_profile(srv, fake_cron, profile):
    before = _cron_job(srv, ALICE, "alice-job")

    status, body, _ = srv.logged_in(ALICE).post("/api/crons/update", {"job_id": "alice-job", "profile": profile})

    # The Bound guard refuses a body naming another Profile before the route
    # runs (403); the cron Profile picker would refuse it too (400).
    assert status in (400, 403), body
    assert _stored_job(srv, ALICE, "alice-job") == before


def test_the_admin_points_a_cron_job_only_at_default(srv, fake_cron):
    """The Admin stays in default and never works in a User's Profile (ADR 0004)."""
    (srv.hermes_home / "cron").mkdir(parents=True, exist_ok=True)
    (srv.hermes_home / "cron" / "jobs.json").write_text(json.dumps([{"id": "admin-job", "name": "a"}]))
    admin = srv.logged_in(ADMIN)

    status, body, _ = admin.post("/api/crons/update", {"job_id": "admin-job", "profile": "default"})
    assert status == 200, body
    status, body, _ = admin.post("/api/crons/update", {"job_id": "admin-job", "profile": BOB})
    assert status == 403, body


@pytest.mark.parametrize("profile", ["default", BOB])
def test_a_users_job_set_to_another_profile_runs_in_their_own(srv, fake_cron, monkeypatch, profile):
    """Jobs stored before the picker was scoped keep running in the User's own Profile."""
    runs = []
    monkeypatch.setattr(routes, "_run_cron_tracked", lambda *args: runs.append(args))
    monkeypatch.setattr(sys.modules["cron.jobs"], "get_job", lambda job_id: _stored_job(srv, ALICE, job_id), raising=False)
    _cron_job(srv, ALICE, f"alice-{profile}-job", profile=profile)

    status, body, _ = srv.logged_in(ALICE).post("/api/crons/run", {"job_id": f"alice-{profile}-job"})
    routes._mark_cron_done(f"alice-{profile}-job")

    assert status == 200, body
    (_job, _home, execution_home, _event_profile), = runs
    assert Path(execution_home).resolve() == srv.profile_home(ALICE).resolve()


# ── Ticket 02: the project dashboard ─────────────────────────────────────────

def _project_folder(path: Path, secret: str, repo_root: Path | None = None) -> Path:
    docs = path / "docs" / "project-os"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "PROJECT.md").write_text(f"# Project\n{secret}\n")
    (path / "PLAN.md").write_text(f"{secret} plan\n")
    if repo_root is not None:
        status = path / ".ax" / "status"
        status.mkdir(parents=True, exist_ok=True)
        (status / "active.json").write_text(json.dumps({"repo_root": str(repo_root)}))
    return path


@pytest.fixture
def boards(srv, tmp_path):
    """Project folders in Bob's Workspace and outside any Workspace, for boards to point at."""
    return {
        "bob": _project_folder(srv.profile_home(BOB) / "workspace" / "bobs-repo", "BOB-SECRET"),
        "outside": _project_folder(tmp_path / "outside-repo", "SERVER-SECRET"),
    }


def _dashboard(client, board="") -> dict:
    status, body, _ = client.get(f"/api/project-os/dashboard?board={board}" if board else "/api/project-os/dashboard")
    assert status == 200, body
    return body


def _without_board(body: dict) -> dict:
    return {k: v for k, v in body.items() if k not in ("selected_board_slug", "goal_summary", "board")}


@pytest.mark.parametrize("board", ["bobs-board", "outside-board"])
def test_a_users_dashboard_never_reads_a_board_folder_outside_their_workspace(srv, boards, board):
    alice = srv.logged_in(ALICE)

    body = _dashboard(alice, board)

    text = json.dumps(body)
    assert "BOB-SECRET" not in text and "SERVER-SECRET" not in text
    assert str(boards["bob"]) not in text and str(boards["outside"]) not in text
    assert _without_board(body) == _without_board(_dashboard(alice, "no-folder-board"))


def test_a_repository_path_inside_a_users_project_is_followed_only_inside_their_workspace(srv, boards):
    # Alice's dashboard reads her last-used Workspace: her Workspace folder.
    own = srv.profile_home(ALICE) / "workspace"
    _project_folder(own, "ALICE-PLAN", repo_root=boards["bob"])
    alice = srv.logged_in(ALICE)

    body = _dashboard(alice)

    # Alice's own status file is echoed back as written (it names Bob's
    # folder); what matters is that the dashboard did not go there.
    text = json.dumps(body)
    assert "BOB-SECRET" not in text
    assert Path(body["repo_root"]).resolve() == own.resolve()
    assert Path(body["workspace"]).resolve() == own.resolve()
    assert "ALICE-PLAN" in text


# ── Step (b), ticket 09: per-Profile views stay on the User's own Profile ────

def test_a_users_saved_prompts_are_their_own(srv):
    for uid in (ALICE, BOB):
        webui = srv.profile_home(uid) / "webui"
        webui.mkdir(parents=True, exist_ok=True)
        (webui / "saved_prompts.json").write_text(json.dumps([{"name": f"prompt of {uid}", "text": uid}]))

    status, body, _ = srv.logged_in(ALICE).get("/api/prompts")

    assert status == 200, body
    assert [p["name"] for p in body["prompts"]] == [f"prompt of {ALICE}"]


@pytest.mark.parametrize("path", [
    "/api/notes/sources", "/api/wiki/status", "/api/health/agent", "/api/gateway/status",
])
def test_a_users_per_profile_views_answer_from_their_own_profile(srv, path):
    status, body, _ = srv.logged_in(ALICE).get(path)

    assert status != 500, body
    assert BOB not in json.dumps(body)


# ── Review fixes: Upstream's isolated profile mode keeps today's behaviour ───

@pytest.fixture
def isolated_mode(monkeypatch, tmp_path):
    """Upstream's posture, login off: this process serves Bob's Profile only."""
    import api.profiles as profiles

    hermes = tmp_path / "hermes-isolated"
    for uid in (ALICE, BOB):
        (hermes / "profiles" / uid).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes / "profiles" / BOB))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes)
    monkeypatch.setattr(profiles, "_INITIAL_ISOLATED_PROFILE_OPT_IN", "1")
    monkeypatch.setattr(profiles, "_INITIAL_HERMES_HOME", str(hermes / "profiles" / BOB))
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()
    yield hermes
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()


class _Handler:
    def __init__(self):
        import io
        self.status, self.wfile, self.headers = None, io.BytesIO(), {}

    def send_response(self, status):
        self.status = status

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue())


def test_isolated_mode_insights_still_count_every_session_in_the_index(isolated_mode, usage_index):
    handler = _Handler()
    routes._handle_insights(handler, types.SimpleNamespace(query="days=7"))

    assert handler.body()["total_sessions"] == 3


def test_isolated_mode_cron_status_still_shows_every_running_job(isolated_mode):
    for job_id in ("iso-a", "iso-b"):
        routes._mark_cron_running(job_id)
    try:
        handler = _Handler()
        routes._handle_cron_status(handler, types.SimpleNamespace(query=""))
        assert {"iso-a", "iso-b"} <= set(handler.body()["running"])
    finally:
        for job_id in ("iso-a", "iso-b"):
            routes._mark_cron_done(job_id)


def test_isolated_mode_cron_picker_still_offers_default(isolated_mode):
    assert "default" in routes._available_cron_profile_names()


# ── Review fixes: a User imports their own CLI session with all Profiles ─────

def test_a_user_imports_their_own_cli_session_with_all_profiles(srv):
    import uuid

    sid = _cli_session_in(srv, ALICE, f"alice_cli_{uuid.uuid4().hex[:10]}")

    status, payload, _ = srv.logged_in(ALICE).post(
        "/api/session/import_cli", {"session_id": sid, "all_profiles": True, "profile": ALICE},
    )

    assert status == 200, payload
    assert payload["session"]["profile"] == ALICE


# ── Review fixes: a refused Profile-home lookup is "not found", not a 500 ────

def test_a_route_that_looks_up_another_profiles_home_answers_not_found(srv, monkeypatch):
    import api.profiles as profiles

    real_get = routes.handle_get

    def handle_get(handler, parsed):
        if parsed.path == "/api/prompts":  # a User route, made to look up Bob's home
            profiles.get_hermes_home_for_profile(BOB)
            return routes.j(handler, {"ok": True})
        return real_get(handler, parsed)

    import server
    monkeypatch.setattr(server, "handle_get", handle_get)

    status, body, _ = srv.logged_in(ALICE).get("/api/prompts")

    assert status == 404, body


# ── Review fixes: dashboard candidates are checked before they are read ─────

def test_a_board_claimed_by_a_folder_outside_the_workspace_keeps_the_users_own(srv, boards):
    own = _project_folder(srv.profile_home(ALICE) / "workspace", "ALICE-PLAN")
    status_dir = boards["bob"] / ".ax" / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "active.json").write_text(json.dumps({"board": "shared-board"}))
    (own / "link-to-bob").symlink_to(boards["bob"], target_is_directory=True)

    body = _dashboard(srv.logged_in(ALICE), "shared-board")

    assert Path(body["repo_root"]).resolve() == own.resolve()
    assert "BOB-SECRET" not in json.dumps(body)


def test_the_cron_working_folder_stored_is_the_one_checked(srv, fake_cron, workspaces):
    _cron_job(srv, ALICE, "alice-job")
    link = srv.profile_home(ALICE) / "workspace" / "link-to-project"
    link.symlink_to(workspaces["alice"], target_is_directory=True)

    status, body, _ = srv.logged_in(ALICE).post("/api/crons/update", {"job_id": "alice-job", "workdir": str(link)})

    assert status == 200, body
    assert _stored_job(srv, ALICE, "alice-job")["workdir"] == str(workspaces["alice"].resolve())


def test_a_routes_bad_input_handling_does_not_turn_the_refusal_into_a_400(srv, monkeypatch):
    import api.profiles as profiles
    import server

    real_get = routes.handle_get

    def handle_get(handler, parsed):
        if parsed.path == "/api/prompts":  # a User route that treats ValueError as bad input
            try:
                profiles.get_hermes_home_for_profile(BOB)
            except ValueError as exc:
                return routes.bad(handler, str(exc), 400)
            return routes.j(handler, {"ok": True})
        return real_get(handler, parsed)

    monkeypatch.setattr(server, "handle_get", handle_get)

    status, body, _ = srv.logged_in(ALICE).get("/api/prompts")

    assert status == 404, body
    assert BOB not in json.dumps(body)
