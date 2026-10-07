"""GFIT-CoWork -- the route table: one row per HTTP route the server dispatches.

A row says everything the server decides about a route before its handler
runs:

- **caller**: who may call it. ``USER`` routes are open to an admitted User;
  ``PUBLIC`` routes are served before login too (the login page and its
  assets, health, the manifests). There is no other caller: there is no Admin
  in the web app (ADR 0006). A row cannot be built without one, and a path
  with no row is refused (fail closed);
- **csrf**: whether an unsafe request to it must carry the session's CSRF
  token. Only login (no session yet) and the
  deprecated process-complete ack (answered 410 Gone to a stale tab that has
  no token) are exempt;
- **handler**: the name of the route module's function that serves it. The
  server dispatches every request by looking up its row;
- **session_guard**: whether session ownership checks the session ids the
  request names (``session_id`` in the query or body, the id in the session
  events path) before the handler runs. Pages, static assets and the routes
  served before login do not run it: a ``PUBLIC`` row cannot have it, since a
  request with no caller is refused every session (ticket 07). Logout does
  not run it either: it names no session, and a session that is no longer
  admitted may still end itself;
- **names_stream**: whether the request names a stream by its ``stream_id``;
  the session guard then asks session ownership about the session that owns
  the stream;
- **body**: ``json`` when the server reads the request's JSON body before the
  handler runs (and the session guard checks it), ``own`` when the handler
  reads its own body (multipart uploads, raw reports).

A pattern is an exact path, a path with ``<name>`` segments (each one matches
exactly one non-empty segment, an id), or a prefix ending in ``*``. One matcher
(:func:`match`) chooses the row for a request: an exact path beats a
``<name>`` pattern, which beats a prefix; among prefixes the longest wins. The
route gate (``api.access``), the login check (``api.auth``) and the CSRF
check all read the row it chooses.

A User prefix row is allowed only where the path has a variable part that
cannot be listed (:data:`VARIABLE_PATH_PREFIXES`, each with its reason).
"""
from __future__ import annotations

from dataclasses import dataclass

