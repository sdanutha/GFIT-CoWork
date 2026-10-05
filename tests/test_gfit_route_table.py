"""GFIT-CoWork: the route table answers what the lists it replaced answered, and
the server dispatches every request through it.

The table was built from the Admin gate's User and Admin-only lists, session
ownership's read/write list and the CSRF exemption. Their answers for every route and probe path were captured
before they were removed (``fixtures/gfit_route_answers_before_the_table.json``);
the table must give the same answers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import route_table
from api.access import user_may_call
from api.route_table import ADMIN, READ, USER, WRITE, Route
from api.session_ownership import session_route_kind

CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures" / "gfit_route_answers_before_the_table.json").read_text(encoding="utf-8")
)




# Routes ADR 0006 opened to Users on purpose (they act in the User's own Profile).
_OPENED_TO_USERS = {
    ("POST", "/api/settings"), ("POST", "/api/default-model"), ("POST", "/api/model/set"), ("POST", "/api/reasoning"),
}


# Handlers of the Admin-only features ADR 0006 deleted: their routes are gone.
_DELETED_BY_ADR_0006 = {
    "_post_api_csp_report", "_get_share_page", "_get_api_share", "_post_api_share_create", "_post_api_share_revoke",
    "_get_api_kanban", "_post_api_kanban", "_patch_api_kanban", "_delete_api_kanban",
    "_get_api_dashboard_status", "_get_api_dashboard_config", "_post_api_dashboard_config",
    "_get_api_extensions_status", "_get_extensions", "_post_api_extensions_toggle",
    "_post_api_extensions_sidecar_proxy_consent",
    "_get_api_session_recovery_audit", "_post_api_session_recovery_repair_safe", "_post_api_admin_reload",
    "_post_api_sessions_cleanup", "_post_api_sessions_cleanup_zero_message",
    "_get_api_approval_inject_test", "_get_api_clarify_inject_test",
}
# Path prefixes of those deleted routes: they now answer like any unknown path.
_DELETED_PATHS_BY_ADR_0006 = ("/share", "/api/share/", "/api/kanban/", "/api/dashboard/", "/api/extensions/", "/extensions/", "/api/csp-report",
                              "/api/session/recovery/", "/api/admin/reload", "/api/sessions/cleanup",
                              "/api/approval/inject_test", "/api/clarify/inject_test")


def _intended(method, path, user_may, kind):
    """The answers the table changes on purpose: a session page's static
    assets and manifest name no session (the old list said READ only because
    its ``/session/*`` prefix also caught them), and the routes ADR 0006
    opened to Users."""
    if (method, path) in _OPENED_TO_USERS:
        return True, kind
    if path.startswith(_DELETED_PATHS_BY_ADR_0006):
        return False, None
    if path.startswith("/session/static/") or path in ("/session/manifest.json", "/session/manifest.webmanifest"):
        return user_may, None
    return user_may, kind


@pytest.mark.parametrize("method,path,user_may,kind", CAPTURED)
def test_the_table_answers_what_the_old_lists_answered(method, path, user_may, kind):
    expected = _intended(method, path, user_may, kind)
    assert (user_may_call(method, path), session_route_kind(method, path)) == expected


def test_a_row_must_say_who_may_call_it():
    with pytest.raises(TypeError):
        Route("GET", "/api/new")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Route("GET", "/api/new", "anyone", handler="_get_new")


def test_a_row_must_name_its_handler():
    with pytest.raises(ValueError):
        Route("GET", "/api/new", USER)


def test_a_row_names_a_known_method_session_kind_and_body():
    with pytest.raises(ValueError):
        Route("HEAD", "/api/new", USER, handler="_get_new")
    with pytest.raises(ValueError):
        Route("GET", "/api/new", USER, session="maybe", handler="_get_new")
    with pytest.raises(ValueError):
        Route("POST", "/api/new", USER, body="form", handler="_post_new")


def test_every_route_appears_once():
    seen = [(route.method, route.pattern) for route in route_table.ROUTES]
    assert len(seen) == len(set(seen))


def test_a_user_prefix_route_has_a_reason():
    prefixes = {route.pattern for route in route_table.ROUTES if route.caller == USER and route.is_prefix}
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


def test_the_table_classifies_reads_writes_and_callers():
    assert route_table.match("GET", "/api/session").session == READ
    assert route_table.match("POST", "/api/session/rename").session == WRITE
    assert route_table.match("POST", "/api/session/yolo").caller == ADMIN
    assert route_table.match("GET", "/api/session/yolo").caller == USER


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
    if handler in _DELETED_BY_ADR_0006:
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


def test_over_http_a_cross_site_put_gets_the_same_answer_as_a_cross_site_post(monkeypatch, tmp_path):
    from tests._gfit_server import gfit_server

    with gfit_server(monkeypatch, tmp_path, users={"admin1": "Admin"}, admins="admin1") as server:
        admin = server.logged_in("admin1")
        cross_site = {"Origin": "http://elsewhere.example"}
        answers = {
            method: admin.request(method, "/api/mcp/servers/demo", {}, headers=cross_site)[:2]
            for method in ("POST", "PUT", "PATCH", "DELETE")
        }
    assert answers["PUT"] == answers["POST"] == answers["PATCH"] == answers["DELETE"]
    assert answers["PUT"][0] == 403
