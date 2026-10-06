"""GFIT-CoWork: the route table answers what the lists it replaced answered, and
the server dispatches every request through it.

The table was built from the Admin gate's User and Admin-only lists, session
ownership's read/write list and the CSRF exemption. Their answers for every route and probe path were captured
before they were removed (``fixtures/gfit_route_answers_before_the_table.json``);
the table must give the same answers to "may a User call it?", except where
ADR 0006 changed them on purpose. There is no Admin, so every row is a
User's or public; the read/write classification went with the Admin's
read-only view.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import route_table
from api.access import user_may_call
from api.route_table import PUBLIC, USER, Route

CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures" / "gfit_route_answers_before_the_table.json").read_text(encoding="utf-8")
)




# Routes ADR 0006 opened to Users on purpose (they act in the User's own
# Profile), and the login page, which is public now that there is no Admin row.
_OPENED_TO_USERS = {
    ("POST", "/api/settings"), ("POST", "/api/default-model"), ("POST", "/api/model/set"), ("POST", "/api/reasoning"),
    ("GET", "/login"),
}


# Paths of the Admin-only features ADR 0006 deleted: they now answer like any
# unknown path, and their handlers are gone. Listed explicitly so a route that
# disappears by accident still fails the dispatch test below.
_DELETED_PATHS_BY_ADR_0006 = (
    "/share", "/api/share/", "/api/kanban/", "/api/dashboard/", "/api/extensions/", "/extensions/",
    "/api/csp-report", "/api/session/recovery/", "/api/admin/reload", "/api/sessions/cleanup",
    "/api/approval/inject_test", "/api/clarify/inject_test", "/api/terminal/", "/api/session/worktree/remove",
    "/api/logs", "/api/health/restart", "/api/gateway/start", "/api/gateway/stop", "/api/gateway/restart",
    "/api/shutdown", "/api/session/yolo", "/api/git/stage", "/api/git/unstage", "/api/git/discard",
    "/api/git/commit", "/api/git/fetch", "/api/git/pull", "/api/git/push", "/api/git/checkout",
    "/api/git/stash-checkout", "/api/escape/", "/api/file/reveal", "/api/file/open-vscode",
    "/api/commands/exec", "/api/mcp/", "/api/providers", "/api/provider/", "/api/models/refresh",
    "/api/onboarding/", "/api/profile/switch", "/api/profile/create", "/api/profile/disable",
    "/api/profile/enable", "/api/profile/delete",
)


def _intended(method, path, user_may):
    """The answers ADR 0006 changed on purpose: routes opened to Users, and deleted routes."""
    if (method, path) in _OPENED_TO_USERS:
        return True
    if path.startswith(_DELETED_PATHS_BY_ADR_0006):
        return False
    return user_may


@pytest.mark.parametrize("method,path,user_may,kind", CAPTURED)
def test_the_table_answers_what_the_old_lists_answered(method, path, user_may, kind):
    assert user_may_call(method, path) == _intended(method, path, user_may)


def test_a_row_must_say_who_may_call_it():
    with pytest.raises(TypeError):
        Route("GET", "/api/new")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Route("GET", "/api/new", "anyone", handler="_get_new")


def test_a_row_must_name_its_handler():
    with pytest.raises(ValueError):
        Route("GET", "/api/new", USER)


def test_a_row_names_a_known_method_and_body():
    with pytest.raises(ValueError):
        Route("HEAD", "/api/new", USER, handler="_get_new")
    with pytest.raises(ValueError):
        Route("POST", "/api/new", USER, body="form", handler="_post_new")


def test_every_route_appears_once():
    seen = [(route.method, route.pattern) for route in route_table.ROUTES]
    assert len(seen) == len(set(seen))


def test_a_user_prefix_route_has_a_reason():
    prefixes = {route.pattern for route in route_table.ROUTES if route.is_prefix}
    assert prefixes == set(route_table.VARIABLE_PATH_PREFIXES)


def test_only_login_and_the_gone_ack_are_csrf_exempt():
    exempt = {(route.method, route.pattern) for route in route_table.ROUTES if not route.csrf}
    # The deprecated ack answers 410 Gone to a stale tab that carries no token.
    assert exempt == {("POST", "/api/auth/login"), ("POST", "/api/process-complete-ack")}
    assert route_table.match("POST", "/api/session/new").csrf


@pytest.mark.parametrize(
    "method,path,pattern",
    [
        ("GET", "/session/static/ui.js", "/session/static/*"),  # the longer prefix
        ("GET", "/session/manifest.json", "/session/manifest.json"),  # exact beats prefix
        ("GET", "/session/20260927_abc", "/session/*"),
        ("GET", "/api/sessions/abc/events", "/api/sessions/<id>/events"),
        ("GET", "/api/sessions/a/b/events", None),
        ("GET", "/api/sessions//events", None),
        ("get", "/api/session", "/api/session"),
        ("POST", "/api/session", None),
    ],
)
def test_one_matcher_chooses_the_row(method, path, pattern):
    route = route_table.match(method, path)
    assert (route.pattern if route else None) == pattern


def test_every_row_is_a_users_or_public_there_is_no_admin_row():
    assert {route.caller for route in route_table.ROUTES} == {USER, PUBLIC}
    assert not hasattr(route_table, "ADMIN")
    with pytest.raises(ValueError):
        Route("GET", "/api/new", "admin", handler="_get_new")


def test_the_public_rows_are_the_login_page_and_what_it_needs():
    public = {(route.method, route.pattern) for route in route_table.ROUTES if route.caller == PUBLIC}
    assert public == {
        ("GET", "/login"), ("POST", "/api/auth/login"), ("GET", "/api/auth/status"),
        ("GET", "/health"), ("GET", "/favicon.ico"), ("GET", "/sw.js"),
        ("GET", "/manifest.json"), ("GET", "/manifest.webmanifest"),
        ("GET", "/session/manifest.json"), ("GET", "/session/manifest.webmanifest"),
        ("GET", "/static/*"), ("GET", "/session/static/*"),
    }


@pytest.mark.parametrize("path,public", [
    ("/login", True), ("/static/ui.js", True), ("/session/static/ui.js", True), ("/health", True),
    ("/", False), ("/session/abc", False), ("/api/session", False), ("/api/auth/logout", False),
    ("/api/profile/create", False), ("/nowhere", False),
])
def test_the_login_check_lets_through_only_public_paths(path, public):
    assert route_table.is_public(path) is public


# ── Dispatch: the table chooses the handler the old if-chains chose ──────────

HANDLERS_BEFORE = json.loads(
    (Path(__file__).parent / "fixtures" / "gfit_route_handlers_before_the_table.json").read_text(encoding="utf-8")
)




@pytest.mark.parametrize(
    "method,path,handler",
    [(method, path, name) for method, paths in HANDLERS_BEFORE.items() for path, name in paths.items()],
)
def test_the_table_dispatches_where_the_old_chains_did(method, path, handler):
    import api.routes as routes

    route = route_table.match(method, path)
    if path.startswith(_DELETED_PATHS_BY_ADR_0006):
        assert route is None or route.handler != handler
        assert not hasattr(routes, handler)
        return
    assert route is not None and route.handler == handler
    assert callable(getattr(routes, handler))


def test_every_row_names_a_handler_in_the_route_module():
    import api.routes as routes

    missing = [
        f"{route.method} {route.pattern}"
        for route in route_table.ROUTES
        if not callable(getattr(routes, route.handler, None))
    ]
    assert not missing


def test_a_patched_handler_takes_effect(monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import urlparse

    import api.routes as routes

    seen = []
    monkeypatch.setattr(routes, "_get_health", lambda handler, parsed: seen.append(parsed.path) or True)
    assert routes.handle_get(SimpleNamespace(headers={}), urlparse("/health")) is True
    assert seen == ["/health"]


class _Handler:
    """Just enough of a request handler for the write preamble's CSRF refusal."""

    def __init__(self, headers):
        self.headers = headers
        self.status = None
        self.body = bytearray()
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)


