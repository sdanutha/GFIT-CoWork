"""GFIT-CoWork -- roles and the Admin-only feature gate.

There are two roles. An **Admin** is an employee ID named in
``HERMES_WEBUI_ADMIN_USERS`` (comma-separated); they log in to the ``default``
Profile and can use everything. Everyone else who logs in is a **User**,
bound to their own Profile.

This module is the one place that decides who is admitted, with which role and
to which Profile (:func:`admit`, Admission), and what a User may call.
Admission runs again on every request from a Directory session, and its answer
is kept as the request's Admission (:func:`request_admission`): the one answer
to "who is calling?" for the rest of that request.
:func:`user_may_call` answers from the route table (``api.route_table``): a User
may call a route whose row says ``USER``; a route whose row says ``ADMIN``, and
any path with no row, including routes added later without one, is refused
(fail closed).

The server gate is the source of truth. The web app hides what its caller may
not use, from :data:`SHELL_FEATURES` (each feature named by its gating route,
answered from this gate), which is cosmetic only.
"""
from __future__ import annotations

import contextlib
import os
import threading
from typing import NamedTuple

from api import route_table

ADMIN_USERS_ENV = "HERMES_WEBUI_ADMIN_USERS"

ROLE_ADMIN = "admin"
ROLE_USER = "user"

ADMIN_ONLY_MESSAGE = "This feature is available to your team's Admin only."

def admin_users() -> frozenset[str]:
    """Return the normalised employee IDs named in ``HERMES_WEBUI_ADMIN_USERS``."""
    from api.directory import normalize_username

    names = (normalize_username(part) for part in os.getenv(ADMIN_USERS_ENV, "").split(","))
    return frozenset(name for name in names if name)


def is_admin(employee_id) -> bool:
    return bool(employee_id) and employee_id in admin_users()


# Why Admission refuses someone.
REFUSED_NO_PROFILE = "no_profile"
REFUSED_PROFILE_NOT_ACTIVE = "profile_not_active"


class Admitted(NamedTuple):
    role: str
    profile: str


class Refused(NamedTuple):
    reason: str


def admit(employee_id: str) -> Admitted | Refused:
    """Admission: may *employee_id* use this Deployment, with which role, in which Profile?"""
    from api import roster
    from api.profiles import named_profile_exists

    if is_admin(employee_id):
        return Admitted(ROLE_ADMIN, "default")
    if not named_profile_exists(employee_id):
        return Refused(REFUSED_NO_PROFILE)
    if roster.is_disabled(employee_id):
        return Refused(REFUSED_PROFILE_NOT_ACTIVE)
    return Admitted(ROLE_USER, employee_id)


# ── The request's Admission ──────────────────────────────────────────────────
#
# Admission runs again on every request from a Directory session. Its answer is
# kept for the rest of that request, on the request thread, and is the one
# answer to "who is calling?". Only an admitted caller has one: a request with
# no Directory session (login turned off, or a public route before login) has
# none. Worker threads carry none. It is cleared with the request Profile
# (api.profiles.clear_request_profile) at the end of every request, before the
# handler serves the next keep-alive request on the same thread.

_request = threading.local()


def admit_request(session_info: dict) -> Admitted | None:
    """Admission again for this request's Directory session.

    Records the Admission as the request's Admission and returns it when it
    still gives the session's role and Profile, or returns None (and records
    none) when it does not: the Profile was deleted or disabled, the Admin list
    changed, or the role is unknown. Called only by the per-request Directory
    session check.
    """
    clear_request_admission()
    _request.directory_session = True
    admission = admit(str(session_info.get("username") or "").strip())
    session = Admitted(session_info.get("role"), str(session_info.get("bound_profile") or "").strip())
    if admission != session:
        return None
    _request.admission = admission
    return admission


def request_admission() -> Admitted | None:
    """This request's Admission, or None when the caller was not admitted."""
    return getattr(_request, "admission", None)


def request_has_directory_session() -> bool:
    """True when this request came with a Directory session, admitted or not.

    With :func:`request_admission` it tells a request with no caller (login
    turned off, a worker thread) from a Directory session whose Admission was
    refused, which must be refused everything (unknown is not allowed).
    """
    return getattr(_request, "directory_session", False)


