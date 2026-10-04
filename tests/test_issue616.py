import pathlib


def test_workspace_suggest_endpoint_is_wired():
    from tests._route_source import route_handler

    assert route_handler("GET", "/api/workspaces/suggest")


def test_spaces_panel_uses_workspace_suggest_autocomplete():
    src = pathlib.Path("static/panels.js").read_text(encoding="utf-8")
    assert "/api/workspaces/suggest" in src
    assert "workspaceFormPathSuggestions" in src
    assert "scheduleWorkspacePathSuggestions" in src
    assert "if(!prefix)" in src
    assert "dataset.path" in src
    assert "scrollIntoView" in src
    assert "_wsSuggestIndex=0" in src
