"""GFIT-CoWork: the Admin gate's list of what a User may call.

Tested at the gate's check interface (method + route -> may a User call it),
with no server. The table states what a User may call; the completeness test
reads the route table the server dispatches through and fails when a route a
User can reach is not named by its own row (ADR 0002: a User reaches only
their own Profile, server-level features are for the Admin).
"""
from __future__ import annotations

from typing import NamedTuple

import pytest

from api import route_table
from api.access import user_entry, user_may_call
from api.route_table import VARIABLE_PATH_PREFIXES

ALLOWED = True
REFUSED = False

GATE_TABLE = [
    # Representative User routes
    ("GET", "/", ALLOWED),
    ("GET", "/api/session", ALLOWED),
    ("POST", "/api/session/new", ALLOWED),
    ("POST", "/api/session/rename", ALLOWED),
    ("POST", "/api/session/import_cli", ALLOWED),
    ("GET", "/api/sessions", ALLOWED),
    ("POST", "/api/chat/start", ALLOWED),
    ("GET", "/api/file", ALLOWED),
    ("POST", "/api/file/save", ALLOWED),
    ("GET", "/api/memory", ALLOWED),
    ("POST", "/api/memory/write", ALLOWED),
    ("POST", "/api/upload", ALLOWED),
    ("DELETE", "/api/prompts", ALLOWED),
    # Closed because they reach every Profile or the server machine
    ("POST", "/api/sessions/cleanup", REFUSED),
    ("POST", "/api/sessions/cleanup_zero_message", REFUSED),
    ("GET", "/api/session/recovery/audit", REFUSED),
    ("POST", "/api/session/recovery/repair-safe", REFUSED),
    ("POST", "/api/file/reveal", REFUSED),
    # Admin-only routes under a prefix Users otherwise reach
    ("POST", "/api/session/yolo", REFUSED),
    ("POST", "/api/session/worktree/remove", REFUSED),
    ("POST", "/api/file/open-vscode", REFUSED),
    # Variable-path routes
    ("GET", "/static/ui.js", ALLOWED),
    ("GET", "/session/20260927_abc123", ALLOWED),
    ("GET", "/plugins/example/app.js", ALLOWED),
    ("GET", "/session/static/ui.js", ALLOWED),
    # Literal routes under a variable-path prefix, named exactly
    ("GET", "/session/manifest.json", ALLOWED),
    ("GET", "/session/manifest.webmanifest", ALLOWED),
    # Session and chat routes, named exactly
    ("POST", "/api/session/undo", ALLOWED),
    ("GET", "/api/session/export", ALLOWED),
    ("GET", "/api/sessions/search", ALLOWED),
    ("GET", "/api/chat/stream", ALLOWED),
    ("GET", "/api/background/status", ALLOWED),
    ("GET", "/api/sessions/20260927_abc123/events", ALLOWED),  # one session id segment
    ("GET", "/api/sessions/a/b/events", REFUSED),
    ("GET", "/api/sessions//events", REFUSED),
    ("GET", "/api/session/new", REFUSED),  # POST only
    ("POST", "/api/chat/stream", REFUSED),  # GET only
    ("GET", "/api/session/not-a-route", REFUSED),
    # File, Workspace, rollback and project routes, named exactly
    ("POST", "/api/file/rename", ALLOWED),
    ("POST", "/api/file/path", ALLOWED),
    ("POST", "/api/workspaces/add", ALLOWED),
    ("GET", "/api/rollback/list", ALLOWED),
    ("GET", "/api/rollback/diff", ALLOWED),
    ("POST", "/api/projects/create", ALLOWED),
    ("GET", "/api/rollback/restore", REFUSED),  # POST only
    ("POST", "/api/file/not-a-route", REFUSED),
    # Cron, skill, command, wiki and note routes, named exactly
    ("POST", "/api/crons/create", ALLOWED),
    ("GET", "/api/crons/run", ALLOWED),
    ("POST", "/api/crons/run", ALLOWED),
    ("POST", "/api/skills/save", ALLOWED),
    ("GET", "/api/commands/bundles", ALLOWED),
    ("GET", "/api/wiki/page", ALLOWED),
    ("GET", "/api/notes/search", ALLOWED),
    ("GET", "/api/crons/create", REFUSED),  # POST only
    ("GET", "/api/skills/save", REFUSED),  # POST only
    ("POST", "/api/commands/exec", REFUSED),  # server-side agent commands
    ("GET", "/api/wiki/not-a-route", REFUSED),
    # Unknown routes are refused (fail closed)
    ("GET", "/api/not-a-route", REFUSED),
    ("POST", "/api/not-a-route", REFUSED),
    ("GET", "/not-a-page", REFUSED),
    # Known routes with the wrong method are refused
    ("POST", "/api/memory", REFUSED),
    ("GET", "/api/memory/write", REFUSED),
    ("DELETE", "/api/models", REFUSED),
    ("PUT", "/api/upload", REFUSED),
]


@pytest.mark.parametrize("method,route,expected", GATE_TABLE)
def test_what_a_user_may_call(method, route, expected):
    assert user_may_call(method, route) is expected


# The completeness test. The server dispatches every request through the route
# table, so the table's rows are the routes it handles.


class DispatchedRoute(NamedTuple):
    method: str
    route: str
    prefix: bool  # a prefix row: every route under it


def dispatched_routes() -> set[DispatchedRoute]:
    """Every route the server dispatches: the route table's rows."""
    return {
        DispatchedRoute(row.method, row.pattern[:-1] if row.is_prefix else row.pattern, row.is_prefix)
        for row in route_table.ROUTES
    }


def test_the_dispatched_routes_are_found():
    routes = dispatched_routes()
    assert DispatchedRoute("GET", "/api/session", False) in routes
    assert DispatchedRoute("POST", "/api/session/new", False) in routes
    assert DispatchedRoute("GET", "/session/manifest.json", False) in routes  # literal set
    assert DispatchedRoute("GET", "/static/", True) in routes
    assert DispatchedRoute("DELETE", "/api/prompts", False) in routes
    assert DispatchedRoute("GET", "/api/sessions/<id>/events", False) in routes


def test_every_route_a_user_can_reach_is_named_exactly():
    unnamed = []
    for method, route, prefix in sorted(dispatched_routes()):
        # A prefix route stands for the routes under it: probe one of them.
        entry = user_entry(method, route + "x" if prefix else route)
        if entry is None or not entry.endswith("*"):
            continue
        # A prefix route may use only its own variable-path entry.
        if prefix and entry == route + "*" and entry in VARIABLE_PATH_PREFIXES:
            continue
        unnamed.append(
            f"{method} {route}{'*' if prefix else ''} reaches Users through the prefix "
            f"{entry}: give it its own route-table row "
            "(a User prefix row also needs its reason in VARIABLE_PATH_PREFIXES)"
        )
    assert not unnamed, "\n".join(unnamed)
