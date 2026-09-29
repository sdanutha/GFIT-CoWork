"""GFIT-CoWork: every request carries its Admission, and nothing more.

Admission runs again on every request from a Directory session and its answer
(role and Profile) is what the rest of the request asks "who is calling?". These
tests pin what a client sees for each caller: the page shell, the Admin gate,
Profile binding and Workspace confinement. They also show that one request's
caller never carries over to the next request on the same keep-alive connection
(one handler object and thread). With login turned off there is no caller:
nothing is pinned or confined.
HTTP tests against an in-process server (see ``tests/_gfit_server.py``).
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

import server
from api.access import ADMIN_ONLY_MESSAGE, request_admission
from api.auth import COOKIE_NAME
from tests._gfit_server import gfit_server as _gfit_server

USER = "521740"
OTHER_USER = "671278"
ADMIN = "600001"

EXTENSION_SCRIPT = "/extensions/gfit-request-admission.js"


@pytest.fixture
def extensions_on(monkeypatch, tmp_path):
    root = tmp_path / "extensions"
    root.mkdir()
    monkeypatch.setenv("HERMES_WEBUI_EXTENSION_DIR", str(root))
    monkeypatch.setenv("HERMES_WEBUI_EXTENSION_SCRIPT_URLS", EXTENSION_SCRIPT)


@pytest.fixture
def srv(monkeypatch, tmp_path, extensions_on):
    users = {USER: "User One", OTHER_USER: "User Two", ADMIN: "Admin One"}
    with _gfit_server(
        monkeypatch, tmp_path, users=users, profile_names=[USER, OTHER_USER], admins=ADMIN,
    ) as s:
        yield s


@pytest.fixture
def login_off(monkeypatch, tmp_path, extensions_on):
    with _gfit_server(
        monkeypatch, tmp_path, users={}, profile_names=[USER, OTHER_USER], directory="",
    ) as s:
        yield s


class KeepAlive:
    """One HTTP/1.1 connection, reused for every request: one handler, one thread."""

    def __init__(self, port: int):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)

    def request(self, method, path, *, session=None, body=None, last=False):
        """Send one request. Unless it is the *last*, the connection must stay open:
        a reconnect would move the next request to a fresh handler and thread."""
        headers = {"Cookie": f"{COOKIE_NAME}={session}"} if session else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        self.conn.request(method, path, body=data, headers=headers)
        resp = self.conn.getresponse()
        raw = resp.read()
        assert last or not resp.will_close, "the server closed the keep-alive connection"
        try:
            return resp.status, json.loads(raw) if raw else None
        except ValueError:
            return resp.status, raw.decode("utf-8", "replace")

    def close(self):
        self.conn.close()


@pytest.fixture
def conn(srv):
    c = KeepAlive(srv.port)
    yield c
    c.close()


def _session(srv, uid) -> str:
    return srv.logged_in(uid).cookies[COOKIE_NAME]


# ── The page shell ───────────────────────────────────────────────────────────

def test_a_users_page_shell_carries_the_user_role_and_no_extensions(srv):
    status, html, _ = srv.logged_in(USER).get("/")
    assert status == 200
    assert '<html data-gfit-role="member" ' in html
    assert EXTENSION_SCRIPT not in html


def test_the_admins_page_shell_carries_the_admin_role_and_extensions(srv):
    status, html, _ = srv.logged_in(ADMIN).get("/")
    assert status == 200
    assert '<html data-gfit-role="admin" ' in html
    assert EXTENSION_SCRIPT in html


# ── One request's caller never reaches the next ──────────────────────────────

def test_an_admin_request_after_a_user_request_on_one_connection_is_the_admins(srv, conn):
    user, admin = _session(srv, USER), _session(srv, ADMIN)

    status, body = conn.request("GET", "/api/logs", session=user)
    assert (status, body) == (403, {"error": ADMIN_ONLY_MESSAGE})
    status, html = conn.request("GET", "/", session=user)
    assert 'data-gfit-role="member"' in html

    status, body = conn.request("GET", "/api/logs", session=admin)
    assert status == 200, body
    status, html = conn.request("GET", "/", session=admin)
    assert 'data-gfit-role="admin"' in html
    assert EXTENSION_SCRIPT in html
    status, body = conn.request("GET", "/api/profiles", session=admin)
    assert {USER, OTHER_USER} <= {p["name"] for p in body["profiles"]}
    status, body = conn.request("GET", "/api/auth/status", session=admin)
    assert (body["role"], body["bound_profile"]) == ("admin", "default")


def test_a_user_request_after_an_admin_request_on_one_connection_is_the_users(srv, conn):
    user, admin = _session(srv, USER), _session(srv, ADMIN)

    assert conn.request("GET", "/api/logs", session=admin)[0] == 200

    status, body = conn.request("GET", "/api/logs", session=user)
    assert (status, body) == (403, {"error": ADMIN_ONLY_MESSAGE})
    status, body = conn.request("GET", "/api/profiles", session=user)
    assert [p["name"] for p in body["profiles"]] == [USER]
    # Refused before its body is read, so the server closes the connection.
    status, body = conn.request(
        "POST", "/api/profile/switch", session=user, body={"name": "default"}, last=True,
    )
    assert status == 403


def test_a_refused_request_and_the_next_get_no_leftover_caller(srv, conn):
    user, admin = _session(srv, USER), _session(srv, ADMIN)
    status, body = conn.request("POST", "/api/profile/disable", session=admin, body={"name": USER})
    assert status == 200, body

    # The disabled User is refused, not served as the Admin who came before.
    status, body = conn.request("GET", "/api/profiles", session=user)
    assert status == 401, body
    status, body = conn.request("GET", "/api/logs", session=user)
    assert status == 401, body
    # A request with no session after the refusal is not anyone.
    status, body = conn.request("GET", "/api/profiles")
    assert status == 401, body
    status, body = conn.request("GET", "/api/auth/status")
    assert body["logged_in"] is False
    assert "role" not in body and "bound_profile" not in body


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/profiles", None),
    ("POST", "/api/session/new", {}),
])
def test_the_requests_admission_ends_with_the_request(srv, conn, monkeypatch, method, path, body):
    """The next request on the connection starts with no caller.

    A Directory session's request clears any earlier Admission before recording
    its own, and a request with no session is refused or served by a public
    route that asks no one, so no client-visible behaviour can show a leftover.
    This spy on the route dispatch is the evidence that the end-of-request step
    clears it. A request with no session runs no Admission, so whatever it sees
    is left over.
    """
    user = _session(srv, USER)
    seen = []
    for name in ("handle_get", "handle_post"):
        route = getattr(server, name)

        def spy(handler, parsed, _route=route):
            seen.append((parsed.path, request_admission()))
            return _route(handler, parsed)

        monkeypatch.setattr(server, name, spy)

    status, _ = conn.request(method, path, session=user, body=body)
    assert status == 200
    conn.request("GET", "/api/auth/status")

    assert seen == [(path, ("member", USER)), ("/api/auth/status", None)]


# ── Login turned off ─────────────────────────────────────────────────────────

def test_with_login_off_a_request_is_not_pinned(login_off):
    client = login_off.client()
    client.cookies["hermes_profile"] = USER
    status, body, _ = client.get("/api/profiles")
    assert status == 200, body
    assert {USER, OTHER_USER} <= {p["name"] for p in body["profiles"]}
    status, body, _ = client.post("/api/profile/switch", {"name": OTHER_USER})
    assert status == 200, body


def test_with_login_off_a_request_is_not_confined(login_off, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text("outside every Profile")
    client = login_off.client()
    client.cookies["hermes_profile"] = USER

    status, body, _ = client.post("/api/workspaces/add", {"path": str(outside)})
    assert status == 200, body
    assert str(outside.resolve()) in {str(Path(w["path"]).resolve()) for w in body["workspaces"]}


def test_with_login_off_the_page_shell_has_no_role(login_off):
    status, html, _ = login_off.client().get("/")
    assert status == 200
    assert "data-gfit-role" not in html
    assert EXTENSION_SCRIPT in html


# ── A Directory session with no Admission is not admitted ────────────────────
#
# The per-request check always records the Admission it confirms, so no HTTP
# request can reach these readers without one. Called directly, they show that
# a missing Admission is "not admitted", never the session record's role.

class _Handler:
    command = "GET"

    def __init__(self, session_info):
        self._request_session = session_info
        self.status = None
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    def write(self, _):
        pass


def _admin_session_record():
    from api.auth import DIRECTORY_AUTH_TYPE

    return {
        "auth_type": DIRECTORY_AUTH_TYPE, "username": ADMIN,
        "role": "admin", "bound_profile": "default",
    }


def test_the_admin_gate_refuses_a_session_with_no_admission():
    from urllib.parse import urlparse

    from api.access import clear_request_admission
    from api.auth import _refuse_admin_only_for_user

    clear_request_admission()
    handler = _Handler(_admin_session_record())
    assert _refuse_admin_only_for_user(handler, urlparse("/api/logs"), _admin_session_record())
    assert handler.status == 403


def test_the_page_shell_gives_no_role_to_a_session_with_no_admission():
    from api.access import clear_request_admission
    from api.routes import _directory_session_role

    clear_request_admission()
    assert _directory_session_role(_Handler(_admin_session_record())) is None
