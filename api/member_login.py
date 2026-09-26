"""GFIT-CoWork -- the login decision for a Directory login.

The order matters (spec, "Login decision"):

1. rate-limit check (per IP)
2. Directory authenticate
3. the Profile named after the employee ID must exist
4. issue a session bound to that Profile

A wrong password and a missing Profile get different messages, but neither the
password nor anything derived from it is logged or stored.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

from api import auth
from api.directory import get_directory

logger = logging.getLogger(__name__)

INCORRECT_MESSAGE = "incorrect username or password"
NO_PROFILE_MESSAGE = (
    "You don't have access to this system yet — contact your team's Admin."
)
RATE_LIMITED_MESSAGE = "Too many attempts. Try again in a minute."


class LoginOutcome(NamedTuple):
    status: int
    error: str | None = None
    session_cookie: str | None = None
    bound_profile: str | None = None


def attempt_login(username, password, client_ip: str) -> LoginOutcome:
    if not auth._check_login_rate(client_ip):
        return LoginOutcome(429, RATE_LIMITED_MESSAGE)

    directory = get_directory()
    identity = directory.authenticate(username, password) if directory else None
    if identity is None:
        auth._record_login_attempt(client_ip)
        return LoginOutcome(401, INCORRECT_MESSAGE)

    from api.profiles import named_profile_exists

    if not named_profile_exists(identity.employee_id):
        logger.info("Directory login for %s refused: no Profile", identity.employee_id)
        return LoginOutcome(403, NO_PROFILE_MESSAGE)

    auth._clear_login_attempts(client_ip)
    cookie = auth.create_session(
        auth_type=auth.DIRECTORY_AUTH_TYPE,
        username=identity.employee_id,
        bound_profile=identity.employee_id,
    )
    return LoginOutcome(200, session_cookie=cookie, bound_profile=identity.employee_id)
