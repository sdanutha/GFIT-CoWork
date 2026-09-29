"""GFIT-CoWork -- the login decision for a Directory login, for the Admin and Users.

The order matters (spec, "Login decision"):

1. rate-limit check (per IP)
2. Directory authenticate
3. Admission (:func:`api.access.admit`): an employee ID on the Admin list logs
   in to ``default`` as an Admin; anyone else needs a Profile named after
   their employee ID, and it must not be disabled in the Profile roster
4. issue a session for that Profile, with the role

A wrong password and a missing Profile get different messages, but neither the
password nor anything derived from it is logged or stored.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import NamedTuple

from api import auth
from api.access import REFUSED_NO_PROFILE, REFUSED_PROFILE_NOT_ACTIVE
from api.directory import DIRECTORY_ENV, DirectoryUnavailable, get_directory, is_directory_enabled

logger = logging.getLogger(__name__)

INCORRECT_MESSAGE = "incorrect username or password"
NO_PROFILE_MESSAGE = (
    "You don't have access to this system yet — contact your team's Admin."
)
SUSPENDED_MESSAGE = (
    "Your access to this system is suspended — contact your team's Admin."
)
RATE_LIMITED_MESSAGE = "Too many attempts. Try again in a minute."
UNAVAILABLE_MESSAGE = (
    "The company directory is unavailable right now. Try again in a few minutes."
)

# What login logs and shows for each reason Admission refuses someone.
_REFUSALS = {
    REFUSED_NO_PROFILE: ("no Profile", NO_PROFILE_MESSAGE),
    REFUSED_PROFILE_NOT_ACTIVE: ("Profile disabled", SUSPENDED_MESSAGE),
}


class LoginOutcome(NamedTuple):
    status: int
    error: str | None = None
    session_cookie: str | None = None
    bound_profile: str | None = None


def attempt_login(username, password, client_ip: str) -> LoginOutcome:
    if not auth._check_login_rate(client_ip):
        return LoginOutcome(429, RATE_LIMITED_MESSAGE)

    directory = get_directory()
    try:
        identity = directory.authenticate(username, password) if directory else None
    except DirectoryUnavailable as exc:
        logger.warning("Directory login unavailable: %s", exc)
        return LoginOutcome(503, UNAVAILABLE_MESSAGE)
    if identity is None:
        auth._record_login_attempt(client_ip)
        return LoginOutcome(401, INCORRECT_MESSAGE)

    from api.access import ROLE_ADMIN, ROLE_MEMBER, Refused, admit

    admission = admit(identity.employee_id)
    if isinstance(admission, Refused):
        why, message = _REFUSALS[admission.reason]
        logger.info("Directory login for %s refused: %s", identity.employee_id, why)
        return LoginOutcome(403, message)
    role, bound_profile = admission

    auth._clear_login_attempts(client_ip)
    if role == ROLE_MEMBER:
        from api import roster
        from api.workspace import ensure_user_workspace

        ensure_user_workspace(bound_profile)
        roster.record_login(bound_profile, identity.display_name)
    cookie = auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE,
        username=identity.employee_id,
        bound_profile=bound_profile,
        role=role,
        # An Admin has no Profile, so no roster record: the name rides on the session.
        display_name=identity.display_name if role == ROLE_ADMIN else None,
    )
    return LoginOutcome(200, session_cookie=cookie, bound_profile=bound_profile)


def session_identity(session_info: dict) -> dict:
    """The name GFIT-CoWork shows for this request's Directory session: display name and "name (ID)" label.

    A User's name comes from the Profile roster (updated from the Directory
    on every login, else the name the Admin typed); an Admin's from the session.
    Whether the caller is a User is the request's Admission.
    """
    from api import roster
    from api.access import caller_is_user

    employee_id = str(session_info.get("username") or "")
    if caller_is_user():
        display_name = roster.view(employee_id)["display_name"]
    else:
        display_name = roster.directory_name(session_info.get("display_name"), employee_id)
    return {
        "display_name": display_name,
        "label": f"{display_name} ({employee_id})" if display_name else employee_id,
    }


# ── Startup: what is true about login ──────────────────────────────────────

_DIRECTORY_HINT = (
    f"set {DIRECTORY_ENV}=ldap and the HERMES_WEBUI_LDAP_* settings (see deploy/README.md)"
)

# Upstream login settings the Directory replaced (ADR 0004). None of them lets
# anyone log in; startup names each one it finds so an old configuration is
# not mistaken for a way in.
_LEFTOVER_ENV = (
    "HERMES_WEBUI_PASSWORD",
    "HERMES_WEBUI_PASSKEY",
    "HERMES_WEBUI_OIDC_ISSUER",
    "HERMES_WEBUI_OIDC_CLIENT_ID",
    "HERMES_WEBUI_OIDC_CLIENT_SECRET",
    "HERMES_WEBUI_OIDC_REDIRECT_URI",
    "HERMES_WEBUI_OIDC_SCOPES",
    "HERMES_WEBUI_OIDC_ALLOW_CLAIM",
    "HERMES_WEBUI_OIDC_ALLOW_VALUES",
    "HERMES_WEBUI_TRUSTED_AUTH_HEADER",
    "HERMES_WEBUI_TRUSTED_GROUPS_HEADER",
    "HERMES_WEBUI_GROUP_PROFILE_MAP",
    "HERMES_WEBUI_TRUSTED_AUTH_LOGOUT_URL",
)
_LEFTOVER_CONFIG_KEYS = ("webui_passkey_enabled", "webui_oidc")


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        # Any other hostname may resolve to a network address: treat it as one.
        return False


def _leftover_login_settings() -> list[str]:
    found = [f"{name} (environment)" for name in _LEFTOVER_ENV if os.getenv(name, "").strip()]
    from api.config import get_config, load_settings

    try:
        if load_settings().get("password_hash"):
            found.append("the password stored in Settings")
    except Exception:
        logger.debug("Could not read Settings for leftover login settings", exc_info=True)
    try:
        cfg = get_config()
        if isinstance(cfg, dict):
            found.extend(f"{key} (config.yaml)" for key in _LEFTOVER_CONFIG_KEYS if key in cfg)
    except Exception:
        logger.debug("Could not read config.yaml for leftover login settings", exc_info=True)
    return found


def startup_check(host: str) -> tuple[bool, list[str]]:
    """Decide from the bind address and the Directory whether the server may serve.

    Returns ``(serve, lines)``: the lines are what startup prints. A network
    address with no Directory would serve with login off, so the server must
    not start. The loopback address with no Directory serves with login off,
    for local development and the test suite.
    """
    lines = [
        f"[!!] Ignoring {setting}: Upstream login is gone; the Directory replaces it."
        for setting in _leftover_login_settings()
    ]
    if is_directory_enabled():
        return True, lines
    if not _is_loopback_host(host):
        lines += [
            f"[!!] Refusing to start: no Directory is configured, so binding to {host} would serve with login off.",
            "     Anyone who reaches the port could use every Profile, the terminal and the agent.",
            f"     To serve on a network address, {_DIRECTORY_HINT}.",
            "     For local development without login, bind to 127.0.0.1.",
        ]
        return False, lines
    if auth.is_auth_enabled():
        lines.append(f"[!!] No Directory is configured, so nobody can log in. To turn login on, {_DIRECTORY_HINT}.")
    else:
        lines += [
            "  [tip] Login is off: no Directory is configured. Any process on this machine",
            "        can use every Profile through the local API.",
            f"        To turn login on, {_DIRECTORY_HINT}.",
        ]
    return True, lines
