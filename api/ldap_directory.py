"""GFIT-CoWork -- the LDAP Directory: check a password against the company AD.

Chosen with ``HERMES_WEBUI_DIRECTORY=ldap``. It binds to AD *as the user* (so AD
decides whether the password is right and the account is enabled), then reads
the user's ``displayName``. The password only ever travels over TLS: an
``ldaps://`` URL, or an ``ldap://`` URL with StartTLS. Plain LDAP is refused
before a connection is opened.

Configuration (nothing is hard-coded):

``HERMES_WEBUI_LDAP_URL``
    ``ldaps://ad.example.com`` (port 636 by default) or ``ldap://ad.example.com``
    (port 389; needs StartTLS).
``HERMES_WEBUI_LDAP_STARTTLS``
    ``1`` to upgrade an ``ldap://`` connection with StartTLS.
``HERMES_WEBUI_LDAP_BIND_FORMAT``
    ``upn`` (``521740@<domain>``), ``domain`` (``<DOMAIN>\\521740``), or a
    template containing ``{username}`` such as
    ``uid={username},ou=people,dc=example,dc=org``.
``HERMES_WEBUI_LDAP_DOMAIN``
    The UPN suffix or the NetBIOS domain, for ``upn`` and ``domain``.
``HERMES_WEBUI_LDAP_BASE_DN``
    Where to look the user up to read ``displayName``.
``HERMES_WEBUI_LDAP_USER_FILTER``
    The lookup filter; default ``(sAMAccountName={username})``.
``HERMES_WEBUI_LDAP_CA_CERT``
    A CA certificate file for the AD server's certificate; the system CAs are
    used when unset. The certificate is always verified.
"""
from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from api.directory import DirectoryUnavailable, Identity, normalize_username

logger = logging.getLogger(__name__)

_ENV = "HERMES_WEBUI_LDAP_"
_INVALID_CREDENTIALS = 49
_CONNECT_TIMEOUT = 5
_RECEIVE_TIMEOUT = 10


@dataclass(frozen=True)
class LdapDirectory:
    host: str
    port: int
    use_ssl: bool
    bind_format: str
    base_dn: str
    user_filter: str
    ca_cert: str | None

    @classmethod
    def from_env(cls) -> "LdapDirectory":
        """Read the configuration; ValueError when it is unusable (including plain LDAP)."""

        def env(name, default=""):
            return os.getenv(_ENV + name, default).strip()

        url = urlsplit(env("URL"))
        if url.scheme not in ("ldaps", "ldap") or not url.hostname:
            raise ValueError("HERMES_WEBUI_LDAP_URL must be an ldaps:// or ldap:// URL")
        use_ssl = url.scheme == "ldaps"
        starttls = env("STARTTLS").lower() in {"1", "true", "yes", "on"}
        if not use_ssl and not starttls:
            raise ValueError(
                "refusing plain LDAP: use an ldaps:// URL or set HERMES_WEBUI_LDAP_STARTTLS=1"
            )
        return cls(
            host=url.hostname,
            port=url.port or (636 if use_ssl else 389),
            use_ssl=use_ssl,
            bind_format=_bind_template(env("BIND_FORMAT", "upn"), env("DOMAIN")),
            base_dn=env("BASE_DN"),
            user_filter=env("USER_FILTER") or "(sAMAccountName={username})",
            ca_cert=env("CA_CERT") or None,
        )

    def authenticate(self, username, password) -> Identity | None:
        name = normalize_username(username)
        # AD treats a bind with an empty password as anonymous, and succeeds.
        if name is None or not isinstance(password, str) or not password:
            return None
        import ldap3
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=self.ca_cert)
        server = ldap3.Server(
            self.host, port=self.port, use_ssl=self.use_ssl, tls=tls,
            connect_timeout=_CONNECT_TIMEOUT, get_info=ldap3.NONE,
        )
        conn = ldap3.Connection(
            server, user=self.bind_format.format(username=name), password=password,
            authentication=ldap3.SIMPLE, receive_timeout=_RECEIVE_TIMEOUT,
            raise_exceptions=False, read_only=True,
        )
        try:
            conn.open()
            if not self.use_ssl and not conn.start_tls():
                raise DirectoryUnavailable("StartTLS failed")
            if not conn.bind():
                if conn.result.get("result") == _INVALID_CREDENTIALS:
                    return None
                raise DirectoryUnavailable(f"bind failed: {conn.result.get('description')}")
            display_name = self._display_name(conn, escape_filter_chars(name))
        except LDAPException as exc:
            raise DirectoryUnavailable(f"{type(exc).__name__}: {exc}") from None
        finally:
            try:
                conn.unbind()
            except Exception:
                pass
            # ldap3 keeps the socket of a failed open; close it so an AD outage
            # does not leak one file descriptor per login attempt.
            if conn.socket is not None:
                conn.socket.close()
        return Identity(name, display_name or name)

    def _display_name(self, conn, name: str) -> str:
        if not self.base_dn:
            return ""
        found = conn.search(
            self.base_dn, self.user_filter.format(username=name),
            attributes=["displayName"], size_limit=1,
        )
        if not found or not conn.entries:
            return ""
        value = conn.entries[0].entry_attributes_as_dict.get("displayName") or [""]
        return str(value[0]).strip()


def _bind_template(bind_format: str, domain: str) -> str:
    if bind_format == "upn":
        if not domain:
            raise ValueError("HERMES_WEBUI_LDAP_DOMAIN is required for the upn bind format")
        return "{username}@" + domain
    if bind_format == "domain":
        if not domain:
            raise ValueError("HERMES_WEBUI_LDAP_DOMAIN is required for the domain bind format")
        return domain + "\\{username}"
    if "{username}" in bind_format:
        return bind_format
    raise ValueError("HERMES_WEBUI_LDAP_BIND_FORMAT must be upn, domain, or contain {username}")
