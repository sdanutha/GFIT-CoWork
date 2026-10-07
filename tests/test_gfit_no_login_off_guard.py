"""GFIT-CoWork: there is no mode with login turned off, and no test-only way in (ADR 0006).

A guard on the source: "is a Directory configured?" may only be asked where it
can refuse — startup (refuse to start) and the per-request session check
(refuse the session). Any other place that asked it would be a branch that
serves something when login is off. The test-only HTTP hooks the Admin had
are gone, and the shared test server is a real login (tests/conftest.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from api import route_table

REPO = Path(__file__).resolve().parent.parent

# file -> how many times it may ask, and why each one refuses.
ALLOWED = {
    "api/login.py": 1,  # startup_check: no Directory -> the server does not start
    "api/auth.py": 1,   # _reconcile_directory_session: no Directory -> the session is ended
}


def _asks(path: Path) -> int:
    return len(re.findall(r"\bis_directory_enabled\(\)", path.read_text(encoding="utf-8")))


def test_only_the_refusing_places_ask_whether_a_directory_is_configured():
    found = {}
    for path in [*sorted((REPO / "api").glob("*.py")), REPO / "server.py"]:
        rel = path.relative_to(REPO).as_posix()
        if rel == "api/directory.py":
            continue
        count = _asks(path)
        if count:
            found[rel] = count
    assert found == ALLOWED, (
        "A new is_directory_enabled() check would be a login-off branch; "
        f"login is always on (ADR 0006). Found: {found}"
    )


def test_the_login_check_has_no_way_round_it():
    source = (REPO / "api" / "auth.py").read_text(encoding="utf-8")
    body = source[source.index("def check_auth("):source.index("\ndef ", source.index("def check_auth(") + 1)]
    assert "is_directory_enabled" not in body
    assert "return True" in body  # public paths and admitted sessions only


def test_no_test_only_route_remains():
    test_hooks = [
        f"{route.method} {route.pattern}" for route in route_table.ROUTES
        if "inject_test" in route.pattern or "/test/" in route.pattern or route.pattern.endswith("_test")
    ]
    assert test_hooks == []


def test_the_shared_test_server_runs_with_a_directory():
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert '"HERMES_WEBUI_DIRECTORY":         "memory"' in conftest
    assert '"HERMES_WEBUI_DIRECTORY":         ""' not in conftest
