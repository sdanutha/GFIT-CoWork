"""GFIT-CoWork: the Admin gate's list of what a User may call.

Tested at the gate's check interface (method + route -> may a User call it),
with no server. The table states what a User may call; the completeness test
reads the routes the server dispatches on and fails when a route a User can
reach is not named exactly on the list (ADR 0002: a User reaches only their own
Profile, server-level features are for the Admin).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

from api.access import (
    LEGACY_PREFIXES,
    MEMBER_ENDPOINTS,
    VARIABLE_PATH_PREFIXES,
    member_entry,
    member_may_call,
)

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
    assert member_may_call(method, route) is expected


# The completeness test. The routes module dispatches each HTTP method in one
# function, comparing ``parsed.path`` against literal routes.
ROUTES_MODULE = Path(__file__).resolve().parent.parent / "api" / "routes.py"
DISPATCHERS = {
    "handle_get": "GET",
    "handle_post": "POST",
    "handle_put": "PUT",
    "handle_patch": "PATCH",
    "handle_delete": "DELETE",
}


class DispatchedRoute(NamedTuple):
    method: str
    route: str
    prefix: bool  # dispatched with ``startswith``: every route under it


def _is_request_path(node) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "parsed"
    )


def _string_literals(node) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [value for element in node.elts for value in _string_literals(element)]
    return []


# Routes a dispatcher matches without a literal, so the scan below cannot see
# them. A variable part is written as <id>.
NON_LITERAL_ROUTES = {
    # handle_get -> _session_events_path_session_id splits the path
    DispatchedRoute("GET", "/api/sessions/<id>/events", False),
}


def dispatched_routes() -> set[DispatchedRoute]:
    """Every route the dispatchers handle: the literal routes they compare the
    request path against (equality, membership in a literal set, literal prefix
    checks), plus NON_LITERAL_ROUTES. A new route matched some other way (a
    regex, a split, another variable) is invisible here until listed there."""
    tree = ast.parse(ROUTES_MODULE.read_text(encoding="utf-8"))
    routes = set(NON_LITERAL_ROUTES)
    for function in tree.body:
        method = DISPATCHERS.get(getattr(function, "name", None))
        if method is None:
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Compare) and _is_request_path(node.left):
                for op, comparator in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                        routes.update(
                            DispatchedRoute(method, route, False)
                            for route in _string_literals(comparator)
                        )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and _is_request_path(node.func.value)
            ):
                routes.update(
                    DispatchedRoute(method, route, True)
                    for argument in node.args
                    for route in _string_literals(argument)
                )
    return routes


def test_the_dispatched_routes_are_found():
    routes = dispatched_routes()
    assert DispatchedRoute("GET", "/api/session", False) in routes
    assert DispatchedRoute("POST", "/api/session/new", False) in routes
    assert DispatchedRoute("GET", "/api/auth/oidc/start", False) in routes  # literal set
    assert DispatchedRoute("GET", "/static/", True) in routes
    assert DispatchedRoute("DELETE", "/api/prompts", False) in routes


def test_every_route_a_user_can_reach_is_named_exactly():
    unnamed = []
    for method, route, prefix in sorted(dispatched_routes()):
        # A prefix route stands for the routes under it: probe one of them.
        entry = member_entry(method, route + "x" if prefix else route)
        if entry is None or not entry.endswith("*"):
            continue
        if entry in VARIABLE_PATH_PREFIXES or entry in LEGACY_PREFIXES:
            continue
        unnamed.append(
            f"{method} {route}{'*' if prefix else ''} reaches Users through the prefix "
            f"{entry}: add it to the User list (MEMBER_ENDPOINTS) as an exact route, "
            "or leave it Admin-only (off the User list, or carved out in "
            "ADMIN_ONLY_ENDPOINTS)"
        )
    assert not unnamed, "\n".join(unnamed)


def test_every_exact_user_entry_is_a_route_the_server_handles():
    handled = {(r.method, r.route) for r in dispatched_routes() if not r.prefix}
    dead = [
        f"{method} {route} is on the User list but the server does not handle it: "
        "remove it from MEMBER_ENDPOINTS"
        for methods, route in MEMBER_ENDPOINTS
        if not route.endswith("*")
        for method in sorted(methods)
        if (method, route) not in handled
    ]
    assert not dead, "\n".join(dead)
