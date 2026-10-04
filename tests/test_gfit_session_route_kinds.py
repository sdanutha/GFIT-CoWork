"""GFIT-CoWork: every route that names a session is a read or a write, in one table.

Session ownership classifies each (method, route) that names a session by
what it does, not its method (``api.session_ownership.SESSION_ROUTE_KINDS``):
the Admin may read another Profile's session in place but not write to it.
This test fails when a session-naming route (the routes placed in
``tests/test_gfit_session_route_answers.py``) has no class, or when the table
names a route that no longer names a session.
"""
from __future__ import annotations

import pytest

from api.session_ownership import READ, SESSION_ROUTE_KINDS, WRITE, session_route_kind
from tests.test_gfit_session_route_answers import SESSION_ROUTES


def test_every_session_route_is_a_read_or_a_write():
    missing = sorted(set(SESSION_ROUTES) - set(SESSION_ROUTE_KINDS))
    assert not missing, f"Classify each as READ or WRITE in SESSION_ROUTE_KINDS: {missing}"


def test_the_table_names_only_session_routes():
    stale = sorted(set(SESSION_ROUTE_KINDS) - set(SESSION_ROUTES))
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
