"""GFIT-CoWork: startup says what is true about login (login-is-the-directory ticket 01).

The Directory is the only login. Startup decides from the bind address and the
Directory configuration:

* a network address with no Directory: the server refuses to start and names
  the Directory settings, so a Deployment is never exposed with login off;
* the loopback address with no Directory: the server starts with login off, as
  Upstream does, and says so without advising ``HERMES_WEBUI_PASSWORD``;
* a Directory configured: the server starts and asks for login.

A leftover Upstream login setting is reported as ignored.

These tests start the server as a real process, as the Deployment tests do.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DIRECTORY_SETTING = "HERMES_WEBUI_DIRECTORY"

LEFTOVER_ENV = {
    "HERMES_WEBUI_PASSWORD": "the-old-shared-password",
    "HERMES_WEBUI_PASSKEY": "1",
    "HERMES_WEBUI_OIDC_ISSUER": "https://idp.example.com",
    "HERMES_WEBUI_TRUSTED_AUTH_HEADER": "X-Remote-User",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, proc: subprocess.Popen, port: int, log: Path):
        self.proc = proc
        self.port = port
        self.log = log

    @property
    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def status(self, path: str) -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def serving(self, timeout: float = 60) -> bool:
        """Wait until the server answers /health (True) or its process exits (False)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.loads(r.read()).get("status") == "ok":
                        return True
            except Exception:
                time.sleep(0.3)
        pytest.fail(f"server neither served nor exited within {timeout}s:\n{self.output}")


@pytest.fixture
def start_server(tmp_path):
    from tests.conftest import HERMES_AGENT, SERVER_SCRIPT, VENV_PYTHON, _kill_process_tree

    started: list[subprocess.Popen] = []

    def start(host: str, extra_env: dict[str, str] | None = None, settings: dict | None = None) -> _Server:
        root = tmp_path / f"run{len(started)}"
        home = root / "hermes"
        state = root / "state"
        home.mkdir(parents=True)
        state.mkdir()
        (root / "home").mkdir()
        if settings is not None:
            (state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        port = _free_port()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root / "home"),
            "LANG": "C.UTF-8",
            "HERMES_HOME": str(home),
            "HERMES_BASE_HOME": str(home),
            "HERMES_WEBUI_STATE_DIR": str(state),
            "HERMES_WEBUI_DEFAULT_WORKSPACE": str(root / "workspace"),
            "HERMES_WEBUI_HOST": host,
            "HERMES_WEBUI_PORT": str(port),
            "HERMES_WEBUI_TEST_NETWORK_BLOCK": "1",
            "AWS_EC2_METADATA_DISABLED": "true",
            # Keeps the agent's import-time launch preparation from rewriting the
            # real agent launchers (see tests/conftest.py).
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            **(extra_env or {}),
        }
        if HERMES_AGENT:
            env["HERMES_WEBUI_AGENT_DIR"] = str(HERMES_AGENT)
        log = root / "server.log"
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                [VENV_PYTHON or sys.executable, str(SERVER_SCRIPT)],
                cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT,
            )
        started.append(proc)
        return _Server(proc, port, log)

    yield start
    for proc in started:
        if proc.poll() is None:
            _kill_process_tree(proc.pid)
            proc.wait(timeout=10)


@pytest.fixture
def directory_env(tmp_path):
    users = tmp_path / "directory-users.json"
    users.write_text(json.dumps({"600001": {"password": "pw-600001", "display_name": "Member"}}))
    return {DIRECTORY_SETTING: "memory", "HERMES_WEBUI_DIRECTORY_USERS": str(users)}


def test_a_network_address_with_no_directory_does_not_start(start_server):
    server = start_server("0.0.0.0")
    assert not server.serving(), f"served with login off on a network address:\n{server.output}"
    assert server.proc.wait(timeout=10) != 0
    assert DIRECTORY_SETTING in server.output


def test_the_loopback_address_with_no_directory_starts_with_login_off(start_server):
    server = start_server("127.0.0.1")
    assert server.serving(), server.output
    assert server.status("/api/sessions") == 200
    output = server.output
    assert "login is off" in output.lower(), output
    assert DIRECTORY_SETTING in output
    assert "HERMES_WEBUI_PASSWORD" not in output


def test_with_a_directory_the_server_starts_and_asks_for_login(start_server, directory_env):
    server = start_server("0.0.0.0", directory_env)
    assert server.serving(), server.output
    assert server.status("/api/sessions") == 401
    assert "login is off" not in server.output.lower()


def test_leftover_upstream_login_settings_are_reported_as_ignored(start_server, directory_env):
    server = start_server(
        "127.0.0.1", {**directory_env, **LEFTOVER_ENV},
        settings={"password_hash": "left-over-hash"},
    )
    assert server.serving(), server.output
    ignored = [line for line in server.output.splitlines() if "ignor" in line.lower()]
    for name in LEFTOVER_ENV:
        assert any(name in line for line in ignored), (name, server.output)
    assert any("password" in line.lower() and "settings" in line.lower() for line in ignored), server.output


def test_a_leftover_password_on_the_loopback_address_leaves_login_off(start_server):
    server = start_server(
        "127.0.0.1", {"HERMES_WEBUI_PASSWORD": "the-old-shared-password"},
        settings={"password_hash": "left-over-hash"},
    )
    assert server.serving(), server.output
    assert server.status("/api/sessions") == 200
    output = server.output
    assert "login is off" in output.lower(), output
    assert any("HERMES_WEBUI_PASSWORD" in line and "ignor" in line.lower() for line in output.splitlines()), output
    assert "nobody can log in" not in output.lower()
