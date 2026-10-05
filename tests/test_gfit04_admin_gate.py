"""GFIT-CoWork ticket 04: the Admin role and the Admin-only feature gate.

An Admin named in ``HERMES_WEBUI_ADMIN_USERS`` logs in to the ``default``
Profile with Admin rights. Admin-only endpoints refuse Members with 403 at the
server, and an endpoint the gate cannot classify is refused too (fail closed).
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import pytest

from api.access import ADMIN_ONLY_MESSAGE
from tests._gfit_server import gfit_server as _gfit_server

ADMIN = "521740"
SECOND_ADMIN = "671278"
MEMBER = "600001"

# Every Admin-only feature the ticket names, one or more endpoints each.
ADMIN_ONLY = [
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
    # provider / API-key / model settings
    ("GET", "/api/providers", None),
    ("POST", "/api/providers", {}),
    ("POST", "/api/providers/delete", {}),
    ("GET", "/api/provider/quota", None),
    ("POST", "/api/models/refresh", {}),
    ("GET", "/api/mcp/servers", None),
    ("PUT", "/api/mcp/servers/x", {}),
    ("DELETE", "/api/mcp/servers/x", {}),
    # Deployment-wide settings
    # onboarding
    ("GET", "/api/onboarding/status", None),
    ("POST", "/api/onboarding/setup", {}),
    ("POST", "/api/onboarding/complete", {}),
    # gateway control
    ("POST", "/api/gateway/start", {}),
    ("POST", "/api/gateway/stop", {}),
    ("POST", "/api/gateway/restart", {}),
    # profile management
    ("POST", "/api/profile/create", {"name": "700001"}),
    ("POST", "/api/profile/delete", {"name": MEMBER}),
    ("POST", "/api/profile/switch", {"name": "default"}),
    # public share links
    ("POST", "/api/share/create", {}),
    ("POST", "/api/share/revoke", {}),
    # reading files outside the Workspace
    ("GET", "/api/escape/list", None),
    ("POST", "/api/escape/authorize", {}),
    ("POST", "/api/file/open-vscode", {}),
    # server-side agent commands
    ("POST", "/api/commands/exec", {"command": "/reload-mcp"}),
    # dashboard control
    ("POST", "/api/dashboard/config", {}),
    # the Kanban board is shared by every Profile
    ("GET", "/api/kanban/board", None),
    ("POST", "/api/kanban/tasks", {}),
]

# Routes that act on the session store every Profile shares, or on the server machine.
REACH_EVERY_PROFILE_OR_THE_SERVER = [
    ("POST", "/api/sessions/cleanup", {}),
    ("POST", "/api/sessions/cleanup_zero_message", {}),
    ("GET", "/api/session/recovery/audit", None),
    ("POST", "/api/session/recovery/repair-safe", {}),
    ("POST", "/api/file/reveal", {"session_id": "x", "path": "."}),
]

# Safe for the Admin to call in a test (no shutdown, update, or install).
ADMIN_SAFE = [
    ("GET", "/api/logs", None),
    ("GET", "/api/providers", None),
    ("GET", "/api/extensions/status", None),
    ("GET", "/api/onboarding/status", None),
    ("GET", "/api/mcp/servers", None),
    ("GET", "/api/escape/list", None),
    ("GET", "/api/session/recovery/audit", None),
    ("POST", "/api/sessions/cleanup_zero_message", {}),  # the test teardown calls it too
    ("POST", "/api/file/reveal", {"session_id": "no-such-session", "path": "."}),  # never opens anything
]

MEMBER_ALLOWED = [
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
    users = {ADMIN: "Admin One", SECOND_ADMIN: "Admin Two", MEMBER: "Member"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[MEMBER],
        admins=f"{ADMIN}, GFIT\\{SECOND_ADMIN}",
    ) as s:
        yield s


@pytest.fixture
def admin(srv):
    return srv.logged_in(ADMIN)


@pytest.fixture
def member(srv):
    return srv.logged_in(MEMBER)


@pytest.mark.parametrize("uid", [ADMIN, SECOND_ADMIN])
def test_admin_lands_in_default_without_a_profile_of_their_own(srv, uid):
    assert not srv.profile_home(uid).exists()
    client = srv.logged_in(uid)
    status, body, _ = client.get("/api/profile/active")
    assert status == 200
    assert body["name"] == "default"
    status, body, _ = client.get("/api/auth/status")
    assert body["role"] == "admin"
    assert body["user"] == uid
    assert body["bound_profile"] == "default"


def test_member_is_reported_as_member(member):
    status, body, _ = member.get("/api/auth/status")
    assert body["role"] == "user"


def test_admin_sees_every_profile(admin):
    status, body, _ = admin.get("/api/profiles")
    assert status == 200
    assert {p["name"] for p in body["profiles"]} >= {"default", MEMBER}
    assert body["single_profile_mode"] is False


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_member_gets_403_from_admin_only_endpoint(member, method, path, body):
    status, payload, _ = member.request(method, path, body)
    assert status == 403, (method, path, payload)


@pytest.mark.parametrize("method,path,body", ADMIN_SAFE)
def test_admin_passes_the_gate(admin, method, path, body):
    status, payload, _ = admin.request(method, path, body)
    assert status != 403, (method, path, payload)


@pytest.mark.parametrize("method,path,body", MEMBER_ALLOWED)
def test_member_can_use_member_endpoints(member, method, path, body):
    status, payload, _ = member.request(method, path, body)
    assert status == 200, (method, path, payload)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_unclassified_endpoint_is_refused_for_members(member, admin, method):
    path = "/api/gfit-unclassified-probe"
    status, _, _ = member.request(method, path, {} if method != "GET" else None)
    assert status == 403
    status, _, _ = admin.request(method, path, {} if method != "GET" else None)
    assert status == 404


def test_member_allowed_path_with_unlisted_method_is_refused(member):
    status, _, _ = member.request("DELETE", "/api/sessions", {})
    assert status == 403


def test_member_index_marks_the_role_and_admin_index_does_not(member, admin):
    status, html, _ = member.get("/")
    assert status == 200
    assert 'data-gfit-role="user"' in html
    status, html, _ = admin.get("/")
    assert 'data-gfit-role="admin"' in html


def test_session_ends_when_admin_is_removed_from_the_list(srv, admin, monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_ADMIN_USERS", SECOND_ADMIN)
    status, _, _ = admin.get("/api/profile/active")
    assert status == 401


def test_member_session_ends_when_they_are_added_to_the_admin_list(srv, member, monkeypatch):
    assert member.get("/api/sessions")[0] == 200
    monkeypatch.setenv("HERMES_WEBUI_ADMIN_USERS", f"{ADMIN},{MEMBER}")

    status, _, _ = member.get("/api/sessions")
    assert status == 401

    again = srv.logged_in(MEMBER)
    status, body, _ = again.get("/api/auth/status")
    assert body["role"] == "admin"
    assert body["bound_profile"] == "default"


def test_admin_with_a_profile_of_their_own_is_signed_out_when_removed_from_the_list(srv, admin, monkeypatch):
    srv.profile_home(ADMIN).mkdir()
    monkeypatch.setenv("HERMES_WEBUI_ADMIN_USERS", SECOND_ADMIN)

    status, _, _ = admin.get("/api/profile/active")
    assert status == 401

    again = srv.logged_in(ADMIN)
    status, body, _ = again.get("/api/auth/status")
    assert body["role"] == "user"
    assert body["bound_profile"] == ADMIN


@pytest.mark.parametrize("method,path,body", REACH_EVERY_PROFILE_OR_THE_SERVER)
def test_member_is_refused_routes_that_reach_every_profile(member, method, path, body):
    status, payload, _ = member.request(method, path, body)
    assert status == 403, (method, path, payload)
    assert payload["error"] == ADMIN_ONLY_MESSAGE
