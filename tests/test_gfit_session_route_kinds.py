"""GFIT-CoWork: every route that names a session is a read or a write, in one table.

Session ownership classifies each (method, route) that names a session by
what it does, not its method (each row's ``session`` in ``api.route_table``):
the Admin may read another Profile's session in place but not write to it.
This test fails when a session-naming route has no class, or when the table
names a route that no longer names a session. Session-naming User routes are
placed in ``tests/test_gfit_session_route_answers.py``; every Admin-only route
the server handles is placed here, as naming a session or not (with a reason).
"""
from __future__ import annotations

import pytest

from api.access import user_may_call
from api.session_ownership import READ, WRITE, session_route_kind
from api.route_table import ROUTES as _ROUTES

# The route table's session routes, as (method, pattern) -> READ or WRITE.
SESSION_ROUTE_KINDS = {(r.method, r.pattern): r.session for r in _ROUTES if r.session is not None}
from tests.test_gfit_admin_gate_list import dispatched_routes
from tests.test_gfit_session_route_answers import SESSION_ROUTES

# Admin-only routes that name a session (a session id in the query or body).
ADMIN_SESSION_ROUTES = {
    ("GET", "/api/escape/file/raw"), ("GET", "/api/escape/file/read"), ("GET", "/api/escape/list"),
    ("GET", "/api/terminal/output"),
    ("GET", "/api/approval/inject_test"), ("GET", "/api/clarify/inject_test"),
    ("POST", "/api/escape/authorize"), ("POST", "/api/file/open-vscode"), ("POST", "/api/file/reveal"),
    *{("POST", f"/api/git/{verb}") for verb in (
        "checkout", "commit", "commit-message", "commit-message-selected", "commit-selected", "discard",
        "fetch", "pull", "push", "stage", "stash-checkout", "unstage")},
    ("POST", "/api/session/worktree/remove"), ("POST", "/api/session/yolo"),
    ("POST", "/api/share/create"), ("POST", "/api/share/revoke"),
    *{("POST", f"/api/terminal/{verb}") for verb in ("close", "input", "resize", "start")},
}

# Admin-only routes that name no session, and why.
_SERVER = "server-level: settings, providers, models, gateway, logs, process"
ADMIN_NAMES_NO_SESSION: dict[tuple[str, str], str] = {
    **{(m, r): "kanban store (prefix route)" for m, r in (
        ("DELETE", "/api/kanban/"), ("GET", "/api/kanban/"), ("PATCH", "/api/kanban/"), ("POST", "/api/kanban/"))},
    **{(m, r): "MCP servers" for m, r in (
        ("DELETE", "/api/mcp/servers/"), ("GET", "/api/mcp/servers"), ("GET", "/api/mcp/tools"),
        ("PATCH", "/api/mcp/servers/"), ("PUT", "/api/mcp/servers/"))},
    **{("GET", r): _SERVER for r in (
        "/api/dashboard/config", "/api/dashboard/status", "/api/extensions/status", "/api/logs",
        "/api/provider/cost-history", "/api/provider/quota", "/api/providers")},
    **{("POST", r): _SERVER for r in (
        "/api/admin/reload", "/api/commands/exec", "/api/dashboard/config", "/api/default-model",
        "/api/extensions/sidecar-proxy-consent", "/api/extensions/toggle", "/api/gateway/restart",
        "/api/gateway/start", "/api/gateway/stop", "/api/health/restart", "/api/model/set",
        "/api/models/refresh", "/api/providers", "/api/providers/delete", "/api/providers/self-hosted",
        "/api/reasoning", "/api/settings", "/api/shutdown", "/api/csp-report")},
    **{(m, r): "onboarding" for m, r in (
        ("GET", "/api/onboarding/oauth/poll"), ("GET", "/api/onboarding/status"),
        ("POST", "/api/onboarding/complete"), ("POST", "/api/onboarding/oauth/cancel"),
        ("POST", "/api/onboarding/oauth/start"), ("POST", "/api/onboarding/probe"),
        ("POST", "/api/onboarding/setup"))},
    **{("POST", f"/api/profile/{verb}"): "Profile management (names a Profile under `name`)"
       for verb in ("create", "delete", "disable", "enable", "switch")},
    ("GET", "/api/session/recovery/audit"): "audits the whole session store",
    ("POST", "/api/session/recovery/repair-safe"): "repairs the whole session store",
    ("POST", "/api/sessions/cleanup"): "bulk cleanup; names no session",
    ("POST", "/api/sessions/cleanup_zero_message"): "bulk cleanup; names no session",
    ("GET", "/api/share/"): "a share link by its token (prefix route)",
    **{("GET", r): "pages and static prefixes" for r in ("/extensions/", "/login", "/share", "/share/")},
}


def _admin_only_routes() -> set[tuple[str, str]]:
    return {(r.method, r.route) for r in dispatched_routes() if not user_may_call(r.method, r.route)}


def test_every_admin_only_route_is_placed():
    admin_only = _admin_only_routes()
    assert not ADMIN_SESSION_ROUTES & set(ADMIN_NAMES_NO_SESSION)
    unplaced = sorted(admin_only - ADMIN_SESSION_ROUTES - set(ADMIN_NAMES_NO_SESSION))
    assert not unplaced, (
        "Place each Admin-only route in ADMIN_SESSION_ROUTES if it names a session "
        f"(then give its route-table row a session kind), or in ADMIN_NAMES_NO_SESSION with a reason: {unplaced}"
    )
    stale = sorted((ADMIN_SESSION_ROUTES | set(ADMIN_NAMES_NO_SESSION)) - admin_only)
    assert not stale, f"Not an Admin-only route any more: {stale}"


def test_every_session_route_is_a_read_or_a_write():
    missing = sorted((set(SESSION_ROUTES) | ADMIN_SESSION_ROUTES) - set(SESSION_ROUTE_KINDS))
    assert not missing, f"Give each route-table row session=READ or WRITE: {missing}"


def test_the_table_names_only_session_routes():
    stale = sorted(set(SESSION_ROUTE_KINDS) - set(SESSION_ROUTES) - ADMIN_SESSION_ROUTES)
    assert not stale, f"Not a session-naming route any more: {stale}"


def test_each_entry_is_a_read_or_a_write():
    assert set(SESSION_ROUTE_KINDS.values()) <= {READ, WRITE}


@pytest.mark.parametrize("method,path,kind", [
    ("GET", "/api/session", READ),
    ("GET", "/api/session/export", READ),
    ("GET", "/api/chat/stream", READ),
    ("GET", "/api/chat/cancel", WRITE),  # a GET that stops a run
    ("POST", "/api/file/path", READ),  # a POST that only resolves a path
    ("POST", "/api/session/conversation-rounds", READ),
    ("POST", "/api/session/duplicate", WRITE),
    ("POST", "/api/session/branch", WRITE),
    ("POST", "/api/session/handoff-summary", WRITE),  # runs the session's model
    ("POST", "/api/approval/respond", WRITE),
])
def test_routes_are_classified_by_what_they_do(method, path, kind):
    assert SESSION_ROUTE_KINDS[(method, path)] == kind


@pytest.mark.parametrize("method,path,kind", [
    ("GET", "/api/session", READ),
    ("GET", "/api/sessions/abc123/events", READ),
    ("GET", "/session/abc123", READ),
    ("POST", "/api/session/rename", WRITE),
    ("GET", "/api/session/rename", WRITE),  # a method the table does not name: unknown is a write
    ("POST", "/api/no-such-route", None),  # not a session route
])
def test_a_request_finds_its_routes_kind(method, path, kind):
    assert session_route_kind(method, path) == kind
