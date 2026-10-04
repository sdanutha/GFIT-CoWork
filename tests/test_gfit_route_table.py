"""GFIT-CoWork: the route table answers what the five lists it replaced answered.

Architecture review round 6, candidate 1. The table was built from the Admin
gate's User and Admin-only lists, session ownership's read/write list and the
CSRF exemption. Their answers for every route and probe path were captured
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




def _intended(method, path, user_may, kind):
    """The one answer the table changes on purpose: a session page's static
    assets and manifest name no session. The old list said READ only because
    its ``/session/*`` prefix also caught them."""
    if path.startswith("/session/static/") or path.startswith("/session/manifest."):
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
        Route("GET", "/api/new", "anyone")


def test_a_row_names_a_known_method_and_session_kind():
    with pytest.raises(ValueError):
        Route("HEAD", "/api/new", USER)
    with pytest.raises(ValueError):
        Route("GET", "/api/new", USER, session="maybe")


def test_every_route_appears_once():
    seen = [(route.method, route.pattern) for route in route_table.ROUTES]
    assert len(seen) == len(set(seen))


def test_a_user_prefix_route_has_a_reason():
    prefixes = {route.pattern for route in route_table.ROUTES if route.caller == USER and route.is_prefix}
    assert prefixes == set(route_table.VARIABLE_PATH_PREFIXES)


def test_only_login_and_csp_reports_are_csrf_exempt():
    exempt = {(route.method, route.pattern) for route in route_table.ROUTES if not route.csrf}
    assert exempt == {("POST", "/api/auth/login"), ("POST", "/api/csp-report")}
    assert route_table.csrf_exempt("POST", "/api/auth/login")
    assert not route_table.csrf_exempt("POST", "/api/session/new")
    assert not route_table.csrf_exempt("POST", "/api/not-a-route")


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
