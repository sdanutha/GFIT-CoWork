"""GFIT-CoWork: shipped code never points at Upstream (upstream-gone ticket 11).

GFIT-CoWork is a hard fork (ADR 0001). The in-app update check, the extension
Gallery, ``/pet`` and the Help links that sent people or requests to Upstream
are gone; this guard keeps them from growing back. It reads the shipped source
and fails, naming each file and line, on any Upstream URL. Comments are not
exempt: a comment citing an Upstream issue keeps the number as plain text
("Upstream #4029") without the URL.

Out of scope: ``docs/``, ``tests/``, ``CHANGELOG.md``, the README's single fork
credit, and ``pyproject.toml``, whose Upstream project URL is the same credit.

:data:`ALLOWLIST` named the offenders the migration removed; it is empty and
stays so.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Where shipped code lives: directories scanned recursively, and single files.
SCANNED_DIRS = ("api", "static", "deploy", ".github")
SCANNED_ROOT_GLOBS = ("*.py", "*.sh", "*.ps1")
SCANNED_FILES = ("Dockerfile", "docker_init.bash")
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".json", ".sh", ".bash", ".ps1",
                 ".yml", ".yaml", ".md", ".txt", ".example", ".caddy", ""}

UPSTREAM_URL = re.compile(
    r"github\.com/nesquena/hermes-webui"
    r"|api\.github\.com/repos/nesquena/"
    r"|hermes-webui\.github\.io"
    r"|github\.com/hermes-webui/"
    r"|github\.com/franksong2702/hermes-webui-desktop-companion"
    r"|get-hermes\.ai",
    re.IGNORECASE,
)

# (file, line text): Upstream URLs not yet removed (none left).
ALLOWLIST: set[tuple[str, str]] = set()


def _shipped_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCANNED_DIRS:
        root = REPO / rel
        if root.is_dir():
            files.extend(p for p in root.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES)
    for pattern in SCANNED_ROOT_GLOBS:
        files.extend(REPO.glob(pattern))
    files.extend(REPO / rel for rel in SCANNED_FILES if (REPO / rel).is_file())
    return sorted({p for p in files if "__pycache__" not in p.parts})


def _upstream_urls_in(text: str) -> list[tuple[int, str]]:
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if UPSTREAM_URL.search(line)]


def _offenders() -> list[tuple[str, int, str]]:
    found = []
    for path in _shipped_files():
        rel = path.relative_to(REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found.extend((rel, n, line) for n, line in _upstream_urls_in(text))
    return found


def test_no_shipped_file_names_an_upstream_url():
    offenders = [(f, n, line) for f, n, line in _offenders() if (f, line) not in ALLOWLIST]
    assert not offenders, "Upstream URLs in shipped code:\n" + "\n".join(
        f"  {f}:{n}: {line}" for f, n, line in offenders
    )


def test_allowlist_is_empty():
    assert not ALLOWLIST


@pytest.mark.parametrize("line", [
    "# See https://github.com/nesquena/hermes-webui/issues/1968.",
    "url='https://api.github.com/repos/nesquena/hermes-webui/tags'",
    '_REGISTRY_URL = "https://hermes-webui.github.io/hermes-webui-extensions/registry.json"',
    "return 'https://github.com/hermes-webui/hermes-webui-extensions/tree/main/'",
    "const URL='https://github.com/franksong2702/hermes-webui-desktop-companion#setup';",
    '<a href="https://get-hermes.ai/">',
    "HTTPS://GITHUB.COM/NESQUENA/HERMES-WEBUI",
])
def test_the_guard_catches_each_url_form(line):
    assert _upstream_urls_in(line)


@pytest.mark.parametrize("line", [
    "# Upstream #4029: drop the stale steer.",
    "HERMES_WEBUI_PORT=8787",
    "localStorage.getItem('hermes-webui-session')",
    "https://github.com/NousResearch/hermes-agent",
])
def test_the_guard_leaves_other_text_alone(line):
    assert not _upstream_urls_in(line)


def test_the_guard_reads_every_kind_of_shipped_file():
    rels = {p.relative_to(REPO).as_posix() for p in _shipped_files()}
    for expected in ("api/routes.py", "static/panels.js", "static/index.html", "server.py",
                     "start.sh", "start.ps1", "Dockerfile", "docker_init.bash",
                     "deploy/docker-compose.yml", ".github/workflows/tests.yml"):
        assert expected in rels, expected
