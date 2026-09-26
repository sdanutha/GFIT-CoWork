"""GFIT-CoWork ticket 05: a Member's files are confined to their Profile.

A Member's Workspaces live in ``<Profile>/workspace``. Registering a Workspace
elsewhere is refused, and every file API refuses a path that resolves outside
it, after ``..`` and symlinks are resolved. The Admin is not confined.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"

REFUSED = (400, 403, 404)
SECRET = "top secret outside the Profile"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


@pytest.fixture
def outside(tmp_path) -> Path:
    """A folder on the server outside every Profile, holding a secret."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "secret.txt").write_text(SECRET)
    return d


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


# ── The default Workspace ────────────────────────────────────────────────────

def test_first_login_creates_a_default_workspace_inside_the_profile(srv):
    assert not _workspace(srv, ALICE).exists()
    alice = srv.logged_in(ALICE)
    assert _workspace(srv, ALICE).is_dir()

    status, body, _ = alice.get("/api/workspaces")
    assert status == 200, body
    paths = [Path(w["path"]).resolve() for w in body["workspaces"]]
    assert paths == [_workspace(srv, ALICE)]


def test_a_new_session_starts_in_the_default_workspace(srv, alice):
    sid = _new_session(alice)
    status, body, _ = alice.get(f"/api/session?session_id={sid}")
    assert Path(body["session"]["workspace"]).resolve() == _workspace(srv, ALICE)


def test_profile_active_reports_the_default_workspace(srv, alice):
    status, body, _ = alice.get("/api/profile/active")
    assert Path(body["default_workspace"]).resolve() == _workspace(srv, ALICE)


# ── Registering Workspaces ───────────────────────────────────────────────────

def test_member_can_register_a_sub_folder_inside_the_profile(srv, alice):
    sub = _workspace(srv, ALICE) / "project-x"
    sub.mkdir()
    status, body, _ = alice.post("/api/workspaces/add", {"path": str(sub)})
    assert status == 200, body
    assert str(sub) in {w["path"] for w in body["workspaces"]}

    sid = _new_session(alice, workspace=str(sub))
    status, body, _ = alice.get(f"/api/session?session_id={sid}")
    assert Path(body["session"]["workspace"]).resolve() == sub


def _escapes(srv, outside) -> dict[str, Path]:
    ws = _workspace(srv, ALICE)
    link = ws / "link-out"
    link.symlink_to(outside, target_is_directory=True)
    (_workspace(srv, BOB)).mkdir(parents=True, exist_ok=True)
    return {
        "outside": outside,
        "dotdot": ws / ".." / "..",
        "profile-home": srv.profile_home(ALICE),
        "symlink": link,
        "other-member": _workspace(srv, BOB),
    }


@pytest.mark.parametrize("kind", ["outside", "dotdot", "profile-home", "symlink", "other-member"])
def test_registering_a_workspace_outside_the_profile_is_refused(srv, alice, outside, kind):
    target = _escapes(srv, outside)[kind]
    status, body, _ = alice.post("/api/workspaces/add", {"path": str(target)})
    assert status in REFUSED, body
    status, body, _ = alice.get("/api/workspaces")
    assert all(
        Path(w["path"]).resolve().is_relative_to(_workspace(srv, ALICE)) for w in body["workspaces"]
    )


@pytest.mark.parametrize("kind", ["outside", "dotdot", "symlink", "other-member"])
def test_a_session_cannot_be_pointed_outside_the_profile(srv, alice, outside, kind):
    target = _escapes(srv, outside)[kind]
    status, body, _ = alice.post("/api/session/new", {"workspace": str(target)})
    assert status in REFUSED, body

    sid = _new_session(alice)
    status, body, _ = alice.post("/api/session/update", {"session_id": sid, "workspace": str(target)})
    assert status in REFUSED, body


# ── File APIs ────────────────────────────────────────────────────────────────

@pytest.fixture
def session(srv, alice, outside):
    """Alice's session in her default Workspace, with a symlink pointing out of it."""
    ws = _workspace(srv, ALICE)
    (ws / "notes.txt").write_text("mine")
    (ws / "link-out").symlink_to(outside, target_is_directory=True)
    (ws / "file-link").symlink_to(outside / "secret.txt")
    (_workspace(srv, BOB)).mkdir(parents=True, exist_ok=True)
    (_workspace(srv, BOB) / "bob.txt").write_text(SECRET)
    return _new_session(alice)


