"""GFIT-CoWork test support: a real ``server.Handler`` in-process, plus a tiny browser.

Every GFIT-CoWork HTTP test starts its own server on a free port, with auth
state, Hermes home and the in-memory Directory isolated to ``tmp_path``. The
shared live test server is left alone: turning Directory login on there would
put every other test behind a login.
"""
from __future__ import annotations

import contextlib
import http.client
import http.cookies
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import api.auth as auth
import api.login as login
import api.profiles as profiles
import api.roster as roster


PASSWORD = "Tr0ub4dor&3-correct-horse"
WRONG_PASSWORD = "not-the-password"


class Client:
    """A tiny browser: keeps cookies across requests to one server."""

    def __init__(self, port: int):
        self.port = port
        self.cookies: dict[str, str] = {}

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        sent = dict(headers or {})
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        assert body is None or raw is None, "pass a JSON body or raw bytes, not both"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=sent)
        resp = conn.getresponse()
        raw = resp.read()
        set_cookies = resp.headers.get_all("Set-Cookie") or []
        for header in set_cookies:
            jar = http.cookies.SimpleCookie()
            jar.load(header)
            for name, morsel in jar.items():
                if morsel.value and morsel["max-age"] != "0":
                    self.cookies[name] = morsel.value
                else:
                    self.cookies.pop(name, None)
        conn.close()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return resp.status, payload, set_cookies

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, {} if body is None else body, **kw)

    def post_file(self, path, fields: dict, filename: str, content: bytes):
        """POST a multipart form with *fields* and one ``file`` upload."""
        boundary = "gfit-test-boundary"
        parts = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        return self.request(
            "POST", path, raw=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def login(self, username, password=PASSWORD):
        return self.request("POST", "/api/auth/login", {"username": username, "password": password})


@dataclass
class GfitServer:
    port: int
    state: Path
    hermes_home: Path
    users: Path
    directory: str

    def client(self) -> Client:
        return Client(self.port)

    def logged_in(self, username, password=PASSWORD) -> Client:
        c = self.client()
        status, body, _ = c.login(username, password)
        assert status == 200, (username, status, body)
        return c

    def profile_home(self, name) -> Path:
        return self.hermes_home / "profiles" / name


@contextlib.contextmanager
def gfit_server(monkeypatch, tmp_path, *, users: dict, profile_names=(), admins="", directory="memory", legacy_env=None):
    """Start an in-process GFIT-CoWork server with Directory login on.

    *users* maps employee ID -> display name; every one of them has the
    password :data:`PASSWORD`. A Profile is created for each of *profile_names*.
    *admins* is the ``HERMES_WEBUI_ADMIN_USERS`` value. *directory* is the
    ``HERMES_WEBUI_DIRECTORY`` kind (empty turns Directory login off).
    *legacy_env* sets environment variables configuring Upstream login methods.
    """
    import server

    state = tmp_path / "state"
    state.mkdir()
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    for name in profile_names:
        (hermes_home / "profiles" / name).mkdir()

    users_file = tmp_path / "directory-users.json"
    users_file.write_text(json.dumps({
        uid: {"password": PASSWORD, "display_name": name} for uid, name in users.items()
    }))
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", directory)
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY_USERS", str(users_file))
    monkeypatch.setenv("HERMES_WEBUI_ADMIN_USERS", admins)

    # Isolate auth state. The Upstream login methods are off in GFIT-CoWork
    # (ticket 09), so nothing else needs switching off.
    monkeypatch.setattr("api.config.STATE_DIR", state)
    monkeypatch.setattr(auth, "_SESSIONS_FILE", state / ".sessions.json")
    monkeypatch.setattr(login, "_LOGIN_ATTEMPTS_FILE", state / ".login_attempts.json")
    monkeypatch.setattr("api.config.STATE_DIR", state)
    for name, value in (legacy_env or {}).items():
        monkeypatch.setenv(name, value)
    auth._sessions.clear()
    login._login_attempts.clear()

    # Profiles live under an isolated Hermes home.
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    profiles._invalidate_root_profile_cache()
    profiles._invalidate_list_profiles_cache()

    httpd = server.QuietHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield GfitServer(httpd.server_address[1], state, hermes_home, users_file, directory)
    finally:
        httpd.shutdown()
        httpd.server_close()
        auth._sessions.clear()
        login._login_attempts.clear()
        profiles._invalidate_root_profile_cache()
        profiles._invalidate_list_profiles_cache()
