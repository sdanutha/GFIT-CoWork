"""GFIT-CoWork -- the route table: one row per HTTP route the server dispatches.

A row says everything the server decides about a route before its handler
runs:

- **caller**: who may call it. ``USER`` routes are open to an admitted User;
  ``ADMIN`` routes are the Admin's only (ADR 0002). A row cannot be built
  without one, and a path with no row is refused to a User (fail closed);
- **session**: whether the route names a session and, if so, whether it reads
  or writes it (``READ``/``WRITE``, by what it does, not by its method). A
  route whose class is unclear (it reads and records something, or runs the
  session's model) is a ``WRITE``: unknown is not allowed;
- **csrf**: whether an unsafe request to it must carry the session's CSRF
  token. Only login (no session yet) and the browser's CSP reports are exempt.

A pattern is an exact path, a path with ``<name>`` segments (each one matches
exactly one non-empty segment, an id), or a prefix ending in ``*``. One matcher
(:func:`match`) chooses the row for a request: an exact path beats a
``<name>`` pattern, which beats a prefix; among prefixes the longest wins. The
Admin gate (``api.access``), session ownership's read-or-write question
(``api.session_ownership``) and the CSRF check all read the row it chooses.

A User prefix row is allowed only where the path has a variable part that
cannot be listed (:data:`VARIABLE_PATH_PREFIXES`, each with its reason).
"""
from __future__ import annotations

from dataclasses import dataclass

USER = "user"
ADMIN = "admin"

READ = "read"
WRITE = "write"

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class Route:
    """One route: see the module docstring for what each field means."""

    method: str
    pattern: str
    caller: str
    session: str | None = None
    csrf: bool = True

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"{self.pattern}: unknown method {self.method!r}")
        if self.caller not in (USER, ADMIN):
            raise ValueError(f"{self.method} {self.pattern}: say who may call it (USER or ADMIN)")
        if self.session not in (None, READ, WRITE):
            raise ValueError(f"{self.method} {self.pattern}: session is READ, WRITE or None")
        if not self.pattern.startswith("/"):
            raise ValueError(f"{self.pattern}: a pattern starts with '/'")

    @property
    def is_prefix(self) -> bool:
        return self.pattern.endswith("*")

    def matches(self, path: str) -> bool:
        """True if *path* is this route (method aside)."""
        if self.is_prefix:
            return path.startswith(self.pattern[:-1])
        pattern_parts, path_parts = self.pattern.split("/"), path.split("/")
        return len(pattern_parts) == len(path_parts) and all(
            (want.startswith("<") and want.endswith(">") and got) or want == got
            for want, got in zip(pattern_parts, path_parts, strict=True)
        )

    def _rank(self) -> tuple[int, int]:
        if self.is_prefix:
            return (0, len(self.pattern))
        return (1, 0) if "<" in self.pattern else (2, 0)


def _route(method):
    def build(pattern: str, caller: str, **fields) -> Route:
        return Route(method, pattern, caller, **fields)

    return build


_get, _post, _put, _patch, _delete = (_route(m) for m in METHODS)


