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
  token. Only login (no session yet), the browser's CSP reports and the
  deprecated process-complete ack (answered 410 Gone to a stale tab that has
  no token) are exempt;
- **handler**: the name of the route module's function that serves it. The
  server dispatches every request by looking up its row;
- **session_guard**: whether session ownership checks the session ids the
  request names (``session_id`` in the query or body, the id in the session
  events path) before the handler runs. Pages, static assets and the routes
  served before login do not run it;
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
    handler: str | None = None
    session_guard: bool = True
    body: str = "json"
    names_stream: bool = False

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"{self.pattern}: unknown method {self.method!r}")
        if self.caller not in (USER, ADMIN):
            raise ValueError(f"{self.method} {self.pattern}: say who may call it (USER or ADMIN)")
        if not self.handler:
            raise ValueError(f"{self.method} {self.pattern}: name the route module's function that serves it")
        if self.body not in ("json", "own"):
            raise ValueError(f"{self.method} {self.pattern}: body is 'json' or 'own'")
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
    _get("/session/static/*", USER, handler="_get_session_static", session_guard=False),
    _get("/session/manifest.json", USER, handler="_get_session_manifest", session_guard=False),
    _get("/session/manifest.webmanifest", USER, handler="_get_session_manifest", session_guard=False),
    _get("/", USER, handler="_get_app_shell", session_guard=False),
    _get("/index.html", USER, handler="_get_app_shell", session_guard=False),
    _get("/session/*", USER, session=READ, handler="_get_app_shell", session_guard=False),
    _get("/sessions", USER, handler="_get_app_shell", session_guard=False),
    _get("/share", ADMIN, handler="_get_share_page", session_guard=False),
    _get("/share/*", ADMIN, handler="_get_share_page", session_guard=False),
    _get("/login", ADMIN, handler="_get_login", session_guard=False),
    _get("/api/auth/status", USER, handler="_get_api_auth_status", session_guard=False),
    _get("/api/share/*", ADMIN, handler="_get_api_share", session_guard=False),
    _get("/manifest.json", USER, handler="_get_manifest", session_guard=False),
    _get("/manifest.webmanifest", USER, handler="_get_manifest", session_guard=False),
    _get("/sw.js", USER, handler="_get_sw_js", session_guard=False),
    _get("/favicon.ico", USER, handler="_get_favicon_ico", session_guard=False),
    _get("/api/insights", USER, handler="_get_api_insights"),
    _get("/api/project-os/dashboard", USER, handler="_get_api_project_os_dashboard"),
    _get("/api/kanban/*", ADMIN, handler="_get_api_kanban"),
    _get("/api/wiki/status", USER, handler="_get_api_wiki_status"),
    _get("/api/wiki/browse", USER, handler="_get_api_wiki_browse"),
    _get("/api/wiki/page", USER, handler="_get_api_wiki_page"),
    _get("/api/logs", ADMIN, handler="_get_api_logs"),
    _get("/health", USER, handler="_get_health", session_guard=False),
    _get("/api/health/agent", USER, handler="_get_api_health_agent"),
    _get("/api/system/health", USER, handler="_get_api_system_health"),
    _get("/api/models", USER, handler="_get_api_models"),
    _get("/api/models/live", USER, handler="_get_api_models_live"),
    _get("/api/model/auxiliary", USER, handler="_get_api_model_auxiliary"),
    _get("/api/dashboard/status", ADMIN, handler="_get_api_dashboard_status"),
    _get("/api/dashboard/config", ADMIN, handler="_get_api_dashboard_config"),
    _get("/api/providers", ADMIN, handler="_get_api_providers"),
    _get("/api/plugins", USER, handler="_get_api_plugins"),
    _get("/api/provider/quota", ADMIN, handler="_get_api_provider_quota"),
    _get("/api/provider/cost-history", ADMIN, handler="_get_api_provider_cost_history"),
    _get("/api/settings", USER, handler="_get_api_settings"),
    _get("/api/transcribe/capability", USER, handler="_get_api_transcribe_capability"),
    _get("/api/reasoning", USER, handler="_get_api_reasoning"),
    _get("/api/onboarding/status", ADMIN, handler="_get_api_onboarding_status"),
    _get("/api/extensions/status", ADMIN, handler="_get_api_extensions_status"),
    _get("/extensions/*", ADMIN, handler="_get_extensions", session_guard=False),
    _get("/static/*", USER, handler="_get_static", session_guard=False),
    _get("/api/session/worktree/status", USER, session=READ, handler="_get_api_session_worktree_status"),
    _get("/api/session/compress/status", USER, session=READ, handler="_get_api_session_compress_status"),
    _get("/api/session", USER, session=READ, handler="_get_api_session"),
    _get("/api/session/lineage/report", USER, session=READ, handler="_get_api_session_lineage_report"),
    _get("/api/session/recovery/audit", ADMIN, handler="_get_api_session_recovery_audit"),
    _get("/api/session/status", USER, session=READ, handler="_get_api_session_status"),
    _get("/api/session/yolo", USER, session=READ, handler="_get_api_session_yolo"),
    _get("/api/session/usage", USER, session=READ, handler="_get_api_session_usage"),
    _get("/api/background/status", USER, session=READ, handler="_get_api_background_status"),
    _get("/api/sessions", USER, handler="_get_api_sessions"),
    _get("/api/projects", USER, handler="_get_api_projects"),
    _get("/api/prompts", USER, handler="_get_api_prompts"),
    _get("/api/session/export", USER, session=READ, handler="_get_api_session_export"),
    _get("/api/workspaces", USER, handler="_get_api_workspaces"),
    _get("/api/workspaces/suggest", USER, handler="_get_api_workspaces_suggest"),
    _get("/api/sessions/search", USER, handler="_get_api_sessions_search"),
    _get("/api/list", USER, session=READ, handler="_get_api_list"),
    _get("/api/escape/list", ADMIN, session=READ, handler="_get_api_escape_list"),
    _get("/api/git/status", USER, session=READ, handler="_get_api_git_status"),
    _get("/api/git/branches", USER, session=READ, handler="_get_api_git_branches"),
    _get("/api/git/diff", USER, session=READ, handler="_get_api_git_diff"),
    _get("/api/personalities", USER, handler="_get_api_personalities"),
    _get("/api/git-info", USER, session=READ, handler="_get_api_git_info"),
    _get("/api/commands", USER, handler="_get_api_commands"),
    _get("/api/commands/bundles", USER, handler="_get_api_commands_bundles"),
    _get("/api/commands/moa/resolve", USER, handler="_get_api_commands_moa_resolve"),
    _get("/api/chat/stream/status", USER, session=READ, handler="_get_api_chat_stream_status", names_stream=True),
    _get("/api/chat/cancel", USER, session=WRITE, handler="_get_api_chat_cancel", names_stream=True),
    _get("/api/chat/stream", USER, session=READ, handler="_get_api_chat_stream", names_stream=True),
    _get("/api/terminal/output", ADMIN, session=READ, handler="_get_api_terminal_output"),
    _get("/api/sessions/gateway/stream", USER, handler="_get_api_sessions_gateway_stream"),
    _get("/api/sessions/events", USER, handler="_get_api_sessions_events"),
    _get("/api/media", USER, handler="_get_api_media"),
    _get("/api/file/raw", USER, session=READ, handler="_get_api_file_raw"),
    _get("/api/escape/file/raw", ADMIN, session=READ, handler="_get_api_escape_file_raw"),
    _get("/api/folder/download", USER, session=READ, handler="_get_api_folder_download"),
    _get("/api/file", USER, session=READ, handler="_get_api_file"),
    _get("/api/escape/file/read", ADMIN, session=READ, handler="_get_api_escape_file_read"),
    _get("/api/approval/pending", USER, session=READ, handler="_get_api_approval_pending"),
    _get("/api/approval/stream", USER, session=READ, handler="_get_api_approval_stream"),
    _get("/api/approval/inject_test", ADMIN, session=WRITE, handler="_get_api_approval_inject_test"),
    _get("/api/clarify/pending", USER, session=READ, handler="_get_api_clarify_pending"),
    _get("/api/clarify/stream", USER, session=READ, handler="_get_api_clarify_stream"),
    _get("/api/session/stream", USER, session=READ, handler="_get_api_session_stream"),
    _get("/api/clarify/inject_test", ADMIN, session=WRITE, handler="_get_api_clarify_inject_test"),
    _get("/api/onboarding/oauth/poll", ADMIN, handler="_get_api_onboarding_oauth_poll"),
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
    _get("/api/mcp/servers", ADMIN, handler="_get_api_mcp_servers"),
    _get("/api/mcp/tools", ADMIN, handler="_get_api_mcp_tools"),
    _get("/api/notes/sources", USER, handler="_get_api_notes_sources"),
    _get("/api/notes/search", USER, handler="_get_api_notes_search"),
    _get("/api/notes/item", USER, handler="_get_api_notes_item"),
    _get("/api/rollback/list", USER, handler="_get_api_rollback_list"),
    _get("/api/rollback/diff", USER, handler="_get_api_rollback_diff"),
    _get("/plugins/*", USER, handler="_get_plugins", session_guard=False),
    _get("/dashboard-plugins/*", USER, handler="_get_dashboard_plugins", session_guard=False),
    _get("/api/sessions/<id>/events", USER, session=READ, handler="_get_session_events"),
    # ── POST ──
    _post("/api/csp-report", ADMIN, csrf=False, handler="_post_api_csp_report", body="own"),
    _post("/api/process-complete-ack", USER, handler="_post_api_process_complete_ack", body="own", csrf=False),
    _post("/api/shutdown", ADMIN, handler="_post_api_shutdown", body="own"),
    _post("/api/health/restart", ADMIN, handler="_post_api_health_restart", body="own"),
    _post("/api/upload", USER, session=WRITE, handler="_post_api_upload", body="own"),
    _post("/api/upload/extract", USER, session=WRITE, handler="_post_api_upload_extract", body="own"),
    _post("/api/workspace/upload", USER, session=WRITE, handler="_post_api_workspace_upload", body="own"),
    _post("/api/transcribe", USER, handler="_post_api_transcribe", body="own"),
    _post("/api/tts", USER, handler="_post_api_tts", body="own"),
    _post("/api/client-events/log", USER, handler="_post_api_client_events_log", body="own"),
    _post("/api/escape/authorize", ADMIN, session=WRITE, handler="_post_api_escape_authorize"),
    _post("/api/extensions/toggle", ADMIN, handler="_post_api_extensions_toggle"),
    _post("/api/extensions/sidecar-proxy-consent", ADMIN, handler="_post_api_extensions_sidecar_proxy_consent"),
    _post("/api/session/recovery/repair-safe", ADMIN, handler="_post_api_session_recovery_repair_safe"),
    _post("/api/kanban/*", ADMIN, handler="_post_api_kanban"),
    _post("/api/dashboard/config", ADMIN, handler="_post_api_dashboard_config"),
    _post("/api/prompts", USER, handler="_post_api_prompts"),
    _post("/api/share/create", ADMIN, session=WRITE, handler="_post_api_share_create"),
    _post("/api/share/revoke", ADMIN, session=WRITE, handler="_post_api_share_revoke"),
    _post("/api/session/new", USER, session=WRITE, handler="_post_api_session_new"),
    _post("/api/session/compression-recovery/start", USER, session=WRITE, handler="_post_api_session_compression_recovery_start"),
    _post("/api/session/duplicate", USER, session=WRITE, handler="_post_api_session_duplicate"),
    _post("/api/default-model", USER, handler="_post_api_default_model"),
    _post("/api/model/set", USER, handler="_post_api_model_set"),
    _post("/api/providers", ADMIN, handler="_post_api_providers"),
    _post("/api/providers/delete", ADMIN, handler="_post_api_providers_delete"),
    _post("/api/providers/self-hosted", ADMIN, handler="_post_api_providers_self_hosted"),
    _post("/api/models/refresh", ADMIN, handler="_post_api_models_refresh"),
    _post("/api/reasoning", USER, handler="_post_api_reasoning"),
    _post("/api/admin/reload", ADMIN, handler="_post_api_admin_reload"),
    _post("/api/sessions/cleanup", ADMIN, handler="_post_api_sessions_cleanup"),
    _post("/api/sessions/cleanup_zero_message", ADMIN, handler="_post_api_sessions_cleanup_zero_message"),
    _post("/api/session/anchor-scene", USER, session=WRITE, handler="_post_api_session_anchor_scene"),
    _post("/api/session/rename", USER, session=WRITE, handler="_post_api_session_rename"),
    _post("/api/session/title/regenerate", USER, session=WRITE, handler="_post_api_session_title_regenerate"),
    _post("/api/personality/set", USER, session=WRITE, handler="_post_api_personality_set"),
    _post("/api/session/toolsets", USER, session=WRITE, handler="_post_api_session_toolsets"),
    _post("/api/session/draft", USER, session=WRITE, handler="_post_api_session_draft"),
    _post("/api/session/update", USER, session=WRITE, handler="_post_api_session_update"),
    _post("/api/session/worktree/remove", ADMIN, session=WRITE, handler="_post_api_session_worktree_remove"),
    _post("/api/session/delete", USER, session=WRITE, handler="_post_api_session_delete"),
    _post("/api/session/clear", USER, session=WRITE, handler="_post_api_session_clear"),
    _post("/api/session/truncate", USER, session=WRITE, handler="_post_api_session_truncate"),
    _post("/api/session/branch", USER, session=WRITE, handler="_post_api_session_branch"),
    _post("/api/session/compress/start", USER, session=WRITE, handler="_post_api_session_compress_start"),
    _post("/api/session/compress", USER, session=WRITE, handler="_post_api_session_compress"),
    _post("/api/session/conversation-rounds", USER, session=READ, handler="_post_api_session_conversation_rounds"),
    _post("/api/session/handoff-summary", USER, session=WRITE, handler="_post_api_session_handoff_summary"),
    _post("/api/session/retry", USER, session=WRITE, handler="_post_api_session_retry"),
    _post("/api/session/undo", USER, session=WRITE, handler="_post_api_session_undo"),
    _post("/api/session/yolo", ADMIN, session=WRITE, handler="_post_api_session_yolo"),
    _post("/api/btw", USER, session=WRITE, handler="_post_api_btw"),
    _post("/api/background", USER, session=WRITE, handler="_post_api_background"),
    _post("/api/goal", USER, session=WRITE, handler="_post_api_goal"),
    _post("/api/bg-task-complete-ack", USER, session=WRITE, handler="_post_api_bg_task_complete_ack"),
    _post("/api/chat/start", USER, session=WRITE, handler="_post_api_chat_start"),
    _post("/api/chat", USER, session=WRITE, handler="_post_api_chat"),
    _post("/api/chat/steer", USER, session=WRITE, handler="_post_api_chat_steer"),
    _post("/api/terminal/start", ADMIN, session=WRITE, handler="_post_api_terminal_start"),
    _post("/api/terminal/input", ADMIN, session=WRITE, handler="_post_api_terminal_input"),
    _post("/api/terminal/resize", ADMIN, session=WRITE, handler="_post_api_terminal_resize"),
    _post("/api/terminal/close", ADMIN, session=WRITE, handler="_post_api_terminal_close"),
    _post("/api/crons/create", USER, handler="_post_api_crons_create"),
    _post("/api/crons/update", USER, handler="_post_api_crons_update"),
    _post("/api/crons/delete", USER, handler="_post_api_crons_delete"),
    _post("/api/crons/run", USER, handler="_post_api_crons_run"),
    _post("/api/crons/pause", USER, handler="_post_api_crons_pause"),
    _post("/api/crons/resume", USER, handler="_post_api_crons_resume"),
    _post("/api/git/stage", ADMIN, session=WRITE, handler="_post_api_git_stage"),
    _post("/api/git/unstage", ADMIN, session=WRITE, handler="_post_api_git_unstage"),
    _post("/api/git/discard", ADMIN, session=WRITE, handler="_post_api_git_discard"),
    _post("/api/git/commit-message", ADMIN, session=WRITE, handler="_post_api_git_commit_message"),
    _post("/api/git/commit-message-selected", ADMIN, session=WRITE, handler="_post_api_git_commit_message_selected"),
    _post("/api/git/commit", ADMIN, session=WRITE, handler="_post_api_git_commit"),
    _post("/api/git/commit-selected", ADMIN, session=WRITE, handler="_post_api_git_commit_selected"),
    _post("/api/git/fetch", ADMIN, session=WRITE, handler="_post_api_git_fetch"),
    _post("/api/git/pull", ADMIN, session=WRITE, handler="_post_api_git_pull"),
    _post("/api/git/push", ADMIN, session=WRITE, handler="_post_api_git_push"),
    _post("/api/git/checkout", ADMIN, session=WRITE, handler="_post_api_git_checkout"),
    _post("/api/git/stash-checkout", ADMIN, session=WRITE, handler="_post_api_git_stash_checkout"),
    _post("/api/file/delete", USER, session=WRITE, handler="_post_api_file_delete"),
    _post("/api/file/save", USER, session=WRITE, handler="_post_api_file_save"),
    _post("/api/file/office-save", USER, session=WRITE, handler="_post_api_file_office_save"),
    _post("/api/file/create", USER, session=WRITE, handler="_post_api_file_create"),
    _post("/api/file/rename", USER, session=WRITE, handler="_post_api_file_rename"),
    _post("/api/file/move", USER, session=WRITE, handler="_post_api_file_move"),
    _post("/api/file/create-dir", USER, session=WRITE, handler="_post_api_file_create_dir"),
    _post("/api/file/reveal", ADMIN, session=WRITE, handler="_post_api_file_reveal"),
    _post("/api/file/path", USER, session=READ, handler="_post_api_file_path"),
    _post("/api/file/open-vscode", ADMIN, session=WRITE, handler="_post_api_file_open_vscode"),
    _post("/api/workspaces/add", USER, handler="_post_api_workspaces_add"),
    _post("/api/workspaces/remove", USER, handler="_post_api_workspaces_remove"),
    _post("/api/workspaces/rename", USER, handler="_post_api_workspaces_rename"),
    _post("/api/workspaces/reorder", USER, handler="_post_api_workspaces_reorder"),
    _post("/api/approval/respond", USER, session=WRITE, handler="_post_api_approval_respond"),
    _post("/api/clarify/respond", USER, session=WRITE, handler="_post_api_clarify_respond"),
    _post("/api/commands/bundles/resolve", USER, handler="_post_api_commands_bundles_resolve"),
    _post("/api/commands/exec", ADMIN, handler="_post_api_commands_exec"),
    _post("/api/skills/save", USER, handler="_post_api_skills_save"),
    _post("/api/skills/delete", USER, handler="_post_api_skills_delete"),
    _post("/api/skills/toggle", USER, handler="_post_api_skills_toggle"),
    _post("/api/memory/write", USER, handler="_post_api_memory_write"),
    _post("/api/gateway/restart", ADMIN, handler="_post_api_gateway_control"),
    _post("/api/gateway/start", ADMIN, handler="_post_api_gateway_control"),
    _post("/api/gateway/stop", ADMIN, handler="_post_api_gateway_control"),
    _post("/api/profile/switch", ADMIN, handler="_post_api_profile_switch"),
    _post("/api/profile/create", ADMIN, handler="_post_api_profile_create"),
    _post("/api/profile/disable", ADMIN, handler="_post_api_profile_enable_or_disable"),
    _post("/api/profile/enable", ADMIN, handler="_post_api_profile_enable_or_disable"),
    _post("/api/profile/delete", ADMIN, handler="_post_api_profile_delete"),
    _post("/api/settings", USER, handler="_post_api_settings"),
    _post("/api/onboarding/oauth/start", ADMIN, handler="_post_api_onboarding_oauth_start"),
    _post("/api/onboarding/oauth/cancel", ADMIN, handler="_post_api_onboarding_oauth_cancel"),
    _post("/api/onboarding/setup", ADMIN, handler="_post_api_onboarding_setup"),
    _post("/api/onboarding/complete", ADMIN, handler="_post_api_onboarding_complete"),
    _post("/api/onboarding/probe", ADMIN, handler="_post_api_onboarding_probe"),
    _post("/api/session/pin", USER, session=WRITE, handler="_post_api_session_pin"),
    _post("/api/session/archive", USER, session=WRITE, handler="_post_api_session_archive"),
    _post("/api/session/move", USER, session=WRITE, handler="_post_api_session_move"),
    _post("/api/projects/create", USER, handler="_post_api_projects_create"),
    _post("/api/projects/rename", USER, handler="_post_api_projects_rename"),
    _post("/api/projects/delete", USER, handler="_post_api_projects_delete"),
    _post("/api/session/import", USER, handler="_post_api_session_import"),
    _post("/api/session/import_cli", USER, session=WRITE, handler="_post_api_session_import_cli"),
    _post("/api/auth/login", USER, csrf=False, handler="_post_api_auth_login"),
    _post("/api/auth/logout", USER, handler="_post_api_auth_logout"),
    _post("/api/rollback/restore", USER, handler="_post_api_rollback_restore"),
    # ── PUT ──
    _put("/api/mcp/servers/*", ADMIN, handler="_put_api_mcp_servers"),
    # ── PATCH ──
    _patch("/api/mcp/servers/*", ADMIN, handler="_patch_api_mcp_servers"),
    _patch("/api/kanban/*", ADMIN, handler="_patch_api_kanban"),
    # ── DELETE ──
    _delete("/api/mcp/servers/*", ADMIN, handler="_delete_api_mcp_servers"),
    _delete("/api/prompts", USER, handler="_delete_api_prompts"),
    _delete("/api/kanban/*", ADMIN, handler="_delete_api_kanban"),
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
