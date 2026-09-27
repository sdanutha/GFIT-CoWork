"""GFIT-CoWork -- roles and the Admin-only feature gate.

There are two roles. An **Admin** is an employee ID named in
``HERMES_WEBUI_ADMIN_USERS`` (comma-separated); they log in to the ``default``
Profile and can use everything. Everyone else who logs in is a **Member**,
pinned to their own Profile.

This module is the one place that decides who is admitted, with which role and
to which Profile (:func:`admit`, Admission), and what a Member may call.
:func:`member_may_call` classifies a request by method and path against
:data:`MEMBER_ENDPOINTS`; anything not listed there is refused, including
endpoints added later (fail closed). :data:`ADMIN_ONLY_ENDPOINTS` names the
server-level features on purpose so the intent is readable, but a Member is
refused them simply because they are not Member endpoints.

The server gate is the source of truth. The frontend hides the matching menus
for Members, which is cosmetic only.
"""
from __future__ import annotations

import os
from typing import NamedTuple

ADMIN_USERS_ENV = "HERMES_WEBUI_ADMIN_USERS"

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ADMIN_ONLY_MESSAGE = "This feature is available to your team's Admin only."

_READ = frozenset({"GET"})
_WRITE = frozenset({"POST"})
_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# (methods, path) a Member may call. A path ending in ``*`` is a prefix.
# Longest match wins, so a narrower Admin-only entry can carve a hole in a
# Member prefix.
MEMBER_ENDPOINTS: tuple[tuple[frozenset, str], ...] = (
    # The app shell and its assets
    (_READ, "/"), (_READ, "/index.html"), (_READ, "/sessions"), (_READ, "/session"),
    (_READ, "/session/*"), (_READ, "/static/*"), (_READ, "/manifest.json"),
    (_READ, "/manifest.webmanifest"), (_READ, "/sw.js"), (_READ, "/favicon.ico"),
    (_READ, "/health"), (_READ, "/plugins/*"), (_READ, "/dashboard-plugins/*"),
    # Sign in and out
    (_READ, "/api/auth/status"), (_WRITE, "/api/auth/login"), (_WRITE, "/api/auth/logout"),
    # Sessions and chat
    (_READ, "/api/session"), (_READ, "/api/session/*"), (_WRITE, "/api/session/*"),
    (_READ, "/api/sessions"), (_READ, "/api/sessions/*"), (_WRITE, "/api/sessions/*"),
    (_READ, "/api/chat/*"), (_WRITE, "/api/chat"), (_WRITE, "/api/chat/*"),
    (_WRITE, "/api/btw"), (_WRITE, "/api/background"), (_READ, "/api/background/*"),
    (_WRITE, "/api/goal"), (_WRITE, "/api/process-complete-ack"),
    (_WRITE, "/api/bg-task-complete-ack"),
    (_READ, "/api/approval/pending"), (_READ, "/api/approval/stream"),
    (_WRITE, "/api/approval/respond"),
    (_READ, "/api/clarify/pending"), (_READ, "/api/clarify/stream"),
    (_WRITE, "/api/clarify/respond"),
    (_READ, "/api/projects"), (_WRITE, "/api/projects/*"),
    (_READ, "/api/prompts"), (_WRITE, "/api/prompts"), (frozenset({"DELETE"}), "/api/prompts"),
    (_READ, "/api/commands"), (_READ, "/api/commands/*"), (_WRITE, "/api/commands/bundles/resolve"),
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
    (_READ, "/api/skills"), (_READ, "/api/skills/*"), (_WRITE, "/api/skills/*"),
    (_READ, "/api/crons"), (_READ, "/api/crons/*"), (_WRITE, "/api/crons/*"),
    # Workspaces and files, confined to the Profile (see api.workspace)
    (_READ, "/api/workspaces"), (_READ, "/api/workspaces/suggest"), (_WRITE, "/api/workspaces/*"),
    (_WRITE, "/api/workspace/upload"),
    (_READ, "/api/list"), (_READ, "/api/file"), (_READ, "/api/file/raw"),
    (_READ, "/api/folder/download"), (_WRITE, "/api/file/*"),
    (_READ, "/api/rollback/*"), (_WRITE, "/api/rollback/restore"),
    # Read-only workspace git
    (_READ, "/api/git/status"), (_READ, "/api/git/branches"), (_READ, "/api/git/diff"),
    (_READ, "/api/git-info"),
    # Read-only views
    (_READ, "/api/settings"), (_READ, "/api/insights"), (_READ, "/api/project-os/dashboard"),
    (_READ, "/api/wiki/*"), (_READ, "/api/notes/*"), (_READ, "/api/plugins"),
    (_READ, "/api/gateway/status"),
    (_READ, "/api/health/agent"), (_READ, "/api/system/health"),
)

# Server-level features, refused for Members. Listed so the intent is explicit;
# they carve holes in the Member prefixes above.
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
    (_ANY, "/api/escape/*"), (_ANY, "/api/file/open-vscode"),  # files outside the Workspace
    (_ANY, "/api/commands/exec"),                    # server-side agent commands
    (_ANY, "/api/dashboard/*"),                      # Hermes dashboard control
    (_ANY, "/api/kanban/*"),                         # one board for every Profile
    (_ANY, "/api/approval/inject_test"), (_ANY, "/api/clarify/inject_test"),
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


def _match_length(entries, method: str, path: str) -> int:
    """Length of the longest entry matching (method, path), or -1."""
    best = -1
    for methods, pattern in entries:
        if method not in methods:
            continue
        if pattern.endswith("*"):
            if path.startswith(pattern[:-1]):
                best = max(best, len(pattern) - 1)
        elif path == pattern:
            best = max(best, len(pattern) + 1)  # an exact match beats any prefix
    return best


def member_may_call(method: str, path: str) -> bool:
    """True if a Member may call *method* *path*. Unclassified endpoints are refused."""
    method = str(method or "").upper()
    path = str(path or "")
    allowed = _match_length(MEMBER_ENDPOINTS, method, path)
    return allowed >= 0 and allowed > _match_length(ADMIN_ONLY_ENDPOINTS, method, path)
