"""GFIT-CoWork -- Admission and the route gate.

There is one role: the **User**, who logs in with the company AD and is bound
to their own Profile. There is no Admin in the web app (ADR 0006); the
Operator works on the server.

This module is the one place that decides who is admitted and to which
Profile (:func:`admit`, Admission), and what a User may call.
Admission runs again on every request from a Directory session, and its answer
is kept as the request's Admission (:func:`request_admission`): the one answer
to "who is calling?" for the rest of that request.
:func:`user_may_call` answers from the route table (``api.route_table``): a User
may call a route whose row says ``USER`` or ``PUBLIC``; any path with no row,
including routes added later without one, is refused (fail closed).
"""
from __future__ import annotations

import contextlib
import threading
from typing import NamedTuple

from api import route_table

# The Admin list of earlier versions. It grants nothing now; startup warns
# when a Deployment still sets it (ADR 0006).
LEFTOVER_ADMIN_USERS_ENV = "HERMES_WEBUI_ADMIN_USERS"

ROLE_USER = "user"

NOT_AVAILABLE_MESSAGE = "This page or action is not available."


# Why Admission refuses someone.
REFUSED_NO_PROFILE = "no_profile"
REFUSED_PROFILE_NOT_ACTIVE = "profile_not_active"


class Admitted(NamedTuple):
    role: str
    profile: str


class Refused(NamedTuple):
    reason: str


def admit(employee_id: str) -> Admitted | Refused:
    """Admission: may *employee_id* use this Deployment, and in which Profile?

    They need their own Profile, named after them, and it must be active.
    """
    from api import roster
    from api.profiles import named_profile_exists

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
# no Directory session (a public route before login) has none. Worker threads carry none. It is cleared with the request Profile
# (api.profiles.clear_request_profile) at the end of every request, before the
# handler serves the next keep-alive request on the same thread.

_request = threading.local()


def admit_request(session_info: dict) -> Admitted | None:
    """Admission again for this request's Directory session.

    Records the Admission as the request's Admission and returns it when it
    still gives the session's role and Profile, or returns None (and records
    none) when it does not: the Profile was deleted or disabled, or the role
    is not ``user`` (an Admin session from an earlier version). Called only by
    the per-request Directory session check.
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

    With :func:`request_admission` it tells a request with no caller (a public
    route, a worker thread) from a Directory session whose Admission was
    refused, which must be refused everything (unknown is not allowed).
    """
    return getattr(_request, "directory_session", False)


def caller_is_user() -> bool:
    """True when this request comes from an admitted User."""
    return request_admission() is not None


def caller_bound_profile() -> str | None:
    """The Profile an admitted User's request is bound to, else None (no caller)."""
    admission = request_admission()
    return admission.profile if admission is not None else None


def settle_request(handler) -> None:
    """Once its Admission is known: set this request's Profile (the only setter).

    A request runs in its Admission's Profile, the User's own, whatever the
    client sends; a request with no Admission (a public route) runs in none,
    which means the process's Profile.
    """
    from api.profiles import set_request_profile

    admission = request_admission()
    if admission is not None and admission.profile:
        set_request_profile(admission.profile)


def clear_request_admission() -> None:
    """Forget this request's Admission and Directory session. Safe to call when none was recorded."""
    _request.admission = None
    _request.directory_session = False


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
    # Every row is a User's or public; a path with no row is refused.
    route = route_table.match(method, path)
    return route.pattern if route is not None else None


def user_may_call(method: str, path: str) -> bool:
    """True if a User may call *method* *path*. Unclassified endpoints are refused."""
    return user_entry(method, path) is not None
