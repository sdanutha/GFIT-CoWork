"""GFIT-CoWork -- the login decision for a Directory login.

The order matters (spec, "Login decision"):

1. rate-limit check (per person: :func:`rate_limit_key`)
2. Directory authenticate
3. Admission (:func:`api.access.admit`): the User needs a Profile named after
   their employee ID, and it must not be disabled in the Profile roster
4. issue a session for that Profile

A wrong password and a missing Profile get different messages, but neither the
password nor anything derived from it is logged or stored.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import NamedTuple

from api import config as _config
from api import auth, trusted_proxy
from api.access import REFUSED_NO_PROFILE, REFUSED_PROFILE_NOT_ACTIVE
from api.directory import DIRECTORY_ENV, DirectoryUnavailable, get_directory, is_directory_enabled

logger = logging.getLogger(__name__)

INCORRECT_MESSAGE = "incorrect username or password"
NO_PROFILE_MESSAGE = (
    "You don't have access to this system yet — contact your team's Operator."
)
SUSPENDED_MESSAGE = (
    "Your access to this system is suspended — contact your team's Operator."
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


# ── Rate limit: wrong passwords per person, kept across restarts ───────────
_LOGIN_ATTEMPTS_FILE = _config.STATE_DIR / '.login_attempts.json'
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 60  # seconds


def _load_login_attempts() -> dict[str, list[float]]:
    """Load persisted login attempts from STATE_DIR, pruning expired entries."""
    try:
        if _LOGIN_ATTEMPTS_FILE.exists():
            data = json.loads(_LOGIN_ATTEMPTS_FILE.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('malformed login-attempts file — expected dict')
            now = time.time()
            attempts: dict[str, list[float]] = {}
            for key, raw_times in data.items():
                if not isinstance(key, str) or not isinstance(raw_times, list):
                    continue
                fresh = [
                    float(t)
                    for t in raw_times
                    if isinstance(t, (int, float)) and now - float(t) < _LOGIN_WINDOW
                ]
                if fresh:
                    attempts[key] = fresh
            return attempts
    except Exception as e:
        logger.debug("Failed to load login attempts file, starting fresh: %s", e)
    return {}


def _save_login_attempts(attempts: dict[str, list[float]]) -> None:
    """Atomically persist login attempts to STATE_DIR/.login_attempts.json (0600)."""
    try:
        _LOGIN_ATTEMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_LOGIN_ATTEMPTS_FILE.parent, suffix='.login_attempts.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(attempts, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _LOGIN_ATTEMPTS_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to persist login attempts: %s", e)


_login_attempts = _load_login_attempts()  # rate_limit_key -> [timestamp, ...]
_LOGIN_ATTEMPTS_LOCK = threading.Lock()


def _check_login_rate(key: str) -> bool:
    """Return True if this key may attempt login (thread-safe)."""
    with _LOGIN_ATTEMPTS_LOCK:
        now = time.time()
        attempts = _login_attempts.get(key, [])
        # Prune old attempts
        attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
        if attempts:
            _login_attempts[key] = attempts
        else:
            _login_attempts.pop(key, None)
        _save_login_attempts(_login_attempts)
        return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(key: str) -> None:
    """Record a login attempt for rate limiting (thread-safe)."""
    with _LOGIN_ATTEMPTS_LOCK:
        now = time.time()
        attempts = _login_attempts.get(key, [])
        attempts.append(now)
        _login_attempts[key] = attempts
        _save_login_attempts(_login_attempts)


def _clear_login_attempts(key: str) -> None:
    """Clear failed login attempts after a successful login (thread-safe)."""
    with _LOGIN_ATTEMPTS_LOCK:
        if key in _login_attempts:
            _login_attempts.pop(key, None)
            _save_login_attempts(_login_attempts)


def rate_limit_key(handler) -> str:
    """The address the login rate limit counts attempts against.

    Behind a reverse proxy every request arrives from the proxy, so one person's
    wrong passwords would lock out the whole Team. With
    ``HERMES_WEBUI_TRUST_FORWARDED_FOR=1`` and a trusted proxy as the socket
    peer, the forwarded client address is used instead; otherwise (or on a
    malformed chain) the socket peer.
    """
    raw = trusted_proxy.peer_address(handler)
    trust_forwarded = os.getenv("HERMES_WEBUI_TRUST_FORWARDED_FOR", "").strip().lower()
    if trust_forwarded in {"1", "true", "yes", "on"} and trusted_proxy.peer_is_trusted_proxy(handler):
        return trusted_proxy.forwarded_client_address(handler) or raw
    return raw


class LoginOutcome(NamedTuple):
    status: int
    error: str | None = None
    session_cookie: str | None = None
    bound_profile: str | None = None


def attempt_login(username, password, rate_key: str) -> LoginOutcome:
    if not _check_login_rate(rate_key):
        return LoginOutcome(429, RATE_LIMITED_MESSAGE)

    directory = get_directory()
    try:
        identity = directory.authenticate(username, password) if directory else None
    except DirectoryUnavailable as exc:
        logger.warning("Directory login unavailable: %s", exc)
        return LoginOutcome(503, UNAVAILABLE_MESSAGE)
    if identity is None:
        _record_login_attempt(rate_key)
        return LoginOutcome(401, INCORRECT_MESSAGE)

    from api import roster
    from api.access import Refused, admit
    from api.workspace import ensure_user_wiki, ensure_user_workspace

    admission = admit(identity.employee_id)
    if isinstance(admission, Refused):
        why, message = _REFUSALS[admission.reason]
        logger.info("Directory login for %s refused: %s", identity.employee_id, why)
        return LoginOutcome(403, message)
    role, bound_profile = admission

    _clear_login_attempts(rate_key)
    ensure_user_workspace(bound_profile)
    ensure_user_wiki(bound_profile)
    roster.record_login(bound_profile, identity.display_name)
    cookie = auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE,
        username=identity.employee_id,
        bound_profile=bound_profile,
        role=role,
    )
    return LoginOutcome(200, session_cookie=cookie, bound_profile=bound_profile)


def session_identity(session_info: dict) -> dict:
    """The name GFIT-CoWork shows for this request's Directory session: display name and "name (ID)" label.

    The name comes from the Profile roster (updated from the Directory on
    every login, else the name the Operator gave at create).
    """
    from api import roster

    employee_id = str(session_info.get("username") or "")
    display_name = roster.view(employee_id)["display_name"]
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
    "HERMES_WEBUI_TRUSTED_GROUPS_PIPE_SEPARATOR",
)
_LEFTOVER_CONFIG_KEYS = ("webui_passkey_enabled", "webui_oidc")


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


class StartupCheck(NamedTuple):
    serve: bool
    lines: list[str]


def _leftover_admin_users_line() -> list[str]:
    from api.access import LEFTOVER_ADMIN_USERS_ENV

    if not os.getenv(LEFTOVER_ADMIN_USERS_ENV, "").strip():
        return []
    return [
        f"[!!] Ignoring {LEFTOVER_ADMIN_USERS_ENV}: there is no Admin in the web app (ADR 0006).",
        "     The employee IDs named there log in as Users, to their own Profile, if they have one.",
        "     Manage Profiles with: python3 -m api.operator_cli",
    ]


def _ignored_sticky_profile_line() -> list[str]:
    from api.profiles import ignored_sticky_profile

    name = ignored_sticky_profile()
    if not name:
        return []
    return [
        f"[!!] Ignoring Hermes's active profile '{name}' (~/.hermes/active_profile): GFIT-CoWork always",
        "     runs as the Deployment's default Profile; a User's Profile is reached only through their login.",
        "     The hermes command line still uses it; `hermes profile use default` clears it.",
    ]


def startup_check() -> StartupCheck:
    """Decide from the Directory whether the server may serve.

    ``lines`` is what startup prints. With no Directory nobody could log in,
    and there is no mode with login turned off (ADR 0006), so the server does
    not start, whatever the address. Local development and tests use the
    in-memory Directory.
    """
    lines = [
        f"[!!] Ignoring {setting}: Upstream login is gone; the Directory replaces it."
        for setting in _leftover_login_settings()
    ] + _leftover_admin_users_line() + _ignored_sticky_profile_line()
    if is_directory_enabled():
        return StartupCheck(True, lines)
    lines += [
        "[!!] Refusing to start: no Directory is configured, so nobody could log in.",
        f"     To use the company AD, {_DIRECTORY_HINT}.",
        "     For local development, set HERMES_WEBUI_DIRECTORY=memory and",
        "     HERMES_WEBUI_DIRECTORY_USERS to a JSON file of test users (see TESTING.md).",
    ]
    return StartupCheck(False, lines)