ROUTES: tuple[Route, ...] = (
    # ── GET ──
    _get("/session/static/*", USER),
    _get("/session/manifest.json", USER),
    _get("/session/manifest.webmanifest", USER),
    _get("/", USER),
    _get("/index.html", USER),
    _get("/session/*", USER, session=READ),
    _get("/sessions", USER),
    _get("/share", ADMIN),
    _get("/share/*", ADMIN),
    _get("/login", ADMIN),
    _get("/api/auth/status", USER),
    _get("/api/share/*", ADMIN),
    _get("/manifest.json", USER),
    _get("/manifest.webmanifest", USER),
    _get("/sw.js", USER),
    _get("/favicon.ico", USER),
    _get("/api/insights", USER),
    _get("/api/project-os/dashboard", USER),
    _get("/api/kanban/*", ADMIN),
    _get("/api/wiki/status", USER),
    _get("/api/wiki/browse", USER),
    _get("/api/wiki/page", USER),
    _get("/api/logs", ADMIN),
    _get("/health", USER),
    _get("/api/health/agent", USER),
    _get("/api/system/health", USER),
    _get("/api/models", USER),
    _get("/api/models/live", USER),
    _get("/api/model/auxiliary", USER),
    _get("/api/dashboard/status", ADMIN),
    _get("/api/dashboard/config", ADMIN),
    _get("/api/providers", ADMIN),
    _get("/api/plugins", USER),
    _get("/api/provider/quota", ADMIN),
    _get("/api/provider/cost-history", ADMIN),
    _get("/api/settings", USER),
    _get("/api/transcribe/capability", USER),
    _get("/api/reasoning", USER),
    _get("/api/onboarding/status", ADMIN),
    _get("/api/extensions/status", ADMIN),
    _get("/extensions/*", ADMIN),
    _get("/static/*", USER),
    _get("/api/session/worktree/status", USER, session=READ),
    _get("/api/session/compress/status", USER, session=READ),
    _get("/api/session", USER, session=READ),
    _get("/api/session/lineage/report", USER, session=READ),
    _get("/api/session/recovery/audit", ADMIN),
    _get("/api/session/status", USER, session=READ),
    _get("/api/session/yolo", USER, session=READ),
    _get("/api/session/usage", USER, session=READ),
    _get("/api/background/status", USER, session=READ),
    _get("/api/sessions", USER),
    _get("/api/projects", USER),
    _get("/api/prompts", USER),
    _get("/api/session/export", USER, session=READ),
    _get("/api/workspaces", USER),
    _get("/api/workspaces/suggest", USER),
    _get("/api/sessions/search", USER),
    _get("/api/list", USER, session=READ),
    _get("/api/escape/list", ADMIN, session=READ),
    _get("/api/git/status", USER, session=READ),
    _get("/api/git/branches", USER, session=READ),
    _get("/api/git/diff", USER, session=READ),
    _get("/api/personalities", USER),
    _get("/api/git-info", USER, session=READ),
    _get("/api/commands", USER),
    _get("/api/commands/bundles", USER),
    _get("/api/commands/moa/resolve", USER),
    _get("/api/chat/stream/status", USER, session=READ),
    _get("/api/chat/cancel", USER, session=WRITE),
    _get("/api/chat/stream", USER, session=READ),
    _get("/api/terminal/output", ADMIN, session=READ),
    _get("/api/sessions/gateway/stream", USER),
    _get("/api/sessions/events", USER),
    _get("/api/media", USER),
    _get("/api/file/raw", USER, session=READ),
    _get("/api/escape/file/raw", ADMIN, session=READ),
    _get("/api/folder/download", USER, session=READ),
    _get("/api/file", USER, session=READ),
    _get("/api/escape/file/read", ADMIN, session=READ),
    _get("/api/approval/pending", USER, session=READ),
    _get("/api/approval/stream", USER, session=READ),
    _get("/api/approval/inject_test", ADMIN, session=WRITE),
    _get("/api/clarify/pending", USER, session=READ),
    _get("/api/clarify/stream", USER, session=READ),
    _get("/api/session/stream", USER, session=READ),
    _get("/api/clarify/inject_test", ADMIN, session=WRITE),
    _get("/api/onboarding/oauth/poll", ADMIN),
    _get("/api/crons", USER),
    _get("/api/crons/output", USER),
    _get("/api/crons/history", USER),
    _get("/api/crons/run", USER),
    _get("/api/crons/recent", USER),
    _get("/api/crons/status", USER),
    _get("/api/crons/delivery-options", USER),
    _get("/api/skills", USER),
    _get("/api/skills/usage", USER),
    _get("/api/skills/content", USER),
    _get("/api/memory", USER),
    _get("/api/profiles", USER),
    _get("/api/profile/active", USER),
    _get("/api/gateway/status", USER),
    _get("/api/mcp/servers", ADMIN),
    _get("/api/mcp/tools", ADMIN),
    _get("/api/notes/sources", USER),
    _get("/api/notes/search", USER),
    _get("/api/notes/item", USER),
    _get("/api/rollback/list", USER),
    _get("/api/rollback/diff", USER),
    _get("/plugins/*", USER),
    _get("/dashboard-plugins/*", USER),
    _get("/api/sessions/<id>/events", USER, session=READ),
    # ── POST ──
    _post("/api/csp-report", ADMIN, csrf=False),
    _post("/api/process-complete-ack", USER),
    _post("/api/shutdown", ADMIN),
    _post("/api/health/restart", ADMIN),
    _post("/api/upload", USER, session=WRITE),
    _post("/api/upload/extract", USER, session=WRITE),
    _post("/api/workspace/upload", USER, session=WRITE),
    _post("/api/transcribe", USER),
    _post("/api/tts", USER),
    _post("/api/client-events/log", USER),
    _post("/api/escape/authorize", ADMIN, session=WRITE),
    _post("/api/extensions/toggle", ADMIN),
    _post("/api/extensions/sidecar-proxy-consent", ADMIN),
    _post("/api/session/recovery/repair-safe", ADMIN),
    _post("/api/kanban/*", ADMIN),
    _post("/api/dashboard/config", ADMIN),
    _post("/api/prompts", USER),
    _post("/api/share/create", ADMIN, session=WRITE),
    _post("/api/share/revoke", ADMIN, session=WRITE),
    _post("/api/session/new", USER, session=WRITE),
    _post("/api/session/compression-recovery/start", USER, session=WRITE),
    _post("/api/session/duplicate", USER, session=WRITE),
    _post("/api/default-model", ADMIN),
    _post("/api/model/set", ADMIN),
    _post("/api/providers", ADMIN),
    _post("/api/providers/delete", ADMIN),
    _post("/api/providers/self-hosted", ADMIN),
    _post("/api/models/refresh", ADMIN),
    _post("/api/reasoning", ADMIN),
    _post("/api/admin/reload", ADMIN),
    _post("/api/sessions/cleanup", ADMIN),
    _post("/api/sessions/cleanup_zero_message", ADMIN),
    _post("/api/session/anchor-scene", USER, session=WRITE),
    _post("/api/session/rename", USER, session=WRITE),
    _post("/api/session/title/regenerate", USER, session=WRITE),
    _post("/api/personality/set", USER, session=WRITE),
    _post("/api/session/toolsets", USER, session=WRITE),
    _post("/api/session/draft", USER, session=WRITE),
    _post("/api/session/update", USER, session=WRITE),
    _post("/api/session/worktree/remove", ADMIN, session=WRITE),
    _post("/api/session/delete", USER, session=WRITE),
    _post("/api/session/clear", USER, session=WRITE),
    _post("/api/session/truncate", USER, session=WRITE),
    _post("/api/session/branch", USER, session=WRITE),
    _post("/api/session/compress/start", USER, session=WRITE),
    _post("/api/session/compress", USER, session=WRITE),
    _post("/api/session/conversation-rounds", USER, session=READ),
    _post("/api/session/handoff-summary", USER, session=WRITE),
    _post("/api/session/retry", USER, session=WRITE),
    _post("/api/session/undo", USER, session=WRITE),
    _post("/api/session/yolo", ADMIN, session=WRITE),
    _post("/api/btw", USER, session=WRITE),
    _post("/api/background", USER, session=WRITE),
    _post("/api/goal", USER, session=WRITE),
    _post("/api/bg-task-complete-ack", USER, session=WRITE),
    _post("/api/chat/start", USER, session=WRITE),
    _post("/api/chat", USER, session=WRITE),
    _post("/api/chat/steer", USER, session=WRITE),
    _post("/api/terminal/start", ADMIN, session=WRITE),
    _post("/api/terminal/input", ADMIN, session=WRITE),
    _post("/api/terminal/resize", ADMIN, session=WRITE),
    _post("/api/terminal/close", ADMIN, session=WRITE),
    _post("/api/crons/create", USER),
    _post("/api/crons/update", USER),
    _post("/api/crons/delete", USER),
    _post("/api/crons/run", USER),
    _post("/api/crons/pause", USER),
    _post("/api/crons/resume", USER),
    _post("/api/git/stage", ADMIN, session=WRITE),
    _post("/api/git/unstage", ADMIN, session=WRITE),
    _post("/api/git/discard", ADMIN, session=WRITE),
    _post("/api/git/commit-message", ADMIN, session=WRITE),
    _post("/api/git/commit-message-selected", ADMIN, session=WRITE),
    _post("/api/git/commit", ADMIN, session=WRITE),
    _post("/api/git/commit-selected", ADMIN, session=WRITE),
    _post("/api/git/fetch", ADMIN, session=WRITE),
    _post("/api/git/pull", ADMIN, session=WRITE),
    _post("/api/git/push", ADMIN, session=WRITE),
    _post("/api/git/checkout", ADMIN, session=WRITE),
    _post("/api/git/stash-checkout", ADMIN, session=WRITE),
    _post("/api/file/delete", USER, session=WRITE),
    _post("/api/file/save", USER, session=WRITE),
    _post("/api/file/office-save", USER, session=WRITE),
    _post("/api/file/create", USER, session=WRITE),
    _post("/api/file/rename", USER, session=WRITE),
    _post("/api/file/move", USER, session=WRITE),
    _post("/api/file/create-dir", USER, session=WRITE),
    _post("/api/file/reveal", ADMIN, session=WRITE),
    _post("/api/file/path", USER, session=READ),
    _post("/api/file/open-vscode", ADMIN, session=WRITE),
    _post("/api/workspaces/add", USER),
    _post("/api/workspaces/remove", USER),
    _post("/api/workspaces/rename", USER),
    _post("/api/workspaces/reorder", USER),
    _post("/api/approval/respond", USER, session=WRITE),
    _post("/api/clarify/respond", USER, session=WRITE),
    _post("/api/commands/bundles/resolve", USER),
    _post("/api/commands/exec", ADMIN),
    _post("/api/skills/save", USER),
    _post("/api/skills/delete", USER),
    _post("/api/skills/toggle", USER),
    _post("/api/memory/write", USER),
    _post("/api/gateway/restart", ADMIN),
    _post("/api/gateway/start", ADMIN),
    _post("/api/gateway/stop", ADMIN),
    _post("/api/profile/switch", ADMIN),
    _post("/api/profile/create", ADMIN),
    _post("/api/profile/disable", ADMIN),
    _post("/api/profile/enable", ADMIN),
    _post("/api/profile/delete", ADMIN),
    _post("/api/settings", ADMIN),
    _post("/api/onboarding/oauth/start", ADMIN),
    _post("/api/onboarding/oauth/cancel", ADMIN),
    _post("/api/onboarding/setup", ADMIN),
    _post("/api/onboarding/complete", ADMIN),
    _post("/api/onboarding/probe", ADMIN),
    _post("/api/session/pin", USER, session=WRITE),
    _post("/api/session/archive", USER, session=WRITE),
    _post("/api/session/move", USER, session=WRITE),
    _post("/api/projects/create", USER),
    _post("/api/projects/rename", USER),
    _post("/api/projects/delete", USER),
    _post("/api/session/import", USER),
    _post("/api/session/import_cli", USER, session=WRITE),
    _post("/api/auth/login", USER, csrf=False),
    _post("/api/auth/logout", USER),
    _post("/api/rollback/restore", USER),
    # ── PUT ──
    _put("/api/mcp/servers/*", ADMIN),
    # ── PATCH ──
    _patch("/api/mcp/servers/*", ADMIN),
    _patch("/api/kanban/*", ADMIN),
    # ── DELETE ──
    _delete("/api/mcp/servers/*", ADMIN),
    _delete("/api/prompts", USER),
    _delete("/api/kanban/*", ADMIN),
)

