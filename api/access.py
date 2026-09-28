"""GFIT-CoWork -- roles and the Admin-only feature gate.

There are two roles. An **Admin** is an employee ID named in
``HERMES_WEBUI_ADMIN_USERS`` (comma-separated); they log in to the ``default``
Profile and can use everything. Everyone else who logs in is a **Member**,
pinned to their own Profile.

This module is the one place that decides who is admitted, with which role and
to which Profile (:func:`admit`, Admission), and what a Member may call.
Admission runs again on every request from a Directory session, and its answer
is kept as the request's Admission (:func:`request_admission`): the one answer
to "who is calling?" for the rest of that request.
:func:`member_may_call` classifies a request by method and path against
:data:`MEMBER_ENDPOINTS`; anything not listed there is refused, including
endpoints added later (fail closed). :data:`ADMIN_ONLY_ENDPOINTS` names the
server-level features on purpose so the intent is readable, but a Member is
refused them simply because they are not Member endpoints. A Member entry names
a route exactly, with the methods it handles. A prefix is allowed only where the
path has a variable part (:data:`VARIABLE_PATH_PREFIXES`, each with its reason),
and it covers only the one prefix route the server dispatches on: a literal
route, or a narrower prefix route, under it needs its own entry.
``tests/test_gfit_admin_gate_list.py`` fails when a dispatched route reaches a
User any other way, so a new route stays Admin-only until someone names it.

The server gate is the source of truth. The frontend hides the matching menus
for Members, which is cosmetic only.
"""
from __future__ import annotations

import os
import threading
from typing import NamedTuple

ADMIN_USERS_ENV = "HERMES_WEBUI_ADMIN_USERS"

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ADMIN_ONLY_MESSAGE = "This feature is available to your team's Admin only."

