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
refused them simply because they are not Member endpoints. A Member entry names
a route exactly; a prefix is allowed only where the path has a variable part
(:data:`VARIABLE_PATH_PREFIXES`), and ``tests/test_gfit_admin_gate_list.py``
fails when a dispatched route reaches Members any other way.

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

# (methods, path) a Member may call. A path ending in ``*`` is a prefix; a
# ``<name>`` segment matches exactly one non-empty path segment (an id).
# Longest match wins, so a narrower Admin-only entry can carve a hole in a
# Member prefix.
MEMBER_ENDPOINTS: tuple[tuple[frozenset, str], ...] = (
    # The app shell and its assets
    (_READ, "/"), (_READ, "/index.html"), (_READ, "/sessions"),
    (_READ, "/session/*"), (_READ, "/static/*"), (_READ, "/manifest.json"),
    (_READ, "/manifest.webmanifest"), (_READ, "/sw.js"), (_READ, "/favicon.ico"),
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
    (_READ, "/api/wiki/*"), (_READ, "/api/notes/*"), (_READ, "/api/plugins"),
    (_READ, "/api/gateway/status"),
    (_READ, "/api/health/agent"), (_READ, "/api/system/health"),
)

# A Member prefix entry is allowed only where the route has a variable part that
# cannot be listed. Each one says why. tests/test_gfit_admin_gate_list.py fails
# when a route reaches Members through any other prefix.
VARIABLE_PATH_PREFIXES: dict[str, str] = {
    "/session/*": "session pages by session id, and their static assets",
    "/static/*": "static assets by file name",
    "/plugins/*": "plugin assets by plugin name and file",
    "/dashboard-plugins/*": "dashboard plugin assets by plugin name and file",
}

# Shortcut prefixes from before every Member route was named exactly. Each one is
# being replaced by exact entries for the routes under it (admin-gate-exact
# tickets 03-05 empty this list). Do not add to it.
LEGACY_PREFIXES: frozenset[str] = frozenset({
    "/api/crons/*", "/api/skills/*", "/api/commands/*", "/api/wiki/*", "/api/notes/*",
})

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


def _segments_match(pattern: str, path: str) -> bool:
    """True if *path* matches *pattern* segment by segment, a ``<name>`` segment
    standing for any one non-empty segment."""
    wanted, given = pattern.split("/"), path.split("/")
    return len(wanted) == len(given) and all(
        (w.startswith("<") and w.endswith(">") and g) or w == g
        for w, g in zip(wanted, given)
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
        elif "<" in pattern:
            if _segments_match(pattern, path):
                best = max(best, (len(pattern) + 1, pattern))
        elif path == pattern:
            best = max(best, (len(pattern) + 1, pattern))  # an exact match beats any prefix
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
