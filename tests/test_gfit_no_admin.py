"""GFIT-CoWork: there is no Admin in the web app (ADR 0006).

Every caller is a User, bound to their own Profile. The Admin-only features
are deleted, so a User calling one is refused like any path with no row (403,
fail closed). A Deployment that still sets ``HERMES_WEBUI_ADMIN_USERS`` gets a
startup warning and nothing else: the people named there log in as Users to
their own Profile, or not at all, and an Admin session from before the change
fails Admission. HTTP tests against an in-process server
(``tests/_gfit_server.py``).

Replaces ``test_gfit04_admin_gate.py``, which tested the Admin role.
"""
from __future__ import annotations

import pytest

import api.auth as auth
from api.access import LEFTOVER_ADMIN_USERS_ENV, NOT_AVAILABLE_MESSAGE
from api.login import NO_PROFILE_MESSAGE, startup_check
from tests._gfit_server import gfit_server as _gfit_server

FORMER_ADMIN = "521740"
FORMER_ADMIN_WITH_PROFILE = "671278"
USER = "600001"

# The Admin-only features ADR 0006 deleted, one or more endpoints each.
DELETED_ADMIN_FEATURES = [
    # terminal
    ("POST", "/api/terminal/start", {}),
    ("POST", "/api/terminal/input", {}),
    ("GET", "/api/terminal/output", None),
    # mutating workspace git
    ("POST", "/api/git/stage", {}),
    ("POST", "/api/git/commit", {}),
    ("POST", "/api/git/push", {}),
    ("POST", "/api/git/pull", {}),
    ("POST", "/api/git/checkout", {}),
    ("POST", "/api/git/discard", {}),
    # extensions
    ("GET", "/api/extensions/status", None),
    ("POST", "/api/extensions/toggle", {}),
    ("GET", "/api/extensions/some-ext/sidecar/x", None),
    ("GET", "/extensions/some-ext/app.js", None),
    # shutdown / reload
    ("POST", "/api/shutdown", {}),
    ("POST", "/api/health/restart", {}),
    ("POST", "/api/admin/reload", {}),
    # server logs
    ("GET", "/api/logs", None),
    # YOLO mode
    ("POST", "/api/session/yolo", {}),
    # providers, API keys, MCP servers
    ("GET", "/api/providers", None),
    ("POST", "/api/providers", {}),
    ("POST", "/api/providers/delete", {}),
    ("GET", "/api/provider/quota", None),
    ("POST", "/api/models/refresh", {}),
    ("GET", "/api/mcp/servers", None),
    ("PUT", "/api/mcp/servers/x", {}),
    ("DELETE", "/api/mcp/servers/x", {}),
    # onboarding
    ("GET", "/api/onboarding/status", None),
    ("POST", "/api/onboarding/setup", {}),
    ("POST", "/api/onboarding/complete", {}),
    # gateway control
    ("POST", "/api/gateway/start", {}),
    ("POST", "/api/gateway/stop", {}),
    ("POST", "/api/gateway/restart", {}),
    # Profile management and switching (the Operator's command line now)
    ("POST", "/api/profile/create", {"name": "700001"}),
    ("POST", "/api/profile/disable", {"name": USER}),
    ("POST", "/api/profile/enable", {"name": USER}),
    ("POST", "/api/profile/delete", {"name": USER, "confirm": USER}),
    ("POST", "/api/profile/switch", {"name": "default"}),
    # public share links
    ("POST", "/api/share/create", {}),
    ("POST", "/api/share/revoke", {}),
    # files outside the Workspace
    ("GET", "/api/escape/list", None),
    ("POST", "/api/escape/authorize", {}),
    ("POST", "/api/file/open-vscode", {}),
    ("POST", "/api/file/reveal", {"session_id": "x", "path": "."}),
    # server-side agent commands
    ("POST", "/api/commands/exec", {"command": "/reload-mcp"}),
    # dashboard, Kanban
    ("POST", "/api/dashboard/config", {}),
    ("GET", "/api/kanban/board", None),
    ("POST", "/api/kanban/tasks", {}),
    # session-store maintenance (the Operator's command line now)
    ("POST", "/api/sessions/cleanup", {}),
    ("POST", "/api/sessions/cleanup_zero_message", {}),
    ("GET", "/api/session/recovery/audit", None),
    ("POST", "/api/session/recovery/repair-safe", {}),
]

USER_ALLOWED = [
    ("GET", "/api/sessions", None),
    ("GET", "/api/profile/active", None),
    ("GET", "/api/profiles", None),
    ("GET", "/api/settings", None),
    ("GET", "/api/models", None),
    ("GET", "/api/workspaces", None),
    ("GET", "/api/memory", None),
    ("GET", "/api/auth/status", None),
    ("POST", "/api/session/new", {}),
    ("GET", "/", None),
]


