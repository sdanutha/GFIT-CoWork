"""GFIT-CoWork: the web app has no Todos (remove-todos tickets 01-03).

The sidebar Todos panel, the Workspace Todos tab and its setting are gone, and
so is the ``todo_state`` pipeline that fed them. The agent keeps its ``todo``
tool; its calls still show as ordinary tool cards.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import json

import pytest

from tests._gfit_server import gfit_server as _gfit_server

USER = "600001"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(
        monkeypatch, tmp_path, users={USER: "User"}, profile_names=[USER],
    ) as s:
        yield s


def _page(srv) -> str:
    status, body, _ = srv.logged_in(USER).get("/")
    assert status == 200
    assert isinstance(body, str)
    return body


def test_the_workspace_panel_has_no_todos_tab_and_settings_no_checkbox(srv):
    page = _page(srv)
    assert 'id="workspaceFilesTab"' in page or "switchWorkspacePanelTab('files')" in page
    for gone in ("workspaceTodosTab", "workspaceTodosPanel", "settingsWorkspaceTodosTab",
                 "switchWorkspacePanelTab('todos')"):
        assert gone not in page, gone


def test_the_workspace_todos_setting_is_not_returned_or_stored(srv):
    client = srv.logged_in(USER)
    status, body, _ = client.post("/api/settings", {"workspace_todos_tab": True, "send_key": "ctrl+enter"})
    assert status == 200, body
    status, body, _ = client.get("/api/settings")
    assert status == 200
    assert body["send_key"] == "ctrl+enter"
    assert "workspace_todos_tab" not in body
