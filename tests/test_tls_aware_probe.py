"""Tests for the TLS-aware /health probe of the image's HEALTHCHECK.

Covers the shell helper (``scripts/lib/health_probe.sh``). The probe must fall
back to plain HTTP when TLS cert/key are configured but the server fell back to
serving HTTP (server.py's tested contract), instead of polling HTTPS forever.
"""
from __future__ import annotations

import http.server
import os
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

# Talks to the shared test server (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("test_server")


REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTH_PROBE = REPO_ROOT / "scripts" / "lib" / "health_probe.sh"


# ── helpers ─────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, *args):  # suppress server log noise during tests
        pass


class _Server:
    """Minimal /health server, optionally TLS-wrapped, on a background thread."""

    def __init__(self, cert: str | None = None, key: str | None = None):
        self.port = _free_port()
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), _HealthHandler)
        if cert and key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def self_signed_cert(tmp_path: Path) -> tuple[str, str]:
    cert = str(tmp_path / "cert.pem")
    key = str(tmp_path / "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key,
         "-out", cert, "-days", "1", "-nodes", "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    return cert, key


def _run_helper(host: str, port: int, env: dict[str, str]) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        ["bash", str(HEALTH_PROBE), host, str(port), "/health", "3"],
        capture_output=True, text=True, env=merged, timeout=30,
    )


# ── shared shell helper ─────────────────────────────────────────────────────

def test_helper_scheme_http_without_tls():
    out = subprocess.run(
        ["bash", "-c", f". {HEALTH_PROBE}; hermes_webui_probe_scheme"],
        capture_output=True, text=True, env={**os.environ,
                                             "HERMES_WEBUI_TLS_CERT": "",
                                             "HERMES_WEBUI_TLS_KEY": ""},
    )
    assert out.stdout == "http"


def test_helper_scheme_https_with_tls():
    out = subprocess.run(
        ["bash", "-c", f". {HEALTH_PROBE}; hermes_webui_probe_scheme"],
        capture_output=True, text=True,
        env={**os.environ, "HERMES_WEBUI_TLS_CERT": "/c", "HERMES_WEBUI_TLS_KEY": "/k"},
    )
    assert out.stdout == "https"


def test_helper_plain_http_probe():
    with _Server() as srv:
        res = _run_helper("127.0.0.1", srv.port,
                          {"HERMES_WEBUI_TLS_CERT": "", "HERMES_WEBUI_TLS_KEY": ""})
    assert res.returncode == 0
    assert '"status": "ok"' in res.stdout


def test_helper_self_signed_warns_and_succeeds(self_signed_cert):
    cert, key = self_signed_cert
    with _Server(cert, key) as srv:
        res = _run_helper("127.0.0.1", srv.port,
                          {"HERMES_WEBUI_TLS_CERT": cert, "HERMES_WEBUI_TLS_KEY": key})
    assert res.returncode == 0, res.stderr
    assert '"status": "ok"' in res.stdout
    assert "self-signed" in res.stderr.lower()


def test_helper_insecure_optin_is_silent(self_signed_cert):
    cert, key = self_signed_cert
    with _Server(cert, key) as srv:
        res = _run_helper("127.0.0.1", srv.port, {
            "HERMES_WEBUI_TLS_CERT": cert,
            "HERMES_WEBUI_TLS_KEY": key,
            "HERMES_WEBUI_TLS_INSECURE_PROBE": "1",
        })
    assert res.returncode == 0, res.stderr
    assert '"status": "ok"' in res.stdout
    # Explicit opt-in is silent by contract.
    assert res.stderr.strip() == ""


def test_helper_https_falls_back_to_http_when_server_is_plain_http(self_signed_cert):
    """TLS configured in env, but the server serves plain HTTP (server.py's
    fallback-on-bad-cert contract). The probe must still succeed via HTTP."""
    cert, key = self_signed_cert
    with _Server() as srv:  # plain HTTP server
        res = _run_helper("127.0.0.1", srv.port,
                          {"HERMES_WEBUI_TLS_CERT": cert, "HERMES_WEBUI_TLS_KEY": key})
    assert res.returncode == 0, res.stderr
    assert '"status": "ok"' in res.stdout


def test_helper_insecure_optin_falls_back_to_http(self_signed_cert):
    """Shell helper: insecure opt-in + server serving plain HTTP must still
    succeed via the HTTP fallback (and stay silent per the opt-in contract)."""
    cert, key = self_signed_cert
    with _Server() as srv:  # plain HTTP
        res = _run_helper("127.0.0.1", srv.port, {
            "HERMES_WEBUI_TLS_CERT": cert,
            "HERMES_WEBUI_TLS_KEY": key,
            "HERMES_WEBUI_TLS_INSECURE_PROBE": "1",
        })
    assert res.returncode == 0, res.stderr
    assert '"status": "ok"' in res.stdout
    assert res.stderr.strip() == ""