ESCAPING_PATHS = [
    "../../../outside/secret.txt",
    "link-out/secret.txt",
    "file-link",
    f"../../{BOB}/workspace/bob.txt",
]


def test_a_file_inside_the_workspace_can_be_read(alice, session):
    status, body, _ = alice.get(f"/api/file?session_id={session}&path=notes.txt")
    assert status == 200, body
    assert "mine" in str(body)


@pytest.mark.parametrize("path", ESCAPING_PATHS)
@pytest.mark.parametrize("endpoint", ["/api/file", "/api/file/raw", "/api/folder/download"])
def test_reading_outside_the_profile_is_refused(alice, session, endpoint, path):
    status, body, _ = alice.get(f"{endpoint}?session_id={session}&path={q(path)}")
    assert status in REFUSED, body
    assert SECRET not in str(body)


def test_media_outside_the_profile_is_refused(srv, alice, session, outside, tmp_path, monkeypatch):
    # Make the whole test area a media root, so only Profile confinement can refuse.
    monkeypatch.setenv("MEDIA_ALLOWED_ROOTS", str(tmp_path.resolve()))
    for path in (outside / "secret.txt", _workspace(srv, BOB) / "bob.txt"):
        status, body, _ = alice.get(f"/api/media?path={q(path)}")
        assert status in REFUSED, (path, body)
        assert SECRET not in str(body)


def test_media_inside_the_profile_is_served(srv, alice, session):
    status, body, _ = alice.get(f"/api/media?path={q(_workspace(srv, ALICE) / 'notes.txt')}")
    assert status == 200, body


@pytest.mark.parametrize("path", ["../../../outside", "link-out", f"../../{BOB}/workspace"])
def test_listing_outside_the_profile_is_refused(alice, session, path):
    status, body, _ = alice.get(f"/api/list?session_id={session}&path={q(path)}")
    assert status in REFUSED, body
    assert "secret.txt" not in str(body) and "bob.txt" not in str(body)


@pytest.mark.parametrize("path", ESCAPING_PATHS)
@pytest.mark.parametrize("endpoint,extra", [
    ("/api/file/save", {"content": "overwritten"}),
    ("/api/file/create", {"content": "overwritten"}),
    ("/api/file/rename", {"new_name": "renamed.txt"}),
    ("/api/file/delete", {}),
    ("/api/file/reveal", {}),
    ("/api/file/create-dir", {}),
])
def test_writing_outside_the_profile_is_refused(srv, alice, session, outside, endpoint, extra, path):
    status, body, _ = alice.post(endpoint, {"session_id": session, "path": path, **extra})
    assert status in REFUSED, (endpoint, path, body)
    assert (outside / "secret.txt").read_text() == SECRET
    assert (_workspace(srv, BOB) / "bob.txt").read_text() == SECRET
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]


def test_a_session_whose_stored_workspace_is_outside_is_refused(srv, alice, session, outside):
    """Fail closed: even a Workspace root planted outside the Profile gives no access."""
    from api.models import get_session

    s = get_session(session)
    s.workspace = str(outside)
    s.save()
    status, body, _ = alice.get(f"/api/file?session_id={session}&path=secret.txt")
    assert status in REFUSED, body
    assert SECRET not in str(body)
    status, body, _ = alice.post("/api/file/save", {"session_id": session, "path": "secret.txt", "content": "x"})
    assert status in REFUSED, body
    assert (outside / "secret.txt").read_text() == SECRET
    status, body, _ = alice.get(f"/api/git/status?session_id={session}")
    assert status in REFUSED, body
    status, body, _ = alice.get(f"/api/git-info?session_id={session}")
    assert body == {"git": None}


def test_writing_inside_the_workspace_works(srv, alice, session):
    status, body, _ = alice.post("/api/file/create-dir", {"session_id": session, "path": "sub"})
    assert status == 200, body
    status, body, _ = alice.post("/api/file/create", {"session_id": session, "path": "sub/a.txt", "content": "hi"})
    assert status == 200, body
    assert (_workspace(srv, ALICE) / "sub" / "a.txt").read_text() == "hi"


# ── The Admin ────────────────────────────────────────────────────────────────

def test_admin_can_register_a_workspace_anywhere(srv, outside):
    admin = srv.logged_in(ADMIN)
    status, body, _ = admin.post("/api/workspaces/add", {"path": str(outside)})
    assert status == 200, body
    sid = _new_session(admin, workspace=str(outside))
    status, body, _ = admin.get(f"/api/file?session_id={sid}&path=secret.txt")
    assert status == 200, body
    assert SECRET in str(body)
