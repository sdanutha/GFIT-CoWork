"""GFIT-CoWork ships no MCP server of its own (remove-admin ticket 08).

``mcp_server.py`` was Upstream's stdio MCP server for project and session
tools. It had no caller identity: started by any agent that configured it, it
took the process's Profile (``default``) or any ``--profile`` it was given, and
read and wrote the Deployment's shared session index and projects directly. It
is deleted, not disabled. Hermes Agent's own MCP client (``api/mcp_runtime.py``,
the MCP routes) is a different thing and stays.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_mcp_server_module_is_gone():
    assert not (REPO / "mcp_server.py").exists()
    modules = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["py-modules"]
    assert "mcp_server" not in modules


def test_nothing_starts_or_documents_it():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    allowed = {"CHANGELOG.md", "tests/test_gfit_no_mcp_server.py", "tests/test_issue2695_packaged_runtime_layout.py"}
    hits = []
    for rel in tracked:
        if rel in allowed or not (REPO / rel).is_file():
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "mcp_server.py" in text or "python3 mcp_server" in text or "-m mcp_server" in text:
            hits.append(rel)
    assert hits == []


def test_hermes_agents_own_mcp_client_support_stays():
    assert (REPO / "api" / "mcp_runtime.py").exists()