_READ = frozenset({"GET"})
_WRITE = frozenset({"POST"})
_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# (methods, path) a Member may call. A path ending in ``*`` is a prefix; a
# ``<name>`` segment matches exactly one non-empty path segment (an id).
# Longest match wins, so a narrower Admin-only entry can carve a hole in a
# Member prefix.
MEMBER_ENDPOINTS: tuple[tuple[frozenset, str], ...] = (
    # The app shell and its assets
    (_READ, "/"), (_READ, "/index.html"), (_READ, "/sessions"),
    (_READ, "/session/*"), (_READ, "/session/static/*"), (_READ, "/static/*"),
    (_READ, "/manifest.json"), (_READ, "/manifest.webmanifest"),
    (_READ, "/session/manifest.json"), (_READ, "/session/manifest.webmanifest"),
    (_READ, "/sw.js"), (_READ, "/favicon.ico"),
    (_READ, "/health"), (_READ, "/plugins/*"), (_READ, "/dashboard-plugins/*"),
    # Sign in and out
    (_READ, "/api/auth/status"), (_WRITE, "/api/auth/login"), (_WRITE, "/api/auth/logout"),
    # Sessions, each acted on by id inside the Member's pinned Profile
    (_READ, "/api/session"), (_READ, "/api/session/compress/status"),
    (_READ, "/api/session/export"), (_READ, "/api/session/lineage/report"),
    (_READ, "/api/session/status"), (_READ, "/api/session/stream"),
    (_READ, "/api/session/usage"), (_READ, "/api/session/worktree/status"),
    (_READ, "/api/session/yolo"),
    (_WRITE, "/api/session/anchor-scene"), (_WRITE, "/api/session/archive"),
    (_WRITE, "/api/session/branch"), (_WRITE, "/api/session/clear"),
    (_WRITE, "/api/session/compress"), (_WRITE, "/api/session/compress/start"),
    (_WRITE, "/api/session/compression-recovery/start"),
    (_WRITE, "/api/session/conversation-rounds"), (_WRITE, "/api/session/delete"),
    (_WRITE, "/api/session/draft"), (_WRITE, "/api/session/duplicate"),
    (_WRITE, "/api/session/handoff-summary"), (_WRITE, "/api/session/import"),
    (_WRITE, "/api/session/import_cli"), (_WRITE, "/api/session/move"),
    (_WRITE, "/api/session/new"), (_WRITE, "/api/session/pin"),
    (_WRITE, "/api/session/rename"), (_WRITE, "/api/session/retry"),
    (_WRITE, "/api/session/title/regenerate"), (_WRITE, "/api/session/toolsets"),
    (_WRITE, "/api/session/truncate"), (_WRITE, "/api/session/undo"),
    (_WRITE, "/api/session/update"),
    (_READ, "/api/sessions"), (_READ, "/api/sessions/search"),
    (_READ, "/api/sessions/events"), (_READ, "/api/sessions/gateway/stream"),
    (_READ, "/api/sessions/<id>/events"),
    # Chat
    (_WRITE, "/api/chat"), (_WRITE, "/api/chat/start"), (_WRITE, "/api/chat/steer"),
    (_READ, "/api/chat/stream"), (_READ, "/api/chat/stream/status"),
    (_READ, "/api/chat/cancel"),
    (_WRITE, "/api/btw"), (_WRITE, "/api/background"), (_READ, "/api/background/status"),
    (_WRITE, "/api/goal"), (_WRITE, "/api/process-complete-ack"),
    (_WRITE, "/api/bg-task-complete-ack"),
    (_READ, "/api/approval/pending"), (_READ, "/api/approval/stream"),
    (_WRITE, "/api/approval/respond"),
    (_READ, "/api/clarify/pending"), (_READ, "/api/clarify/stream"),
    (_WRITE, "/api/clarify/respond"),
    (_READ, "/api/projects"), (_WRITE, "/api/projects/create"),
    (_WRITE, "/api/projects/rename"), (_WRITE, "/api/projects/delete"),
    (_READ, "/api/prompts"), (_WRITE, "/api/prompts"), (frozenset({"DELETE"}), "/api/prompts"),
    (_READ, "/api/commands"), (_READ, "/api/commands/bundles"),
    (_READ, "/api/commands/moa/resolve"), (_WRITE, "/api/commands/bundles/resolve"),
    (_READ, "/api/personalities"), (_WRITE, "/api/personality/set"),
    (_READ, "/api/reasoning"), (_READ, "/api/media"),
    (_WRITE, "/api/upload"), (_WRITE, "/api/upload/extract"),
    (_WRITE, "/api/transcribe"), (_READ, "/api/transcribe/capability"), (_WRITE, "/api/tts"),
    (_WRITE, "/api/client-events/log"),
    # Models the Admin configured for this Profile
    (_READ, "/api/models"), (_READ, "/api/models/live"), (_READ, "/api/model/auxiliary"),
    # The Member's own Profile: memory, skills, cron jobs
    (_READ, "/api/profiles"), (_READ, "/api/profile/active"),
    (_READ, "/api/memory"), (_WRITE, "/api/memory/write"),
    (_READ, "/api/skills"), (_READ, "/api/skills/content"), (_READ, "/api/skills/usage"),
    (_WRITE, "/api/skills/save"), (_WRITE, "/api/skills/delete"), (_WRITE, "/api/skills/toggle"),
    (_READ, "/api/crons"), (_READ, "/api/crons/status"), (_READ, "/api/crons/recent"),
    (_READ, "/api/crons/history"), (_READ, "/api/crons/output"), (_READ, "/api/crons/run"),
    (_READ, "/api/crons/delivery-options"),
    (_WRITE, "/api/crons/create"), (_WRITE, "/api/crons/update"), (_WRITE, "/api/crons/delete"),
    (_WRITE, "/api/crons/pause"), (_WRITE, "/api/crons/resume"), (_WRITE, "/api/crons/run"),
    # Workspaces and files, confined to the Profile (see api.workspace)
    (_READ, "/api/workspaces"), (_READ, "/api/workspaces/suggest"),
    (_WRITE, "/api/workspaces/add"), (_WRITE, "/api/workspaces/remove"),
    (_WRITE, "/api/workspaces/rename"), (_WRITE, "/api/workspaces/reorder"),
    (_WRITE, "/api/workspace/upload"),
    (_READ, "/api/list"), (_READ, "/api/file"), (_READ, "/api/file/raw"),
    (_READ, "/api/folder/download"),
    (_WRITE, "/api/file/save"), (_WRITE, "/api/file/office-save"),
    (_WRITE, "/api/file/create"), (_WRITE, "/api/file/create-dir"),
    (_WRITE, "/api/file/rename"), (_WRITE, "/api/file/move"),
    (_WRITE, "/api/file/delete"), (_WRITE, "/api/file/path"),
    (_READ, "/api/rollback/list"), (_READ, "/api/rollback/diff"),
    (_WRITE, "/api/rollback/restore"),
    # Read-only workspace git
    (_READ, "/api/git/status"), (_READ, "/api/git/branches"), (_READ, "/api/git/diff"),
    (_READ, "/api/git-info"),
    # Read-only views
    (_READ, "/api/settings"), (_READ, "/api/insights"), (_READ, "/api/project-os/dashboard"),
    (_READ, "/api/wiki/status"), (_READ, "/api/wiki/browse"), (_READ, "/api/wiki/page"),
    (_READ, "/api/notes/sources"), (_READ, "/api/notes/search"), (_READ, "/api/notes/item"),
    (_READ, "/api/plugins"),
    (_READ, "/api/gateway/status"),
    (_READ, "/api/health/agent"), (_READ, "/api/system/health"),
)

