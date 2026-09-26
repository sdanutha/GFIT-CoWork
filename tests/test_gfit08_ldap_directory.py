"""GFIT-CoWork ticket 08: the LDAP Directory (company AD over LDAPS or StartTLS).

Tests that need a real LDAP server run against the mock LDAP in
``dev/mock-ldap`` (see its README) and are skipped unless
``GFIT_MOCK_LDAP_URL`` is set. The rest need no server.
"""
from __future__ import annotations

import os
import socket
import threading

import pytest

from api.directory import DirectoryUnavailable, Identity, get_directory
from tests._gfit_server import gfit_server as _gfit_server

MEMBER = "521740"

LDAP_KEYS = (
    "HERMES_WEBUI_LDAP_URL", "HERMES_WEBUI_LDAP_STARTTLS", "HERMES_WEBUI_LDAP_BIND_FORMAT",
    "HERMES_WEBUI_LDAP_DOMAIN", "HERMES_WEBUI_LDAP_BASE_DN", "HERMES_WEBUI_LDAP_USER_FILTER",
    "HERMES_WEBUI_LDAP_CA_CERT",
)


@pytest.fixture
def ldap_env(monkeypatch):
    for key in LDAP_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HERMES_WEBUI_DIRECTORY", "ldap")
    monkeypatch.setenv("HERMES_WEBUI_LDAP_BIND_FORMAT", "upn")
    monkeypatch.setenv("HERMES_WEBUI_LDAP_DOMAIN", "gfit.co.th")
    monkeypatch.setenv("HERMES_WEBUI_LDAP_BASE_DN", "dc=gfit,dc=co,dc=th")

    def configure(**values):
        for key, value in values.items():
            monkeypatch.setenv(f"HERMES_WEBUI_LDAP_{key.upper()}", value)

    return configure


class _Listener:
    """A TCP port that records whether anything connected to it."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.connected = False
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        try:
            conn, _ = self.sock.accept()
            self.connected = True
            conn.close()
        except OSError:
            pass

    def close(self):
        self._thread.join(timeout=2)
        self.sock.close()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_ldap_is_chosen_by_config(ldap_env):
    ldap_env(url="ldaps://ad.example.invalid")
    from api.ldap_directory import LdapDirectory

    assert isinstance(get_directory(), LdapDirectory)


def test_plain_ldap_without_starttls_is_refused_before_connecting(ldap_env):
    listener = _Listener()
    try:
        ldap_env(url=f"ldap://127.0.0.1:{listener.port}")
        with pytest.raises(DirectoryUnavailable):
            get_directory().authenticate(MEMBER, "a-password")
    finally:
        listener.close()
    assert listener.connected is False


@pytest.mark.parametrize("url", ["", "http://ad.example.invalid", "ldaps://"])
def test_a_broken_url_makes_the_directory_unavailable(ldap_env, url):
    ldap_env(url=url)
    with pytest.raises(DirectoryUnavailable):
        get_directory().authenticate(MEMBER, "a-password")


def test_an_unreachable_directory_is_unavailable_not_a_wrong_password(ldap_env):
    ldap_env(url=f"ldaps://127.0.0.1:{_closed_port()}")
    with pytest.raises(DirectoryUnavailable):
        get_directory().authenticate(MEMBER, "a-password")


def test_an_empty_password_is_refused_without_asking_ad(ldap_env):
    """AD treats an empty password as an anonymous bind that succeeds."""
    ldap_env(url=f"ldaps://127.0.0.1:{_closed_port()}")
    assert get_directory().authenticate(MEMBER, "") is None


@pytest.fixture
def unreachable_srv(monkeypatch, tmp_path, ldap_env):
    with _gfit_server(monkeypatch, tmp_path, users={}, profile_names=[MEMBER], directory="ldap") as s:
        ldap_env(url=f"ldaps://127.0.0.1:{_closed_port()}")
        yield s


def test_login_while_ad_is_unreachable_says_so(unreachable_srv):
    import api.auth as auth

    client = unreachable_srv.client()
    for _ in range(auth._LOGIN_MAX_ATTEMPTS + 1):
        status, body, _ = client.login(MEMBER, "a-password")
        assert status == 503, body
        assert "incorrect" not in body["error"].lower()
        assert "unavailable" in body["error"].lower()


# ── Against the mock LDAP (dev/mock-ldap) ────────────────────────────────────

MOCK_URL = os.getenv("GFIT_MOCK_LDAP_URL", "")
mock_ldap = pytest.mark.skipif(not MOCK_URL, reason="mock LDAP not running (set GFIT_MOCK_LDAP_URL)")
MOCK_PASSWORD = "Somchai-Pass-1"


@pytest.fixture
def mock_env(ldap_env):
    ca = os.getenv("GFIT_MOCK_LDAP_CA_CERT", "dev/mock-ldap/certs/ca.crt")
    ldap_env(
        url=MOCK_URL,
        bind_format="uid={username},ou=people,dc=gfit,dc=local",
        base_dn="ou=people,dc=gfit,dc=local",
        user_filter="(uid={username})",
        ca_cert=ca,
    )
    return ldap_env


@mock_ldap
def test_mock_correct_password_returns_display_name(mock_env):
    assert get_directory().authenticate(f"GFIT\\{MEMBER}", MOCK_PASSWORD) == Identity(MEMBER, "สมชาย ใจดี")


@mock_ldap
def test_mock_wrong_password_is_refused(mock_env):
    assert get_directory().authenticate(MEMBER, "wrong") is None


@mock_ldap
def test_mock_unknown_user_is_refused(mock_env):
    assert get_directory().authenticate("999999", MOCK_PASSWORD) is None


@mock_ldap
def test_mock_plaintext_port_is_refused(mock_env):
    mock_env(url="ldap://localhost:1389")
    with pytest.raises(DirectoryUnavailable):
        get_directory().authenticate(MEMBER, MOCK_PASSWORD)


@mock_ldap
def test_mock_starttls_on_the_plain_port_works(mock_env):
    mock_env(url="ldap://localhost:1389", starttls="1")
    assert get_directory().authenticate(MEMBER, MOCK_PASSWORD) == Identity(MEMBER, "สมชาย ใจดี")