# A User prefix row is allowed only for a variable part that cannot be listed.
VARIABLE_PATH_PREFIXES: dict[str, str] = {
    "/session/*": "session pages by session id",
    "/session/static/*": "static assets requested relative to a session page",
    "/static/*": "static assets by file name",
    "/plugins/*": "plugin assets by plugin name and file",
    "/dashboard-plugins/*": "dashboard plugin assets by plugin name and file",
}

_EXACT: dict[tuple[str, str], Route] = {
    (route.method, route.pattern): route for route in ROUTES if not route.is_prefix and "<" not in route.pattern
}
_OTHERS: tuple[Route, ...] = tuple(
    route for route in ROUTES if route.is_prefix or "<" in route.pattern
)


def match(method: str, path: str) -> Route | None:
    """The row for a request to *method* *path*, or None when there is none."""
    method = str(method or "").upper()
    path = str(path or "")
    exact = _EXACT.get((method, path))
    if exact is not None:
        return exact
    best = None
    for route in _OTHERS:
        if route.method == method and route.matches(path) and (best is None or route._rank() > best._rank()):
            best = route
    return best


def routes_at(path: str) -> tuple[Route, ...]:
    """The rows *path* is, one per method that has one."""
    found = (match(method, path) for method in METHODS)
    return tuple(route for route in found if route is not None)


def csrf_exempt(method: str, path: str) -> bool:
    """True when an unsafe request to *method* *path* need not carry a CSRF token."""
    route = match(method, path)
    return route is not None and not route.csrf
