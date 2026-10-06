"""GFIT-CoWork Workspace policy, step (a): the four gaps, closed on today's code.

- Registering a Workspace with "create the folder" creates nothing outside a
  User's Workspace (ticket 01).
- A User can extract an archive into their session's attachments, which are
  outside Workspace confinement and guarded by session ownership (ticket 02).
- A User's worktree stays inside their Workspace (ticket 03).
- Rollback cannot tell a User whether a folder exists (ticket 04).

HTTP tests against an in-process server (see ``tests/_gfit_server.py``). The
Admin keeps today's behaviour in each case.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"

OUTSIDE_WORKSPACE_MESSAGE = "That path is outside your Workspace."


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB],
    ) as s:
        yield s


@pytest.fixture
def alice(srv):
    return srv.logged_in(ALICE)


def _workspace(srv, uid) -> Path:
    return (srv.profile_home(uid) / "workspace").resolve()


def _new_session(client, **body) -> str:
    status, payload, _ = client.post("/api/session/new", body)
    assert status == 200, payload
    return payload["session"]["session_id"]


def q(value) -> str:
    return quote(str(value), safe="")


# ── 01: Registering with "create the folder" ─────────────────────────────────

def _outside_targets(srv, tmp_path) -> dict[str, tuple[Path, Path]]:
    """Folders a User must not create: (target, its top-most new ancestor)."""
    return {
        "other-user": (srv.profile_home(BOB) / "planted" / "deep", srv.profile_home(BOB) / "planted"),
        "outside-every-profile": (tmp_path / "planted" / "deep", tmp_path / "planted"),
    }


@pytest.mark.parametrize("kind", ["other-user", "outside-every-profile"])
def test_registering_with_create_outside_creates_nothing(srv, alice, tmp_path, kind):
    target, top = _outside_targets(srv, tmp_path)[kind]

    status, body, _ = alice.post("/api/workspaces/add", {"path": str(target), "create": True})

    assert status == 400, body
    assert body["error"] == OUTSIDE_WORKSPACE_MESSAGE
    assert not top.exists()


def test_registering_with_create_inside_the_workspace_works(srv, alice):
    sub = _workspace(srv, ALICE) / "new-project" / "src"

    status, body, _ = alice.post("/api/workspaces/add", {"path": str(sub), "create": True})

    assert status == 200, body
    assert sub.is_dir()
    assert str(sub) in {w["path"] for w in body["workspaces"]}


# ── 02: Extracting an archive into the session's attachments ────────────────

@pytest.fixture
def attachments(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "attachments"
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(root))
    return root


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _extract(client, sid, archive: bytes, name="pack.zip"):
    return client.post_file("/api/upload/extract", {"session_id": sid}, name, archive)


def test_user_can_extract_an_archive_into_their_session(alice, attachments):
    sid = _new_session(alice)

    status, body, _ = _extract(alice, sid, _zip({"a.txt": "one", "docs/b.txt": "two"}))

    assert status == 200, body
    assert sorted(body["files"]) == ["pack/a.txt", "pack/docs/b.txt"]
    session_dir = attachments / sid
    assert (session_dir / "pack" / "a.txt").read_text() == "one"
    assert (session_dir / "pack" / "docs" / "b.txt").read_text() == "two"


def test_an_archive_entry_with_dotdot_stays_inside_the_destination(alice, attachments, tmp_path):
    sid = _new_session(alice)

    status, body, _ = _extract(alice, sid, _zip({"ok.txt": "fine", "../../../escaped.txt": "evil"}))

    assert status == 400, body
    assert not list(tmp_path.rglob("escaped.txt"))


def test_extracting_into_another_users_session_is_not_found(srv, alice, attachments):
    bob_sid = _new_session(srv.logged_in(BOB))

    status, body, _ = _extract(alice, bob_sid, _zip({"a.txt": "one"}))

    assert status == 404, body
    assert not (attachments / bob_sid).exists()


# ── 03: A User's worktree stays inside their Workspace ──────────────────────

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not available")


@pytest.fixture
def agent_worktrees(monkeypatch, tmp_path):
    """Stand in for Hermes Agent's worktree helper; record each repository it is asked about.

    Like the agent, it places the worktree in ``<repo>/.worktrees``.
    """
    import api.worktrees as worktrees

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    calls: list[Path] = []
    place = {"at": None}

    def fake_setup(repo_root):
        calls.append(Path(repo_root))
        path = place["at"] or Path(repo_root) / ".worktrees" / "hermes-test"
        _git(Path(repo_root), "worktree", "add", "-q", "-b", "hermes/hermes-test", str(path))
        _git(Path(repo_root), "worktree", "lock", str(path))
        return {"path": str(path), "branch": "hermes/hermes-test", "repo_root": repo_root}

    monkeypatch.setattr(worktrees, "_setup_agent_worktree", fake_setup)
    return calls, place


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "init")


@needs_git
def test_explicit_worktree_is_refused_when_the_repository_is_outside_the_workspace(
    srv, alice, agent_worktrees
):
    calls, _ = agent_worktrees
    _git_init(srv.profile_home(ALICE))  # the Workspace sits inside a larger repository

    status, body, _ = alice.post("/api/session/new", {"worktree": True})

    assert status == 400, body
    assert "worktree" in body["error"].lower() and "Workspace" in body["error"]
    assert calls == []
    assert not list(srv.profile_home(ALICE).rglob(".worktrees"))


@needs_git
def test_configured_worktree_falls_back_to_no_worktree_outside_the_workspace(
    srv, alice, agent_worktrees, monkeypatch
):
    import api.routes as routes

    calls, _ = agent_worktrees
    _git_init(srv.profile_home(ALICE))
    monkeypatch.setattr(routes, "_worktree_default_from_config", lambda profile: True)

    status, body, _ = alice.post("/api/session/new", {})

    assert status == 200, body
    session = body["session"]
    assert Path(session["workspace"]).resolve() == _workspace(srv, ALICE)
    assert not session.get("worktree_path")
    assert calls == []


@needs_git
def test_worktree_inside_a_workspace_that_is_its_own_repository(srv, alice, agent_worktrees):
    calls, _ = agent_worktrees
    _git_init(_workspace(srv, ALICE))

    status, body, _ = alice.post("/api/session/new", {"worktree": True})

    assert status == 200, body
    assert calls == [_workspace(srv, ALICE)]
    worktree = Path(body["session"]["workspace"]).resolve()
    assert worktree == _workspace(srv, ALICE) / ".worktrees" / "hermes-test"
    assert Path(body["session"]["worktree_path"]).resolve() == worktree


@needs_git
def test_a_worktree_the_agent_places_outside_the_workspace_is_refused(
    srv, alice, agent_worktrees, tmp_path
):
    _, place = agent_worktrees
    repo = _workspace(srv, ALICE)
    _git_init(repo)
    place["at"] = tmp_path / "elsewhere" / "hermes-test"

    status, body, _ = alice.post("/api/session/new", {"worktree": True})

    assert status == 400, body
    status, body, _ = alice.get("/api/sessions")
    assert all(not s.get("worktree_path") for s in body.get("sessions", []))
    # The worktree the agent made outside is discarded, with its branch.
    assert not place["at"].exists()
    assert "elsewhere" not in _git(repo, "worktree", "list")
    assert _git(repo, "branch", "--list", "hermes/hermes-test") == ""


# ── 04: Rollback cannot probe the server's folders ──────────────────────────

ROLLBACK_ROUTES = [
    ("GET", "/api/rollback/list?workspace={ws}"),
    ("GET", "/api/rollback/diff?workspace={ws}&checkpoint=abc123"),
    ("POST", "/api/rollback/restore"),
]


def _rollback(client, method, route, ws):
    if method == "GET":
        return client.get(route.format(ws=q(ws)))
    return client.post(route, {"workspace": str(ws), "checkpoint": "abc123"})


@pytest.mark.parametrize("method,route", ROLLBACK_ROUTES)
def test_rollback_answers_the_same_for_existing_and_missing_outside_folders(
    alice, tmp_path, method, route
):
    existing = tmp_path / "outside"
    existing.mkdir()
    missing = tmp_path / "no-such-folder"

    answers = []
    for path in (existing, missing):
        status, body, _ = _rollback(alice, method, route, path)
        answers.append((status, str(body).replace(str(path), "<path>")))

    assert answers[0] == answers[1]
    assert answers[0][0] == 400


def test_rollback_lists_checkpoints_for_the_users_own_workspace(srv, alice):
    status, body, _ = alice.get(f"/api/rollback/list?workspace={q(_workspace(srv, ALICE))}")

    assert status == 200, body
    assert body["checkpoints"] == []
