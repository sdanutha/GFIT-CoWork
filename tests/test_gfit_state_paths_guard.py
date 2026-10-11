"""GFIT-CoWork: state paths have one owner, and extracted modules stand on their own.

Architecture review round 6, candidate 6. The session-list cache was
extracted from the route module but read its paths and hooks back out of it
by name; it reads them from their owners now and must not import the route
module again.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Modules extracted from the route module, which the route module imports.
EXTRACTED = ("api/route_session_list_cache.py",)


def imports_of_the_route_module(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(alias.name == "api.routes" for alias in node.names):
            lines.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and (
            node.module == "api.routes" or (node.module == "api" and any(a.name == "routes" for a in node.names))
        ):
            lines.append(node.lineno)
    return lines


def test_extracted_modules_do_not_import_the_route_module():
    found = {
        module: imports_of_the_route_module((ROOT / module).read_text(encoding="utf-8"))
        for module in EXTRACTED
    }
    assert {module: lines for module, lines in found.items() if lines} == {}


def test_the_import_guard_finds_each_spelling():
    assert imports_of_the_route_module("import api.routes as _routes\n") == [1]
    assert imports_of_the_route_module("from api.routes import x\n") == [1]
    assert imports_of_the_route_module("from api import routes\n") == [1]
    assert imports_of_the_route_module("from api import models\n") == []


def re_export_shims(source: str) -> list[str]:
    """A module-level ``__getattr__``, or an import kept only to re-export (``noqa: F401``)."""
    found = []
    tree = ast.parse(source)
    if any(isinstance(node, ast.FunctionDef) and node.name == "__getattr__" for node in tree.body):
        found.append("module __getattr__")
    for number, line in enumerate(source.splitlines(), start=1):
        if line.lstrip().startswith(("from ", "import ")) and "noqa: F401" in line:
            found.append(f"line {number}")
    return found


def test_the_route_module_re_exports_nothing():
    assert re_export_shims((ROOT / "api" / "routes.py").read_text(encoding="utf-8")) == []


def test_the_shim_guard_finds_both_kinds():
    source = "from api.x import (  # noqa: F401\n    a,\n)\n\ndef __getattr__(name):\n    return name\n"
    assert re_export_shims(source) == ["module __getattr__", "line 1"]


# ── Behaviour: one patch of api.config moves the session store ──────────────


def test_patching_the_config_module_moves_the_session_store(monkeypatch, tmp_path):
    from tests._gfit_server import gfit_server

    sessions = tmp_path / "moved-sessions"
    sessions.mkdir()
    monkeypatch.setattr("api.config.SESSION_DIR", sessions)
    monkeypatch.setattr("api.config.SESSION_INDEX_FILE", sessions / "_index.json")
    alice = "521740"
    with gfit_server(monkeypatch, tmp_path, users={alice: "Alice"}, profile_names=[alice]) as server:
        client = server.logged_in(alice)
        status, payload, _ = client.post("/api/session/new", {})
        assert status == 200, payload
        sid = payload["session"]["session_id"]
        status, payload, _ = client.post("/api/session/rename", {"session_id": sid, "title": "Moved"})
        assert status == 200, payload
    assert (sessions / f"{sid}.json").exists()
    assert sid in (sessions / "_index.json").read_text(encoding="utf-8")


def state_path_copies(source: str) -> list[str]:
    """Module-level bindings of a state path name (by import or assignment)."""
    names = {"STATE_DIR", "SESSION_DIR", "SESSION_INDEX_FILE", "SETTINGS_FILE", "PROJECTS_FILE",
             "WORKSPACES_FILE", "LAST_WORKSPACE_FILE"}
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom):
            found += [alias.asname or alias.name for alias in node.names if (alias.asname or alias.name) in names]
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [t.id for t in targets if isinstance(t, ast.Name) and t.id in names]
    return found


def test_no_module_but_the_config_module_holds_a_copy_of_a_state_path():
    copies = {
        str(path.relative_to(ROOT)): state_path_copies(path.read_text(encoding="utf-8"))
        for path in (ROOT / "api").rglob("*.py")
        if path.name != "config.py" or path.parent != ROOT / "api"
    }
    assert {module: names for module, names in copies.items() if names} == {}


def test_the_copy_guard_finds_imports_and_assignments():
    assert state_path_copies("from api.config import SESSION_DIR, LOCK\nSTATE_DIR = 1\n") == ["SESSION_DIR", "STATE_DIR"]