@pytest.fixture
def srv(monkeypatch, tmp_path):
    users = {FORMER_ADMIN: "Former Admin", FORMER_ADMIN_WITH_PROFILE: "Former Admin Two", USER: "User"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[USER, FORMER_ADMIN_WITH_PROFILE],
    ) as s:
        monkeypatch.setenv(LEFTOVER_ADMIN_USERS_ENV, f"{FORMER_ADMIN}, GFIT\\{FORMER_ADMIN_WITH_PROFILE}")
        yield s


@pytest.fixture
def user(srv):
    return srv.logged_in(USER)


@pytest.mark.parametrize("method,path,body", DELETED_ADMIN_FEATURES)
def test_a_deleted_admin_feature_is_refused_like_an_unknown_path(user, method, path, body):
    status, payload, _ = user.request(method, path, body)
    assert status == 403, (method, path, payload)
    if isinstance(payload, dict) and "error" in payload and "Cross-origin" not in payload["error"]:
        assert payload["error"] == NOT_AVAILABLE_MESSAGE


@pytest.mark.parametrize("method,path,body", USER_ALLOWED)
def test_a_user_can_use_the_user_routes(user, method, path, body):
    status, payload, _ = user.request(method, path, body)
    assert status == 200, (method, path, payload)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_an_unclassified_endpoint_is_refused(user, method):
    status, _, _ = user.request(method, "/api/gfit-unclassified-probe", {} if method != "GET" else None)
    assert status == 403


def test_a_user_route_under_an_unlisted_method_is_refused(user):
    status, _, _ = user.request("DELETE", "/api/sessions", {})
    assert status == 403


def test_a_former_admin_without_a_profile_is_refused_at_login(srv):
    status, body, _ = srv.client().login(FORMER_ADMIN)
    assert status == 403
    assert body["error"] == NO_PROFILE_MESSAGE


def test_a_former_admin_with_a_profile_logs_in_as_a_user_to_it(srv):
    client = srv.logged_in(FORMER_ADMIN_WITH_PROFILE)

    status, body, _ = client.get("/api/auth/status")
    assert (body["user"], body["bound_profile"]) == (FORMER_ADMIN_WITH_PROFILE, FORMER_ADMIN_WITH_PROFILE)
    assert "role" not in body
    assert client.get("/api/profile/active")[1]["name"] == FORMER_ADMIN_WITH_PROFILE
    assert client.get("/api/logs")[0] == 403


@pytest.mark.parametrize("employee_id", [FORMER_ADMIN, FORMER_ADMIN_WITH_PROFILE])
def test_an_admin_session_from_before_the_change_fails_admission(srv, employee_id):
    cookie = auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE, username=employee_id, bound_profile="default", role="admin",
    )
    client = srv.client()
    client.cookies[auth.COOKIE_NAME] = cookie

    assert client.get("/api/profile/active")[0] == 401
    assert client.get("/api/logs")[0] == 401
    assert not any(record.get("role") == "admin" for record in auth._sessions.values() if isinstance(record, dict))


def test_the_app_shell_carries_no_role_or_feature_list(user):
    status, html, _ = user.get("/")
    assert status == 200
    assert "data-gfit-role" not in html and "data-gfit-may" not in html


def test_an_unauthenticated_request_is_refused(srv):
    client = srv.client()
    assert client.get("/api/sessions")[0] == 401
    status, _, headers = client.get("/")
    assert status == 302


def test_startup_warns_about_a_leftover_admin_list(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    monkeypatch.setenv(LEFTOVER_ADMIN_USERS_ENV, "521740")

    check = startup_check()

    assert check.serve is True
    assert any(LEFTOVER_ADMIN_USERS_ENV in line and "no Admin" in line for line in check.lines)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_startup_says_nothing_without_an_admin_list(monkeypatch, value):
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    if value is None:
        monkeypatch.delenv(LEFTOVER_ADMIN_USERS_ENV, raising=False)
    else:
        monkeypatch.setenv(LEFTOVER_ADMIN_USERS_ENV, value)

    assert not any(LEFTOVER_ADMIN_USERS_ENV in line for line in startup_check().lines)


def test_a_login_stored_with_the_role_member_keeps_working(srv, monkeypatch):
    # Moved from test_gfit_shell_features.py, which went with the feature list.
    import json

    client = srv.logged_in(USER)
    stored = json.loads(auth._SESSIONS_FILE.read_text())
    for record in stored.values():
        if isinstance(record, dict) and record.get("role") == "user":
            record["role"] = "member"  # as written before the rename
    auth._SESSIONS_FILE.write_text(json.dumps(stored))

    monkeypatch.setattr(auth, "_sessions", auth._load_sessions())  # a restart

    assert client.get("/")[0] == 200
    assert client.get("/api/sessions")[0] == 200
