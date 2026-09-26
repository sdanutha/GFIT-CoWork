"""GFIT-CoWork -- the login decision for a Directory login.

The order matters (spec, "Login decision"):

1. rate-limit check (per IP)
2. Directory authenticate
3. an employee ID on the Admin list is bound to ``default`` as an Admin
4. anyone else needs a Profile named after their employee ID
5. issue a session bound to that Profile, with the role

A wrong password and a missing Profile get different messages, but neither the
password nor anything derived from it is logged or stored.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

from api import auth
from api.directory import DirectoryUnavailable, get_directory

logger = logging.getLogger(__name__)

INCORRECT_MESSAGE = "incorrect username or password"
NO_PROFILE_MESSAGE = (
    "You don't have access to this system yet — contact your team's Admin."
)
RATE_LIMITED_MESSAGE = "Too many attempts. Try again in a minute."
UNAVAILABLE_MESSAGE = (
    "The company directory is unavailable right now. Try again in a few minutes."
)


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

    from api.access import ROLE_ADMIN, ROLE_MEMBER, is_admin
    from api.profiles import named_profile_exists

    if is_admin(identity.employee_id):
        role, bound_profile = ROLE_ADMIN, "default"
    elif named_profile_exists(identity.employee_id):
        role, bound_profile = ROLE_MEMBER, identity.employee_id
    else:
        logger.info("Directory login for %s refused: no Profile", identity.employee_id)
        return LoginOutcome(403, NO_PROFILE_MESSAGE)

    auth._clear_login_attempts(client_ip)
    if role == ROLE_MEMBER:
        from api.workspace import ensure_member_workspace

        ensure_member_workspace(bound_profile)
    cookie = auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE,
        username=identity.employee_id,
        bound_profile=bound_profile,
        role=role,
    )
    return LoginOutcome(200, session_cookie=cookie, bound_profile=bound_profile)