USER = "user"
PUBLIC = "public"

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class Route:
    """One route: see the module docstring for what each field means."""

    method: str
    pattern: str
    caller: str
    csrf: bool = True
    handler: str | None = None
    session_guard: bool = True
    body: str = "json"
    names_stream: bool = False

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"{self.pattern}: unknown method {self.method!r}")
        if self.caller not in (USER, PUBLIC):
            raise ValueError(f"{self.method} {self.pattern}: say who may call it (USER or PUBLIC)")
        if not self.handler:
            raise ValueError(f"{self.method} {self.pattern}: name the route module's function that serves it")
        if self.body not in ("json", "own"):
            raise ValueError(f"{self.method} {self.pattern}: body is 'json' or 'own'")
        if not self.pattern.startswith("/"):
            raise ValueError(f"{self.pattern}: a pattern starts with '/'")
        if self.caller == PUBLIC and self.session_guard:
            raise ValueError(f"{self.method} {self.pattern}: a PUBLIC route has no caller to own sessions; session_guard=False")

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
    _get("/session/static/*", PUBLIC, handler="_get_session_static", session_guard=False),
    _get("/session/manifest.json", PUBLIC, handler="_get_session_manifest", session_guard=False),
    _get("/session/manifest.webmanifest", PUBLIC, handler="_get_session_manifest", session_guard=False),
    _get("/", USER, handler="_get_app_shell", session_guard=False),
    _get("/index.html", USER, handler="_get_app_shell", session_guard=False),
    _get("/session/*", USER, handler="_get_app_shell", session_guard=False),
    _get("/sessions", USER, handler="_get_app_shell", session_guard=False),
    _get("/login", PUBLIC, handler="_get_login", session_guard=False),
    _get("/api/auth/status", PUBLIC, handler="_get_api_auth_status", session_guard=False),
    _get("/manifest.json", PUBLIC, handler="_get_manifest", session_guard=False),
    _get("/manifest.webmanifest", PUBLIC, handler="_get_manifest", session_guard=False),
    _get("/sw.js", PUBLIC, handler="_get_sw_js", session_guard=False),
    _get("/favicon.ico", PUBLIC, handler="_get_favicon_ico", session_guard=False),
    _get("/api/insights", USER, handler="_get_api_insights"),
    _get("/api/project-os/dashboard", USER, handler="_get_api_project_os_dashboard"),
    _get("/api/wiki/status", USER, handler="_get_api_wiki_status"),
    _get("/api/wiki/browse", USER, handler="_get_api_wiki_browse"),
    _get("/api/wiki/page", USER, handler="_get_api_wiki_page"),
    _get("/health", PUBLIC, handler="_get_health", session_guard=False),
    _get("/api/health/agent", USER, handler="_get_api_health_agent"),
    _get("/api/system/health", USER, handler="_get_api_system_health"),
    _get("/api/models", USER, handler="_get_api_models"),
    _get("/api/models/live", USER, handler="_get_api_models_live"),
    _get("/api/model/auxiliary", USER, handler="_get_api_model_auxiliary"),
    _get("/api/plugins", USER, handler="_get_api_plugins"),
    _get("/api/settings", USER, handler="_get_api_settings"),
    _get("/api/transcribe/capability", USER, handler="_get_api_transcribe_capability"),
    _get("/api/reasoning", USER, handler="_get_api_reasoning"),
    _get("/static/*", PUBLIC, handler="_get_static", session_guard=False),
    _get("/api/session/worktree/status", USER, handler="_get_api_session_worktree_status"),
    _get("/api/session/compress/status", USER, handler="_get_api_session_compress_status"),
    _get("/api/session", USER, handler="_get_api_session"),
    _get("/api/session/lineage/report", USER, handler="_get_api_session_lineage_report"),
    _get("/api/session/status", USER, handler="_get_api_session_status"),
    _get("/api/session/usage", USER, handler="_get_api_session_usage"),
    _get("/api/background/status", USER, handler="_get_api_background_status"),
    _get("/api/sessions", USER, handler="_get_api_sessions"),
    _get("/api/projects", USER, handler="_get_api_projects"),
    _get("/api/prompts", USER, handler="_get_api_prompts"),
    _get("/api/session/export", USER, handler="_get_api_session_export"),
    _get("/api/workspaces", USER, handler="_get_api_workspaces"),
    _get("/api/workspaces/suggest", USER, handler="_get_api_workspaces_suggest"),
    _get("/api/sessions/search", USER, handler="_get_api_sessions_search"),
    _get("/api/list", USER, handler="_get_api_list"),
    _get("/api/git/status", USER, handler="_get_api_git_status"),
    _get("/api/git/branches", USER, handler="_get_api_git_branches"),
    _get("/api/git/diff", USER, handler="_get_api_git_diff"),
    _get("/api/personalities", USER, handler="_get_api_personalities"),
    _get("/api/git-info", USER, handler="_get_api_git_info"),
    _get("/api/commands", USER, handler="_get_api_commands"),
    _get("/api/commands/bundles", USER, handler="_get_api_commands_bundles"),
    _get("/api/commands/moa/resolve", USER, handler="_get_api_commands_moa_resolve"),
    _get("/api/chat/stream/status", USER, handler="_get_api_chat_stream_status", names_stream=True),
    _get("/api/chat/cancel", USER, handler="_get_api_chat_cancel", names_stream=True),
    _get("/api/chat/stream", USER, handler="_get_api_chat_stream", names_stream=True),
    _get("/api/sessions/gateway/stream", USER, handler="_get_api_sessions_gateway_stream"),
    _get("/api/sessions/events", USER, handler="_get_api_sessions_events"),
    _get("/api/media", USER, handler="_get_api_media"),
    _get("/api/file/raw", USER, handler="_get_api_file_raw"),
    _get("/api/folder/download", USER, handler="_get_api_folder_download"),
    _get("/api/file", USER, handler="_get_api_file"),
    _get("/api/approval/pending", USER, handler="_get_api_approval_pending"),
    _get("/api/approval/stream", USER, handler="_get_api_approval_stream"),
    _get("/api/clarify/pending", USER, handler="_get_api_clarify_pending"),
    _get("/api/clarify/stream", USER, handler="_get_api_clarify_stream"),
    _get("/api/session/stream", USER, handler="_get_api_session_stream"),
    _get("/api/crons", USER, handler="_get_api_crons"),
    _get("/api/crons/output", USER, handler="_get_api_crons_output"),
    _get("/api/crons/history", USER, handler="_get_api_crons_history"),
    _get("/api/crons/run", USER, handler="_get_api_crons_run"),
    _get("/api/crons/recent", USER, handler="_get_api_crons_recent"),
    _get("/api/crons/status", USER, handler="_get_api_crons_status"),
    _get("/api/crons/delivery-options", USER, handler="_get_api_crons_delivery_options"),
    _get("/api/skills", USER, handler="_get_api_skills"),
    _get("/api/skills/usage", USER, handler="_get_api_skills_usage"),
    _get("/api/skills/content", USER, handler="_get_api_skills_content"),
    _get("/api/memory", USER, handler="_get_api_memory"),
    _get("/api/profiles", USER, handler="_get_api_profiles"),
    _get("/api/profile/active", USER, handler="_get_api_profile_active"),
    _get("/api/gateway/status", USER, handler="_get_api_gateway_status"),
    _get("/api/notes/sources", USER, handler="_get_api_notes_sources"),
    _get("/api/notes/search", USER, handler="_get_api_notes_search"),
    _get("/api/notes/item", USER, handler="_get_api_notes_item"),
    _get("/api/rollback/list", USER, handler="_get_api_rollback_list"),
    _get("/api/rollback/diff", USER, handler="_get_api_rollback_diff"),
    _get("/plugins/*", USER, handler="_get_plugins", session_guard=False),
    _get("/dashboard-plugins/*", USER, handler="_get_dashboard_plugins", session_guard=False),
    _get("/api/sessions/<id>/events", USER, handler="_get_session_events"),
    # ── POST ──
    _post("/api/process-complete-ack", USER, handler="_post_api_process_complete_ack", body="own", csrf=False),
    _post("/api/upload", USER, handler="_post_api_upload", body="own"),
    _post("/api/upload/extract", USER, handler="_post_api_upload_extract", body="own"),
    _post("/api/workspace/upload", USER, handler="_post_api_workspace_upload", body="own"),
    _post("/api/transcribe", USER, handler="_post_api_transcribe", body="own"),
    _post("/api/tts", USER, handler="_post_api_tts", body="own"),
    _post("/api/client-events/log", USER, handler="_post_api_client_events_log", body="own"),
    _post("/api/prompts", USER, handler="_post_api_prompts"),
    _post("/api/session/new", USER, handler="_post_api_session_new"),
    _post("/api/session/compression-recovery/start", USER, handler="_post_api_session_compression_recovery_start"),
    _post("/api/session/duplicate", USER, handler="_post_api_session_duplicate"),
    _post("/api/default-model", USER, handler="_post_api_default_model"),
    _post("/api/model/set", USER, handler="_post_api_model_set"),
    _post("/api/reasoning", USER, handler="_post_api_reasoning"),
    _post("/api/session/anchor-scene", USER, handler="_post_api_session_anchor_scene"),
    _post("/api/session/rename", USER, handler="_post_api_session_rename"),
    _post("/api/session/title/regenerate", USER, handler="_post_api_session_title_regenerate"),
    _post("/api/personality/set", USER, handler="_post_api_personality_set"),
    _post("/api/session/toolsets", USER, handler="_post_api_session_toolsets"),
    _post("/api/session/draft", USER, handler="_post_api_session_draft"),
    _post("/api/session/update", USER, handler="_post_api_session_update"),
    _post("/api/session/delete", USER, handler="_post_api_session_delete"),
    _post("/api/session/clear", USER, handler="_post_api_session_clear"),
    _post("/api/session/truncate", USER, handler="_post_api_session_truncate"),
    _post("/api/session/branch", USER, handler="_post_api_session_branch"),
    _post("/api/session/compress/start", USER, handler="_post_api_session_compress_start"),
    _post("/api/session/compress", USER, handler="_post_api_session_compress"),
    _post("/api/session/conversation-rounds", USER, handler="_post_api_session_conversation_rounds"),
    _post("/api/session/handoff-summary", USER, handler="_post_api_session_handoff_summary"),
    _post("/api/session/retry", USER, handler="_post_api_session_retry"),
    _post("/api/session/undo", USER, handler="_post_api_session_undo"),
    _post("/api/btw", USER, handler="_post_api_btw"),
    _post("/api/background", USER, handler="_post_api_background"),
    _post("/api/goal", USER, handler="_post_api_goal"),
    _post("/api/bg-task-complete-ack", USER, handler="_post_api_bg_task_complete_ack"),
    _post("/api/chat/start", USER, handler="_post_api_chat_start"),
    _post("/api/chat", USER, handler="_post_api_chat"),
    _post("/api/chat/steer", USER, handler="_post_api_chat_steer"),
    _post("/api/crons/create", USER, handler="_post_api_crons_create"),
    _post("/api/crons/update", USER, handler="_post_api_crons_update"),
    _post("/api/crons/delete", USER, handler="_post_api_crons_delete"),
    _post("/api/crons/run", USER, handler="_post_api_crons_run"),
    _post("/api/crons/pause", USER, handler="_post_api_crons_pause"),
    _post("/api/crons/resume", USER, handler="_post_api_crons_resume"),
    _post("/api/file/delete", USER, handler="_post_api_file_delete"),
    _post("/api/file/save", USER, handler="_post_api_file_save"),
    _post("/api/file/office-save", USER, handler="_post_api_file_office_save"),
    _post("/api/file/create", USER, handler="_post_api_file_create"),
    _post("/api/file/rename", USER, handler="_post_api_file_rename"),
    _post("/api/file/move", USER, handler="_post_api_file_move"),
    _post("/api/file/create-dir", USER, handler="_post_api_file_create_dir"),
    _post("/api/file/path", USER, handler="_post_api_file_path"),
    _post("/api/workspaces/add", USER, handler="_post_api_workspaces_add"),
    _post("/api/workspaces/remove", USER, handler="_post_api_workspaces_remove"),
    _post("/api/workspaces/rename", USER, handler="_post_api_workspaces_rename"),
    _post("/api/workspaces/reorder", USER, handler="_post_api_workspaces_reorder"),
    _post("/api/approval/respond", USER, handler="_post_api_approval_respond"),
    _post("/api/clarify/respond", USER, handler="_post_api_clarify_respond"),
    _post("/api/commands/bundles/resolve", USER, handler="_post_api_commands_bundles_resolve"),
    _post("/api/skills/save", USER, handler="_post_api_skills_save"),
    _post("/api/skills/delete", USER, handler="_post_api_skills_delete"),
    _post("/api/skills/toggle", USER, handler="_post_api_skills_toggle"),
    _post("/api/memory/write", USER, handler="_post_api_memory_write"),
    _post("/api/settings", USER, handler="_post_api_settings"),
    _post("/api/session/pin", USER, handler="_post_api_session_pin"),
    _post("/api/session/archive", USER, handler="_post_api_session_archive"),
    _post("/api/session/move", USER, handler="_post_api_session_move"),
    _post("/api/projects/create", USER, handler="_post_api_projects_create"),
    _post("/api/projects/rename", USER, handler="_post_api_projects_rename"),
    _post("/api/projects/delete", USER, handler="_post_api_projects_delete"),
    _post("/api/session/import", USER, handler="_post_api_session_import"),
    _post("/api/session/import_cli", USER, handler="_post_api_session_import_cli"),
    _post("/api/auth/login", PUBLIC, csrf=False, handler="_post_api_auth_login", session_guard=False),
    _post("/api/auth/logout", USER, handler="_post_api_auth_logout", session_guard=False),
    _post("/api/rollback/restore", USER, handler="_post_api_rollback_restore"),
    # ── PUT ──
    # ── PATCH ──
    # ── DELETE ──
    _delete("/api/prompts", USER, handler="_delete_api_prompts"),
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


def is_public(path: str) -> bool:
    """True if *path* is served before login: its row, under any method, is ``PUBLIC``."""
    return any(route.caller == PUBLIC for route in routes_at(path))


def routes_at(path: str) -> tuple[Route, ...]:
    """The rows *path* is, one per method that has one."""
    found = (match(method, path) for method in METHODS)
    return tuple(route for route in found if route is not None)
