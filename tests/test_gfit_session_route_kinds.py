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
from tests._route_source import SESSION_ROUTE_KINDS
from tests.test_gfit_admin_gate_list import dispatched_routes
from tests.test_gfit_session_route_answers import SESSION_ROUTES

# Admin-only routes that name a session (a session id in the query or body).
ADMIN_SESSION_ROUTES: set[tuple[str, str]] = set()

# Admin-only routes that name no session, and why.
ADMIN_NAMES_NO_SESSION: dict[tuple[str, str], str] = {
    **{("POST", f"/api/profile/{verb}"): "Profile management (names a Profile under `name`)"
       for verb in ("create", "delete", "disable", "enable", "switch")},
    **{("GET", r): "pages and static prefixes" for r in ("/login",)},
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
