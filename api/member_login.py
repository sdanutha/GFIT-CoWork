"""GFIT-CoWork -- the login decision for a Directory login.

The order matters (spec, "Login decision"):

1. rate-limit check (per IP)
2. Directory authenticate
3. Admission (:func:`api.access.admit`): an employee ID on the Admin list is
   bound to ``default`` as an Admin; anyone else needs a Profile named after
   their employee ID, and it must not be disabled in the Profile roster
4. issue a session bound to that Profile, with the role

A wrong password and a missing Profile get different messages, but neither the
password nor anything derived from it is logged or stored.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

from api import auth
from api.access import REFUSED_NO_PROFILE, REFUSED_PROFILE_NOT_ACTIVE
from api.directory import DirectoryUnavailable, get_directory

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
        from api.workspace import ensure_member_workspace

        ensure_member_workspace(bound_profile)
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
    """The name GFIT-CoWork shows for a Directory session: display name and "name (ID)" label.

    A Member's name comes from the Profile roster (updated from the Directory
    on every login, else the name the Admin typed); an Admin's from the session.
    """
    from api import roster
    from api.access import ROLE_MEMBER

    employee_id = str(session_info.get("username") or "")
    if session_info.get("role") == ROLE_MEMBER:
        display_name = roster.view(employee_id)["display_name"]
    else:
        display_name = roster.directory_name(session_info.get("display_name"), employee_id)
    return {
        "display_name": display_name,
        "label": f"{display_name} ({employee_id})" if display_name else employee_id,
    }