def test_a_cross_site_put_is_refused_with_the_same_message_as_a_post(monkeypatch):
    from types import SimpleNamespace

    import api.routes as routes

    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "memory")
    answers = {}
    for method, dispatch in (("POST", routes.handle_post), ("PUT", routes.handle_put),
                             ("PATCH", routes.handle_patch), ("DELETE", routes.handle_delete)):
        handler = _Handler({"Origin": "http://example.com", "Host": "example.com", "Content-Length": "2"})
        handler.command = method
        dispatch(handler, SimpleNamespace(path="/api/mcp/servers/demo", query=""))
        answers[method] = (handler.status, json.loads(bytes(handler.body))["error"])
    assert answers["PUT"] == answers["POST"] == answers["PATCH"] == answers["DELETE"]
    assert answers["PUT"][0] == 403


def test_over_http_a_cross_site_write_is_refused_whatever_its_method(monkeypatch, tmp_path):
    # A User's PUT/PATCH/DELETE to a route with no such row is refused by the
    # route gate before the CSRF check; the POST by the CSRF check.
    from tests._gfit_server import gfit_server

    with gfit_server(monkeypatch, tmp_path, users={"521740": "User"}, profile_names=["521740"]) as server:
        user = server.logged_in("521740")
        cross_site = {"Origin": "http://elsewhere.example"}
        answers = {
            method: user.request(method, "/api/session/rename", {}, headers=cross_site)[:2]
            for method in ("POST", "PUT", "PATCH", "DELETE")
        }
    assert {status for status, _ in answers.values()} == {403}
    assert "Cross-origin" in answers["POST"][1]["error"]
