"""GFIT-CoWork: the User's web app asks the server what it may use.

Architecture review round 5, candidate 9. The server names each feature of the
web app by the route that gates it (``api.access.SHELL_FEATURES``) and stamps
the ones the caller may use on the app shell (``data-gfit-may``). The browser
hides the rest and does not call their routes, so a User's web app no longer
polls routes the gate refuses. The stylesheet and scripts name features, never
the role. The stored role is "user"; logins stored as "member" keep working.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from api import auth
from api.access import ROLE_ADMIN, ROLE_USER, SHELL_FEATURES, shell_features, user_may_call
from tests._gfit_server import gfit_server as _gfit_server
from tests.test_gfit_admin_gate_list import dispatched_routes

REPO = Path(__file__).resolve().parent.parent
USER = "600001"
ADMIN = "521740"

# What a User's web app may not call today, so must not show or poll.
REFUSED_TO_A_USER = {"provider_quota", "profiles_admin", "onboarding"}


# ── The list follows the gate ────────────────────────────────────────────────

def test_a_user_may_use_exactly_the_features_the_gate_lets_a_user_call():
    expected = {name for name, (method, path) in SHELL_FEATURES.items() if user_may_call(method, path)}
    assert set(shell_features(ROLE_USER)) == expected
    assert set(SHELL_FEATURES) - expected == REFUSED_TO_A_USER


@pytest.mark.parametrize("role", [ROLE_ADMIN, None])
def test_the_admin_and_a_request_with_no_caller_may_use_every_feature(role):
    assert shell_features(role) == tuple(SHELL_FEATURES)


def test_every_feature_names_a_route_the_server_handles():
    routes = dispatched_routes()

    def handled(method, path):
        return any(
            r.method == method and (path.startswith(r.route) if r.prefix else path == r.route)
            for r in routes
        )

    assert [name for name, route in SHELL_FEATURES.items() if not handled(*route)] == []


# ── The app shell carries it ────────────────────────────────────────────────

@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {ADMIN: "Admin One", USER: "User One"}
    with _gfit_server(monkeypatch, tmp_path, users=users, profile_names=[USER], admins=ADMIN) as s:
        yield s


def _may(html: str) -> set[str] | None:
    match = re.search(r'<html [^>]*data-gfit-may="([^"]*)"', html)
    return set(match.group(1).split()) if match else None


def test_a_users_app_shell_carries_the_features_they_may_use(srv):
    status, html, _ = srv.logged_in(USER).get("/")
    assert status == 200
    assert 'data-gfit-role="user"' in html
    assert _may(html) == set(shell_features(ROLE_USER))
    assert not _may(html) & REFUSED_TO_A_USER


def test_the_admins_app_shell_carries_every_feature(srv):
    status, html, _ = srv.logged_in(ADMIN).get("/")
    assert status == 200
    assert _may(html) == set(SHELL_FEATURES)


def test_with_login_off_the_app_shell_carries_no_list(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={}, directory="") as srv:
        status, html, _ = srv.client().get("/")
    assert status == 200
    assert _may(html) is None


# ── The stored role ──────────────────────────────────────────────────────────

def test_a_login_stored_with_the_role_member_keeps_working(srv, monkeypatch):
    client = srv.logged_in(USER)
    stored = json.loads(auth._SESSIONS_FILE.read_text())
    for record in stored.values():
        if isinstance(record, dict) and record.get("role") == ROLE_USER:
            record["role"] = "member"  # as written before the rename
    auth._SESSIONS_FILE.write_text(json.dumps(stored))

    monkeypatch.setattr(auth, "_sessions", auth._load_sessions())  # a restart

    status, html, _ = client.get("/")
    assert status == 200
    assert 'data-gfit-role="user"' in html
    assert client.get("/api/sessions")[0] == 200


# ── The browser names features, not the role ────────────────────────────────

def _static(name: str) -> str:
    return (REPO / "static" / name).read_text(encoding="utf-8")


def test_the_stylesheet_and_scripts_never_name_a_role():
    for name in ("style.css", "ui.js", "boot.js", "panels.js", "messages.js", "commands.js", "onboarding.js"):
        source = _static(name)
        assert "data-gfit-role" not in source, name
        assert "gfitAdminOnly" not in source and "data-gfit-admin-only" not in source, name


def test_every_feature_the_browser_names_is_one_the_server_lists():
    css = _static("style.css")
    js = "".join(_static(n) for n in ("ui.js", "boot.js", "panels.js", "messages.js", "commands.js", "onboarding.js"))
    named = set(re.findall(r'data-gfit-may~="([a-z_]+)"', css))
    named |= set(re.findall(r'data-gfit-feature="([a-z_]+)"', css))
    named |= set(re.findall(r"gfitMay\('([a-z_]+)'\)", js))
    named |= set(re.findall(r"gfitFeature='([a-z_]+)'", js))
    assert named, "the browser names no feature"
    assert named - set(SHELL_FEATURES) == set()
    # Every feature a User may not use is hidden or skipped somewhere.
    assert REFUSED_TO_A_USER - named == set()