# A Member prefix entry is allowed only where the route has a variable part that
# cannot be listed. Each one says why, and covers only the prefix route it names
# (see the module docstring).
VARIABLE_PATH_PREFIXES: dict[str, str] = {
    "/session/*": "session pages by session id",
    "/session/static/*": "static assets requested relative to a session page",
    "/static/*": "static assets by file name",
    "/plugins/*": "plugin assets by plugin name and file",
    "/dashboard-plugins/*": "dashboard plugin assets by plugin name and file",
}

# Server-level features, refused for Members. Listed so the intent is explicit:
# none of them is on the Member list, so they are refused anyway. An entry here
# carves a hole only if it falls under a variable-path prefix above.
ADMIN_ONLY_ENDPOINTS: tuple[tuple[frozenset, str], ...] = (
    (_ANY, "/api/terminal/*"),                       # terminal
    (_ANY, "/api/git/*"),                            # mutating workspace git
    (_ANY, "/api/session/worktree/remove"),
    (_ANY, "/api/extensions/*"), (_ANY, "/extensions/*"),  # extensions
    (_ANY, "/api/updates/*"),                        # self-update
    (_ANY, "/api/shutdown"), (_ANY, "/api/health/restart"), (_ANY, "/api/admin/reload"),
    (_ANY, "/api/logs"),                             # server logs
    (_WRITE, "/api/session/yolo"),                   # YOLO mode
    (_ANY, "/api/providers"), (_ANY, "/api/providers/*"), (_ANY, "/api/provider/*"),
    (_ANY, "/api/model/set"), (_ANY, "/api/default-model"), (_ANY, "/api/models/refresh"),
    (_WRITE, "/api/reasoning"), (_ANY, "/api/mcp/*"),
    (_WRITE, "/api/settings"),                       # Deployment-wide settings
    (_ANY, "/api/onboarding/*"),                     # onboarding
    (_ANY, "/api/gateway/*"),                        # gateway control
    (_ANY, "/api/profile/*"),                        # profile management
    (_ANY, "/api/share/create"), (_ANY, "/api/share/revoke"),  # public share links
    (_ANY, "/api/escape/*"),                         # files outside the Workspace
    (_ANY, "/api/file/open-vscode"), (_ANY, "/api/file/reveal"),  # the server machine
    (_ANY, "/api/commands/exec"),                    # server-side agent commands
    (_ANY, "/api/dashboard/*"),                      # Hermes dashboard control
    (_ANY, "/api/kanban/*"),                         # one board for every Profile
    (_ANY, "/api/approval/inject_test"), (_ANY, "/api/clarify/inject_test"),
    # The session store every Profile shares
    (_ANY, "/api/sessions/cleanup"), (_ANY, "/api/sessions/cleanup_zero_message"),
    (_ANY, "/api/session/recovery/audit"), (_ANY, "/api/session/recovery/repair-safe"),
)


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
    return Admitted(ROLE_MEMBER, employee_id)


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
    return admission is not None and admission.role == ROLE_MEMBER


def caller_bound_profile() -> str | None:
    """The Profile an admitted User's request is bound to, else None (the Admin, or no caller)."""
    return request_admission().profile if caller_is_user() else None


def clear_request_admission() -> None:
    """Forget this request's Admission and Directory session. Safe to call when none was recorded."""
    _request.admission = None
    _request.directory_session = False


def _segments_match(pattern: str, path: str) -> bool:
    """True if *path* matches *pattern* segment by segment, a ``<name>`` segment
    standing for any one non-empty segment."""
    pattern_parts, path_parts = pattern.split("/"), path.split("/")
    return len(pattern_parts) == len(path_parts) and all(
        (want.startswith("<") and want.endswith(">") and got) or want == got
        for want, got in zip(pattern_parts, path_parts)
    )


def _best_match(entries, method: str, path: str) -> tuple[int, str | None]:
    """(length, pattern) of the longest entry matching (method, path), or (-1, None)."""
    best = (-1, None)
    for methods, pattern in entries:
        if method not in methods:
            continue
        if pattern.endswith("*"):
            if path.startswith(pattern[:-1]):
                best = max(best, (len(pattern) - 1, pattern))
        elif _segments_match(pattern, path):
            # A match of the whole path, placeholders included, beats any prefix.
            best = max(best, (len(path) + 1, pattern))
    return best


def member_entry(method: str, path: str) -> str | None:
    """The Member entry that lets a Member call *method* *path*, or None if refused."""
    method = str(method or "").upper()
    path = str(path or "")
    allowed, pattern = _best_match(MEMBER_ENDPOINTS, method, path)
    if allowed >= 0 and allowed > _best_match(ADMIN_ONLY_ENDPOINTS, method, path)[0]:
        return pattern
    return None


def member_may_call(method: str, path: str) -> bool:
    """True if a Member may call *method* *path*. Unclassified endpoints are refused."""
    return member_entry(method, path) is not None
