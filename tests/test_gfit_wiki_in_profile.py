"""GFIT-CoWork: a User's wiki lives inside their own Workspace.

- A User's wiki is ``<Profile>/workspace/wiki`` unless their own Profile sets
  ``WIKI_PATH`` (or ``wiki.path``) inside their Workspace. The server
  account's ``~/wiki`` and the process environment (which may hold another
  Profile's ``WIKI_PATH``) are never a User's wiki.
- A ``WIKI_PATH`` outside the User's Workspace is refused: no wiki.
- Login writes ``WIKI_PATH`` into a User's Profile ``.env`` when it has none,
  so the Agent's llm-wiki skill uses the same folder. An existing value is
  kept, and the process environment is not changed.
- The Admin, and login turned off, keep today's resolution.

HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import os

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"
ADMIN = "600001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_PATH", raising=False)
    users = {ALICE: "Alice", BOB: "Bob", ADMIN: "Admin"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[ALICE, BOB], admins=ADMIN,
    ) as s:
        yield s


def _page(wiki, name, text="page"):
    path = wiki / "entities" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {name}\n---\n{text}\n")


def _browse(client) -> tuple[int, list[str]]:
    status, body, _ = client.get("/api/wiki/browse")
    pages = [p["name"] for p in body.get("pages", [])] if isinstance(body, dict) else []
    return status, pages


def _env_value(path, key="WIKI_PATH"):
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


def test_a_users_wiki_is_in_their_workspace_not_the_process_environment(srv, tmp_path, monkeypatch):
    bobs_wiki = srv.profile_home(BOB) / "workspace" / "wiki"
    _page(bobs_wiki, "bob-secret")
    # The process environment may hold whichever Profile's .env was loaded last.
    monkeypatch.setenv("WIKI_PATH", str(bobs_wiki))
    alice = srv.logged_in(ALICE)
    _page(srv.profile_home(ALICE) / "workspace" / "wiki", "alice-note")

    status, pages = _browse(alice)

    assert status == 200
    assert pages == ["alice-note.md"]


def test_a_users_wiki_never_falls_back_to_the_server_accounts_home(srv, tmp_path, monkeypatch):
    server_home = tmp_path / "server-home"
    _page(server_home / "wiki", "server-wiki-page")
    monkeypatch.setenv("HOME", str(server_home))
    alice = srv.logged_in(ALICE)

    status, pages = _browse(alice)

    assert "server-wiki-page.md" not in pages


def test_a_users_wiki_path_outside_their_workspace_is_refused(srv):
    bobs_wiki = srv.profile_home(BOB) / "workspace" / "wiki"
    _page(bobs_wiki, "bob-secret")
    alice = srv.logged_in(ALICE)
    (srv.profile_home(ALICE) / ".env").write_text(f"WIKI_PATH={bobs_wiki}\n")

    status, pages = _browse(alice)

    assert status == 404
    assert pages == []
    status, body, _ = alice.get("/api/wiki/status")
    assert status == 200, body
    assert body["available"] is False


def test_a_users_own_wiki_path_inside_their_workspace_is_used(srv):
    custom = srv.profile_home(ALICE) / "workspace" / "notes" / "kb"
    _page(custom, "custom-page")
    alice = srv.logged_in(ALICE)
    (srv.profile_home(ALICE) / ".env").write_text(f"WIKI_PATH={custom}\n")

    status, pages = _browse(alice)

    assert (status, pages) == (200, ["custom-page.md"])


def test_login_records_the_users_wiki_folder_for_the_agent(srv):
    srv.logged_in(ALICE)

    expected = srv.profile_home(ALICE) / "workspace" / "wiki"
    assert _env_value(srv.profile_home(ALICE) / ".env") == str(expected)
    assert expected.is_dir()
    assert "WIKI_PATH" not in os.environ


def test_login_keeps_a_wiki_path_the_profile_already_has(srv):
    env = srv.profile_home(ALICE) / ".env"
    env.write_text("# mine\nWIKI_PATH=/somewhere/else\nOTHER=1\n")

    srv.logged_in(ALICE)

    assert env.read_text() == "# mine\nWIKI_PATH=/somewhere/else\nOTHER=1\n"


def test_the_admins_wiki_keeps_todays_resolution(srv, tmp_path, monkeypatch):
    shared = tmp_path / "team-wiki"
    _page(shared, "team-page")
    monkeypatch.setenv("WIKI_PATH", str(shared))

    status, pages = _browse(srv.logged_in(ADMIN))

    assert (status, pages) == (200, ["team-page.md"])
    assert not (srv.hermes_home / ".env").exists() or _env_value(srv.hermes_home / ".env") is None
