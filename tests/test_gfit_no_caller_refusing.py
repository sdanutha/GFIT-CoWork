"""GFIT-CoWork: a web request with no caller is refused; only code with no caller is unconfined (ticket 07).

ADR 0006: every protected request has a caller. A request thread that has no
Admission (a public route, or a session whose Admission was not run) must
get the refusing answer, never the unconfined rules: those read any Profile
and name the owning Profile of a session (409 ``session_profile_mismatch``).
The unconfined adapter and policy stay for code that genuinely has no caller
and never answers HTTP: worker threads, startup, and the session-list cache
builder (an explicit no-caller block).
"""
from __future__ import annotations

import pytest

from tests._gfit_server import gfit_server as _gfit_server

ALICE = "521740"
BOB = "671278"


@pytest.fixture
def srv(monkeypatch, tmp_path):
    with _gfit_server(monkeypatch, tmp_path, users={ALICE: "Alice", BOB: "Bob"}, profile_names=[ALICE, BOB]) as s:
        yield s


def _alices_session(srv) -> str:
    alice = srv.logged_in(ALICE)
    status, body, _ = alice.post("/api/session/new", {})
    assert status == 200, body
    return body["session"]["session_id"]


def _names_no_owner(body) -> None:
    text = str(body)
    assert ALICE not in text, body
    assert "session_profile_mismatch" not in text, body


# ── HTTP: a request with no caller ──────────────────────────────────────────

def test_a_login_request_naming_a_session_does_not_learn_its_owner(srv):
    sid = _alices_session(srv)
    anonymous = srv.client()

    status, body, _ = anonymous.post(
        "/api/auth/login", {"username": BOB, "password": "wrong", "session_id": sid})

    assert status != 409, body
    _names_no_owner(body)


def test_a_failed_login_naming_a_session_is_answered_like_any_failed_login(srv):
    sid = _alices_session(srv)

    named = srv.client().post("/api/auth/login", {"username": BOB, "password": "wrong", "session_id": sid})
    unnamed = srv.client().post("/api/auth/login", {"username": BOB, "password": "wrong"})

    assert named[:2] == unnamed[:2]


def test_a_user_asking_for_another_profiles_session_gets_not_found_without_its_owner(srv):
    sid = _alices_session(srv)
    bob = srv.logged_in(BOB)

    status, body, _ = bob.get(f"/api/session?session_id={sid}")

    assert status == 404, body
    _names_no_owner(body)


def test_login_and_logout_still_work(srv):
    alice = srv.logged_in(ALICE)
    assert alice.get("/api/sessions")[0] == 200
    assert alice.post("/api/auth/logout")[0] == 200
    assert alice.get("/api/sessions")[0] == 401


def test_no_public_route_runs_the_session_guard():
    from api import route_table

    guarded = [f"{r.method} {r.pattern}" for r in route_table.ROUTES
               if r.caller == route_table.PUBLIC and r.session_guard]
    assert guarded == []


def test_a_public_route_cannot_be_declared_with_the_session_guard():
    from api import route_table

    with pytest.raises(ValueError):
        route_table.Route("POST", "/api/example", route_table.PUBLIC, handler="_x", session_guard=True)


# ── The request thread: no Admission is refused ─────────────────────────────

class _Handler:
    """A stand-in request handler: the server's per-request hooks only need an object."""

    command = "GET"


@pytest.fixture
def request_thread():
    """This thread is serving a request, as server.py marks it, with no Admission recorded."""
    from api.auth import reset_request_auth_state
    from api.profiles import clear_request_profile

    reset_request_auth_state(_Handler())
    try:
        yield
    finally:
        clear_request_profile()


def test_a_request_with_no_admission_gets_the_refusing_session_ownership(request_thread):
    from api import session_ownership

    assert session_ownership.request_session_ownership() is session_ownership.REFUSING


def test_a_request_with_no_admission_gets_the_refusing_workspace_policy(request_thread):
    from api import workspace_policy

    assert workspace_policy.request_workspace_policy() is workspace_policy.REFUSING


def test_code_with_no_caller_keeps_the_unconfined_rules():
    from api import session_ownership, workspace_policy

    assert session_ownership.request_session_ownership() is session_ownership.UNCONFINED
    assert workspace_policy.request_workspace_policy() is workspace_policy.UNCONFINED


def test_a_thread_a_request_starts_has_no_caller(request_thread):
    import threading

    from api import session_ownership

    seen = []
    worker = threading.Thread(target=lambda: seen.append(session_ownership.request_session_ownership()))
    worker.start()
    worker.join()
    assert seen == [session_ownership.UNCONFINED]
    assert session_ownership.request_session_ownership() is session_ownership.REFUSING


def test_the_session_list_cache_builder_is_the_one_no_caller_block_in_a_request(request_thread):
    from api import session_ownership
    from api.access import without_request_admission

    with without_request_admission():
        assert session_ownership.request_session_ownership() is session_ownership.UNCONFINED
    assert session_ownership.request_session_ownership() is session_ownership.REFUSING

    with pytest.raises(RuntimeError):
        with without_request_admission():
            raise RuntimeError("builder failed")
    assert session_ownership.request_session_ownership() is session_ownership.REFUSING


def test_the_request_mark_ends_with_the_request():
    from api import session_ownership
    from api.auth import reset_request_auth_state
    from api.profiles import clear_request_profile

    reset_request_auth_state(_Handler())
    clear_request_profile()
    assert session_ownership.request_session_ownership() is session_ownership.UNCONFINED


# ── Refusals never name an owner ────────────────────────────────────────────

def test_the_unconfined_refusal_of_another_profiles_session_names_no_owner(monkeypatch):
    from api import session_ownership

    found = {"session_id": "abc123", "profile": "someone-else"}
    monkeypatch.setattr("api.profiles.get_active_profile_name", lambda: "default")
    refusal = session_ownership.UNCONFINED.refuse_found_session("abc123", found)

    assert refusal is session_ownership.NOT_FOUND


def test_no_answer_in_the_server_names_a_sessions_owning_profile():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    hits = [p.relative_to(root).as_posix() for p in [*(root / "api").glob("*.py"), root / "server.py"]
            if "session_profile_mismatch" in p.read_text(encoding="utf-8")]
    assert hits == []


# ── The route gate fails closed ─────────────────────────────────────────────

def test_the_route_gate_refuses_a_session_that_is_not_a_directory_session():
    import io
    from urllib.parse import urlparse

    from api.auth import _refuse_unlisted_route

    class Handler(_Handler):
        def __init__(self):
            self.status = None
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

    handler = Handler()
    refused = _refuse_unlisted_route(handler, urlparse("/api/sessions"), {"auth_type": "password"})

    assert refused is True
    assert handler.status == 403


def test_a_request_with_no_caller_reads_the_deployments_settings_and_no_profile(request_thread, monkeypatch):
    # The login page and login itself read settings (language, session TTL):
    # the Deployment's only, never a Profile's config.
    from api import config

    def no_profile(*_args, **_kwargs):
        raise AssertionError("a request with no caller read a Profile's model config")

    monkeypatch.setattr(config, "get_effective_default_model", no_profile)
    monkeypatch.setattr(config, "get_config", no_profile)
    settings = config.load_settings()

    assert "language" in settings
    assert "default_model_provider" not in settings