def caller_is_user() -> bool:
    """True when this request comes from an admitted User (not the Admin)."""
    admission = request_admission()
    return admission is not None and admission.role == ROLE_USER


def caller_bound_profile() -> str | None:
    """The Profile an admitted User's request is bound to, else None (the Admin, or no caller)."""
    return request_admission().profile if caller_is_user() else None


def profile_for_request(admission, *, directory_session: bool, cookie_profile) -> str | None:
    """The Profile a request runs in: the one answer, from its Admission.

    A Directory session runs in its Admission's Profile (a User's own,
    ``default`` for the Admin), whatever cookie the browser sends; one with no
    Admission runs in none. With no Directory session (login turned off) the
    authenticated profile cookie picks it, as Upstream did. None means the
    process's Profile.
    """
    if admission is not None:
        return admission.profile or None
    if directory_session:
        return None
    return cookie_profile or None


def settle_request(handler) -> None:
    """Once its Admission is known: set this request's Profile (the only setter)
    and record its route, for session ownership's read-or-write question."""
    from urllib.parse import urlparse

    from api.helpers import get_profile_cookie
    from api.profiles import set_request_profile

    _request.route = (getattr(handler, "command", "GET"), urlparse(getattr(handler, "path", "") or "").path)
    profile = profile_for_request(
        request_admission(),
        directory_session=request_has_directory_session(),
        cookie_profile=get_profile_cookie(handler),
    )
    if profile:
        set_request_profile(profile)


def request_route() -> tuple[str, str] | None:
    """This request's (method, path), or None off a request thread."""
    return getattr(_request, "route", None)


def clear_request_admission() -> None:
    """Forget this request's Admission, Directory session and route. Safe to call when none was recorded."""
    _request.admission = None
    _request.directory_session = False
    _request.route = None


@contextlib.contextmanager
def without_request_admission():
    """Set the request's Admission aside while the block runs, then restore it.

    Inside, the request has no caller, as on a worker thread (the unconfined
    rule). Only for building a cache keyed by the view, not the caller (the
    session list), so what is built never depends on who built it; the
    caller's own answer is applied after the cache.
    """
    saved = (getattr(_request, "admission", None), getattr(_request, "directory_session", False))
    clear_request_admission()
    try:
        yield
    finally:
        _request.admission, _request.directory_session = saved


def user_entry(method: str, path: str) -> str | None:
    """The route-table pattern that lets a User call *method* *path*, or None if refused."""
    route = route_table.match(method, path)
    return route.pattern if route is not None and route.caller == route_table.USER else None


def user_may_call(method: str, path: str) -> bool:
    """True if a User may call *method* *path*. Unclassified endpoints are refused."""
    return user_entry(method, path) is not None


# ── What the web app shows its caller ───────────────────────────────────────
#
# Each feature of the web app that the gate may refuse, named by the route that
# gates it. The app shell carries the features its caller may use
# (``data-gfit-may`` on ``<html>``); the browser hides the others and does not
# call them. The list follows the gate, so the two cannot disagree; the gate
# stays the authority.
SHELL_FEATURES: dict[str, tuple[str, str]] = {
    "provider_quota": ("GET", "/api/provider/quota"),
    "mcp_servers": ("GET", "/api/mcp/servers"),
    "terminal": ("POST", "/api/terminal/start"),
    "logs": ("GET", "/api/logs"),
    "profiles_admin": ("POST", "/api/profile/create"),
    "settings": ("POST", "/api/settings"),
    "gateway_restart": ("POST", "/api/gateway/restart"),
    "yolo": ("POST", "/api/session/yolo"),
    "reveal_on_server": ("POST", "/api/file/reveal"),
    "onboarding": ("GET", "/api/onboarding/status"),
}


def shell_features(role) -> tuple[str, ...]:
    """The features of :data:`SHELL_FEATURES` a caller with *role* may use.

    A User may use those whose route the gate lets a User call; the Admin, and
    a request with no caller (login turned off), may use every one.
    """
    if role != ROLE_USER:
        return tuple(SHELL_FEATURES)
    return tuple(name for name, (method, path) in SHELL_FEATURES.items() if user_may_call(method, path))
