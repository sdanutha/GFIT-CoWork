# Hermes Web UI: Developer and Architecture Guide

> This document is the canonical reference for anyone (human or agent) working on the
> Hermes Web UI. It covers the exact current state of the code, every design decision and
> quirk discovered during development.
>
> Keep this document updated as architecture changes are made.

> Current shipped build: `v0.51.792` (July 1, 2026).
> Automated coverage: ~11,500 tests via `pytest tests/ --collect-only -q`. CI runs on
> Python 3.11, 3.12, and 3.13 (3 parallel shards each) against every PR, plus a ruff
> lint gate, a headless browser smoke test, and a Docker smoke test.
>
> Notable architecture state: the bootstrap and first-run onboarding flow own setup discovery; the default WebUI state directory is `~/.hermes/webui`; `ctl.sh` provides a daemon wrapper for homelab installs; chat streaming is still WebUI-owned SSE with stream-ownership guards, cancellation, async manual compression, and turn-journal audit plumbing; provider/model discovery is profile-aware with live-model cache invalidation and custom-provider scoping. (Version/test-count numbers above are a periodic snapshot — the authoritative source is the latest git tag and `pytest --collect-only`.)

---

## 1. Overview and Purpose

The Hermes Web UI is a lightweight web application that gives you a browser-based
interface to the Hermes agent that is functionally equivalent to the CLI. It is modeled on
the Claude-style interface: a sidebar for session management, a central chat area,
and a demand-driven right panel used for workspace browsing and preview surfaces.
The right panel is closed by default on desktop and opens only when it is actively
being used for browsing or previewing content.

To prevent a visible first-paint mismatch on refresh, `static/index.html` preloads the
saved workspace panel state into `document.documentElement.dataset.workspacePanel`
before the main stylesheet loads. Desktop CSS honors that preload marker immediately,
and `static/boot.js` keeps the dataset synchronized with the runtime panel state machine.

The design philosophy is deliberately minimal. There is no build step, no bundler, no
frontend framework. The Python server is split into a routing shell (server.py) and
business logic modules (api/). The frontend is seven vanilla JS modules loaded from static/.
This makes the code easy to modify from a terminal or by an agent.

Hermes-level chrome is intentionally consolidated: the sidebar has no dedicated brand header.
Instead, the footer exposes a single "Hermes WebUI" launch button that opens one tabbed
control-center modal for global preferences, conversation import/export, and clear-conversation
actions. The topbar remains focused on conversation context and the workspace/files toggle.

---

## 2. File Inventory

    <repo>/
    server.py              Thin routing shell + HTTP Handler + auth middleware.
                           Delegates all route handling to api/routes.py.
    bootstrap.py           One-shot launcher: optional agent install, deps, health wait, browser open.
    start.sh               Thin wrapper around bootstrap.py for shell-based startup.
    ctl.sh                 Daemon lifecycle wrapper (start/stop/restart/status/logs) for homelab installs.
    pyproject.toml         Standard build metadata plus the Ruff lint gate; source-checkout launch surface still centers on bootstrap.py / start.sh / ctl.sh.
    Dockerfile             python:3.12-slim container image
    deploy/                Deployment kit: one Team's Compose file, config example, Caddy proxy
    .dockerignore          Excludes .git, tests/, .env* from Docker builds
    api/
      __init__.py          Package marker
      agent_compat.py      Resolver for Hermes Agent names moved to sibling modules (compatibility-only)
      auth.py              Session store and cookie, CSRF, signed Profile cookie, per-request gate
      login.py             Directory login, rate limit, startup login check
      trusted_proxy.py     Client address behind a trusted reverse proxy
      config.py            Discovery, globals, model detection, reloadable config
      helpers.py           HTTP helpers: j(), bad(), require(), resolve_inside(), security headers
      goals.py             Persistent-goal commands and profile-scoped native GoalManager bridge
      models.py            Session model + CRUD, per-session profile tracking, CLI/state.db bridge
      profiles.py          Profile state management, hermes_cli wrapper
      onboarding.py        First-run onboarding status, real provider config writes, OAuth linking, readiness detection
      routes.py            All GET + POST route handlers (if/elif dispatch, no decorators)
      startup.py           Startup helpers: auto_install_agent_deps()
      state_sync.py        /insights sync — message_count to the agent's state.db
      streaming.py         SSE engine, run_agent, cancel, compression, HERMES_HOME save/restore
      version.py           Running GFIT-CoWork and Hermes Agent versions
      upload.py            Multipart parser, file upload handler
      workspace.py         File ops: list_dir, read_file_content, git detection, workspace helpers
    static/
      index.html           HTML template
      style.css            All CSS incl. mobile responsive, themes + skins, KaTeX
      ui.js                DOM helpers, renderMd, tool cards, context indicator, file tree
      workspace.js         File preview, file ops, git badge, central api() fetch wrapper
      sessions.js          Session CRUD, list rendering, collapsible groups, search, SSE sync
      messages.js          send(), SSE event handlers, approval/clarify, transcript, recovery
      panels.js            Cron, skills, memory, profiles, todo, settings (Control Center)
      commands.js          Slash command registry, parser, autocomplete dropdown
      boot.js              Event wiring, mobile nav, voice input, theme/skin boot, bfcache handler
      onboarding.js        First-run wizard overlay, provider setup flow
      i18n.js              Localization catalog (en, es, de, zh, zh-Hant, ru, …)
      login.js             Login page + open-redirect guard
      icons.js             Lucide icon path registry
      sw.js                Service worker: offline shell cache, version-pinned assets
    tests/
      conftest.py          Isolated test server/state fixtures
      ~1,150 test files    ~11,500 tests collected via pytest (run `pytest --collect-only -q` for exact)
      test_regressions.py  Permanent regression gate
    CONTRIBUTING.md        Contributor workflow and PR expectations.
    ARCHITECTURE.md        THIS FILE.
    TESTING.md             Manual browser test plan and automated coverage reference.
    CHANGELOG.md           Release notes per version.
    requirements.txt       Python dependencies.
    .env.example           Sample environment variable overrides.

> Per-file line counts intentionally omitted — they drift every release. Use
> `git ls-files | xargs wc -l` (or your editor) for current sizes; the role of
> each file above is the durable part.

State directory (runtime data, separate from source):

    ~/.hermes/webui/
    sessions/          One JSON file per session: {session_id}.json
    workspaces.json    Registered workspaces list
    last_workspace.txt Last-used workspace path
    settings.json      User settings (default model, workspace, send key)
    projects.json      Session project groups (name, color, id)

Log file:

    ~/.hermes/webui/bootstrap-8787.log   start.sh/bootstrap background server log
    ~/.hermes/webui.log                  ctl.sh daemon log

---

## 3. Runtime Environment

- Python interpreter: <agent-dir>/venv/bin/python
- The venv has all Hermes agent dependencies (run_agent, tools/*, cron/*)
- Server binds to 127.0.0.1:8787 (localhost only, not public internet)
- Access from Mac: SSH tunnel: ssh -N -L 8787:127.0.0.1:8787 <user>@<your-server>
- The server imports Hermes modules via sys.path.insert(0, parent_dir)

Environment variables controlling behavior:

    HERMES_WEBUI_HOST              Bind address (default: 127.0.0.1)
    HERMES_WEBUI_PORT              Port (default: 8787)
    HERMES_WEBUI_DEFAULT_WORKSPACE Default workspace path for new sessions
    HERMES_WEBUI_STATE_DIR         Where sessions/ folder lives
    HERMES_CONFIG_PATH             Path to ~/.hermes/config.yaml
    HERMES_WEBUI_DEFAULT_MODEL     Optional model override; unset means provider default
    HERMES_WEBUI_DIRECTORY         Login: the Directory (ldap or memory); unset = login off (loopback only)
    HERMES_WEBUI_SKIP_ONBOARDING   Optional: bypass the first-run onboarding wizard
    HERMES_PREFILL_MESSAGES_FILE   Optional JSON message list for browser-turn prefill context
    HERMES_WEBUI_PREFILL_MESSAGES_SCRIPT Optional command that prints JSON messages or plain-text user prefill context
    HERMES_WEBUI_PREFILL_MESSAGES_SCRIPT_TIMEOUT Optional script timeout in seconds (default 5, max 30)
    HERMES_WEBUI_PREFILL_CONTEXT_MAX_CHARS Optional parsed prefill budget in characters (default 12000, 0 disables)
    HERMES_HOME                    Base directory for Hermes state (~/.hermes by default)

Test isolation environment variables (set by conftest.py):

    HERMES_WEBUI_TEST_PORT=...                         Optional pinned test port
    HERMES_WEBUI_TEST_STATE_DIR=/tmp/hermes-webui-tests/* Optional pinned test state (default: OS temp dir; must be outside ~/.hermes)
    HERMES_WEBUI_DEFAULT_WORKSPACE=.../test-workspace  Isolated test workspace

Tests NEVER talk to the production server (port 8787).
The test state dir is wiped before each test session and deleted after.
See: <repo>/tests/conftest.py

Per-request environment variables (set by chat handler, restored after):

    TERMINAL_CWD         Set to session.workspace before running agent.
                         The terminal tool reads this to default cwd.
    HERMES_EXEC_ASK      Set to "1" to enable approval gate for dangerous commands.
    HERMES_SESSION_KEY   Set to session_id. The approval tool keys pending entries
                         by this value, enabling per-session approval state.
    HERMES_HOME          Set to the active profile's directory before running agent.
                         Saved and restored around each agent run.

WARNING: These env vars are process-global. Two concurrent chat requests will clobber
each other. This is safe only for single-user, single-concurrent-request use.
See Architecture Phase B for the fix.

---

## 4. Server Architecture: Current State

### 4.1 HTTP Server Layer

Python stdlib ThreadingHTTPServer (from http.server). Each HTTP request runs in its own
thread. The Handler class subclasses BaseHTTPRequestHandler with two methods:

    do_GET    Routes: /, /health, /api/session, /api/sessions, /api/list,
                      /api/chat/stream, /api/file, /api/approval/pending,
                      /api/session/worktree/status
    do_POST   Routes: /api/upload, /api/session/new, /api/session/update,
                      /api/session/delete, /api/chat/start, /api/chat,
                      /api/approval/respond, /api/session/worktree/remove

Routing is a flat if/elif chain inside each method. No routing framework.

Helper functions used by all handlers:

    j(handler, payload, status=200)     Sends JSON response with correct headers
    t(handler, payload, status=200, ct) Sends plain text or HTML response
    read_body(handler)                  Reads and JSON-parses the POST body

CRITICAL ORDERING RULE in do_POST:
The /api/upload check MUST appear BEFORE calling read_body(). read_body() calls
handler.rfile.read() which consumes the HTTP body stream. The upload handler also
needs rfile (to read the multipart payload). If read_body() runs first on a multipart
request, the upload handler receives an empty body and the upload silently fails.

### 4.2 Session Model

Session is a plain Python class (not a dataclass, not SQLAlchemy):

    Fields:
      session_id    hex string, 12 chars (uuid4().hex[:12])
      title         string, auto-set from first user message
      workspace     absolute path string, resolved at creation
      model         model ID string (e.g. "anthropic/claude-sonnet-4.6")
      messages      list of OpenAI-format message dicts
      created_at    float Unix timestamp
      updated_at    float Unix timestamp, updated on every save()
      pinned        bool, default False (Sprint 12)
      archived      bool, default False (Sprint 14)
      project_id    string or null, FK to projects.json (Sprint 15)
      tool_calls    list of tool call dicts (Sprint 10)

    Key methods:
      path (property)  Returns SESSION_DIR/{session_id}.json
      save()           Writes __dict__ as pretty JSON to path, updates updated_at
      load(cls, sid)   Class method: reads JSON from disk, returns Session or None
      compact()        Returns metadata-only dict (no messages) for the session list

    In-memory cache:
      SESSIONS = {}    dict: session_id -> Session object
      LOCK = threading.Lock()   defined but NOT currently used around SESSIONS access

    get_session(sid): checks SESSIONS cache, loads from disk on miss, raises KeyError
    new_session(workspace, model): creates Session, caches in SESSIONS, saves, returns
    all_sessions(): scans SESSION_DIR/*.json + SESSIONS, deduplicates, sorts by updated_at,
                    returns list of compact() dicts

    all_sessions() does a full directory scan on every call.
    With 10 sessions: negligible. With 1000+: will be slow.
    See Architecture Phase C for the index file fix.

title_from(): takes messages list, finds first user message, returns first 64 chars.
Called after run_conversation() completes to set the session title retroactively.

#### Session transcript reconciliation with `state.db`

`reconciled_state_db_messages_for_session()` uses
`merge_session_messages_append_only()` to combine a WebUI sidecar or context
projection with active Agent `state.db` rows. Its explicit
`incoming_provenance="state_db"` fence permits a state-only row whose timestamp
predates the sidecar tail to use a safe chronological slot. Paginated
`msg_limit` consumers rely on this merged order directly rather than applying a
later timestamp sort.

The same merge helper also stitches ordered child sidecars onto archived
compression parents. Those calls leave incoming provenance unverified, so their
stable message sequence remains append-only even when parent rows carry later,
restamped timestamps. Timestamp alone is never authority to move a continuation
inside its parent transcript.

If an older row could only be placed before the first surviving sidecar/context
row, the insertion helper declines it to avoid resurrecting compacted history.
Reconciliation then appends that row instead of dropping it; rows without a
usable timestamp and rows at or after the sidecar tail also append normally.
The fallback therefore preserves an accepted state-only row when exact ordering
is ambiguous, while safely placeable recovery rows remain chronological.

#### Imported `state.db` sidebar projection

`api.models.get_cli_sessions()` projects conversations from the active Hermes
profile's `state.db` into sidebar-shaped rows. The default projection keeps
interactive sources (CLI, TUI, ACP, messaging, and similar user-facing sessions)
in a bounded 20-row candidate window. Background sources use independent recovery
passes so a high-volume worker source cannot consume that interactive window:

- Cron: up to `CRON_PROJECT_CHIP_LIMIT` rows.
- Webhook: up to `WEBHOOK_PROJECT_CHIP_LIMIT` rows.
- Kanban: up to `KANBAN_PROJECT_CHIP_LIMIT` rows.

Source-specific views still use their dedicated bounds, and the later sidebar
visibility stage decides whether recovered background rows are shown. In
`all_profiles=True` mode the per-profile source bounds are disabled before rows
are merged; cross-profile scoping, visibility, deduplication, and final route
limits remain downstream responsibilities.

#### Compression lineage and session-list invalidation

`api.agent_sessions._is_continuation_session()` is the shared classifier for
sidebar projection, lineage metadata/reporting, and `state.db` transcript
stitching. It uses the direct parent link, no conflicting non-empty source,
`compression` or `cli_close` parent end reason, and the existing two-second
`started_at` overlap allowance. A `source="tool"` child is always a separate
conversation, even when its parent is also a tool session or has no source.
A direct `_branched_from`, `_delegate_from`, or `_reset_from` marker in the
child's `model_config` also makes a boundary; inherited ancestor markers do
not. Malformed or unverifiable marker evidence fails closed as a boundary.
The overlap allowance is the existing master policy, not a new window set by
this change.

The gateway watcher's cheap database fingerprint includes `model_config`, so
a marker-only update causes a fresh projection. Its published-payload hash
covers every emitted session field, not just ID, activity time and message
count; a changed projected title or lineage field can therefore emit
`sessions_changed` even without message-row churn.

### 4.3 SSE Streaming Engine

This is the most architecturally interesting part. Two endpoints cooperate:

    POST /api/chat/start     Receives the user message. Creates a queue.Queue, stores it
                             in STREAMS[stream_id], spawns a daemon thread running
                             _run_agent_streaming(), returns {stream_id} immediately.

    GET  /api/chat/stream    Long-lived SSE connection. Reads from STREAMS[stream_id]
                             and forwards events to the browser until 'done' or 'error'.

Queue registry:

    STREAMS = {}               dict: stream_id -> queue.Queue
    STREAMS_LOCK = threading.Lock()

SSE event types and their data shapes:

    token       {"text": "..."}                         LLM token delta
    tool        {"name": "...", "preview": "..."}       Tool invocation started
    approval    {"command": "...", "description": "...", "pattern_keys": [...]}
    done        {"session": {compact_fields + messages}} Agent finished successfully
    error       {"message": "...", "trace": "..."}       Agent threw exception

The SSE handler loop:
    - Blocks on queue.get(timeout=30)
    - On timeout (no events in 30s): sends a heartbeat comment (": heartbeat

")
      to keep the connection alive through proxies and firewalls
    - On 'done' or 'error' event: breaks the loop and returns
    - Catches BrokenPipeError and ConnectionResetError silently (browser disconnected)

Stream cleanup: _run_agent_streaming() pops its stream_id from STREAMS in a finally
block. If the browser disconnects mid-stream, the daemon thread runs to completion and
then cleans up. The queue fills and the put_nowait() calls fail silently (queue.Full
is caught).

Fallback sync endpoint: POST /api/chat still exists and holds the connection open until
the agent finishes. The frontend never uses it but it can be useful for debugging.

### 4.4 Agent Invocation (_run_agent_streaming)

    def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id):

1. Fetches session from SESSIONS (not from disk -- session was just updated by /api/chat/start)
2. Sets TERMINAL_CWD, HERMES_EXEC_ASK, HERMES_SESSION_KEY env vars
3. Creates AIAgent with:
   - model=model, platform='cli', quiet_mode=True
   - enabled_toolsets=CLI_TOOLSETS (from config.yaml or hardcoded default)
   - session_id=session_id
   - stream_delta_callback=on_token (fires per token)
   - tool_progress_callback=on_tool (fires per tool invocation)
4. Calls agent.run_conversation(user_message=msg_text, conversation_history=s.messages,
                                 task_id=session_id)
   NOTE: keyword is task_id NOT session_id (common mistake, documented in skill)
5. On return: updates s.messages, calls title_from(), saves session
6. Puts ('done', {session: ...}) into queue
7. Finally block: restores env vars, pops stream_id from STREAMS

on_token callback:
    if text is None: return  # end-of-stream sentinel from AIAgent
    put('token', {'text': text})

on_tool callback:
    put('tool', {'name': name, 'preview': preview})
    # Also immediately surface any pending approval:
    if has_pending(session_id):
        with _lock: p = dict(_pending.get(session_id, {}))
        if p: put('approval', p)

The approval surface-on-tool logic means approvals appear immediately after the tool
fires (within the same SSE stream), without waiting for the next poll cycle.

### 4.5 Approval System Integration

The approval system uses the existing Hermes gateway module at tools/approval.py.
All state lives in module-level variables in that file:

    _pending = {}        dict: session_key -> pending_entry_dict
    _lock = Lock()       protects _pending
    _permanent_approved  set of permanently approved pattern keys

Because server.py imports tools.approval at module load time and everything runs in the
same process, this state IS shared between HTTP threads and agent daemon threads.

Important: this only works because Python imports are cached (sys.modules). The same
module object is used everywhere. If the approval module were ever imported in a subprocess
or via importlib.reload(), this would break.

GET /api/approval/pending:
    - Peeks at _pending[sid] without removing it
    - Returns {pending: entry} or {pending: null}
    - Called by the browser every 1500ms while S.busy is true (polling fallback)

POST /api/approval/respond:
    - Pops _pending[sid] (removes it)
    - For choice "once" or "session": calls approve_session(sid, pattern_key) for each key
    - For choice "always": calls approve_session + approve_permanent + save_permanent_allowlist
    - For choice "deny": just pops, does nothing (agent gets denied result)
    - Returns {ok: true, choice: choice}

### 4.6 File Upload Parser

parse_multipart(rfile, content_type, content_length):
    - Reads all content_length bytes from rfile into memory (up to MAX_UPLOAD_BYTES, default 20MB, env-overridable via HERMES_WEBUI_MAX_UPLOAD_MB)
    - Extracts boundary from Content-Type header
    - Splits raw bytes on b'--' + boundary
    - For each part: parses MIME headers via email.parser.HeaderParser
    - Returns (fields, files) where fields is {name: value} and files is {name: (filename, bytes)}

handle_upload(handler):
    - Calls parse_multipart()
    - Validates: file field present, filename present, session exists
    - Sanitizes filename: replaces non-word chars with _, truncates to 200 chars
    - Writes bytes to session.workspace / safe_name
    - Returns {filename, path, size}

Why not cgi.FieldStorage:
    - Deprecated in Python 3.11+
    - Broken for binary files (silently corrupts or throws)
    - The manual parser handles all file types correctly

### 4.7 File System Operations

resolve_in_workspace(root, requested):
    - Resolves requested path relative to root (helpers.resolve_inside, the unconfined primitive)
    - Calls .relative_to(root) to assert the result is inside root
    - Raises ValueError on path traversal (../../etc/passwd)
    - Then the request's Workspace policy confines it (a User: inside their Workspace)

list_dir(workspace, rel='.'):
    - Calls resolve_in_workspace, then iterdir()
    - Sorts: directories first, then files, case-insensitive alpha within each group
    - Returns up to 200 entries with {name, path, type, size}

read_file_content(workspace, rel):
    - Calls resolve_in_workspace
    - Enforces MAX_FILE_BYTES = 200KB size limit
    - Reads as UTF-8 with errors='replace' (binary files show replacement chars)
    - Returns {path, content, size, lines}

### 4.8 Persistent Goal Profile Boundary

`api/goals.py` exposes the WebUI `/goal` command payloads and post-turn evaluation hook.
Hermes Agent's native `GoalManager` is the authoritative owner of goal evaluation,
continuation decisions, wait barriers, failure counters, contracts, subgoals, and
`state.db` persistence.

For a profile-scoped WebUI session, the bridge delegates only when the Agent exposes
both the context-local `set_hermes_home_override()` API and call-time default
`SessionDB` path resolution. The bridge probes the resolved default path under the
selected context before constructing the native manager, then binds that profile's
Hermes home before every native call. The override is reset in a `finally` block after
every operation, so concurrent sessions using the same session ID under different
profiles cannot cross-read or cross-write goal state. Goal snapshot rollback uses the
same scoped native persistence path.

Older Hermes Agent versions that lack either capability continue through
`_LegacyProfileGoalManager`, which pins persistence to the selected profile's explicit
`state.db` path. This includes intermediate versions whose context API is present but
whose default `SessionDB()` path remains frozen at module import. Keep this fallback
compatibility-only: new goal semantics belong in Hermes Agent's native manager rather
than a second WebUI implementation.

### 4.9 Hermes Agent Moved-Name Compatibility

Hermes Agent owns its module layout. Its September 2026 decomposition moved names the
WebUI uses (for example `tools.approval.set_current_session_key` to
`tools.approval_context`) into `<stem>_<topic>` sibling modules. The old paths resolve
only through temporary PLUGIN-COMPAT `__getattr__` pointers that emit
`HermesPluginCompatWarning` and are removed on schedule. The Agent's
`compat_manifest.json` is the authoritative map of what moved where.

WebUI code reaches a moved name only through
`api.agent_compat.agent_attr(owner, name, home, default=...)`, which resolves in this
order:

1. the owner module's own namespace: pre-split Agents, and tests that stub the original
   module in `sys.modules` or patch the name onto it;
2. the `home` module: split Agents, with or without the old-path pointer (no warning);
3. plain attribute access on the owner: non-module test doubles.

It raises like the import it replaces (or returns `default`), so each call site keeps its
existing fallback. Current users: approval session identity and MCP discovery
(`streaming.py`), `/reload-mcp` (`commands.py`), MCP runtime status (`routes.py`), Claude
Code credential linking (`oauth.py`), LM Studio reasoning options (`config.py`), and
kanban connections and dispatch (`kanban_bridge.py`).

Import names that are still native to their module directly. Never
`from <old module> import <moved name>`, and never feature-detect a moved name with
`hasattr`/`getattr` on the old module: once the pointers are removed those silently turn
"moved" into "missing" behind the call sites' broad `except` blocks. When a later Agent
split moves another name, route it through `agent_attr` and add pre-split and
pointer-removed cases to `tests/test_agent_compat.py`. The resolver is
compatibility-only: delete it, and import directly from the new homes, once the WebUI
stops supporting Agents that predate the split.

### 4.10 MCP Runtime Profile Boundary

Hermes Agent owns the in-process MCP ledger. It keys a connection by
`(profile_home_key, name)` and registers its tools in that profile's registry overlay
only when the calling task serves a *routed* profile: the context-local Hermes-home
override differs from the process home. Otherwise the connection uses the bare server
name and the global registry slot, which belong to the process profile.

- `api.profiles._set_hermes_home()` is the single writer of the process-profile home:
  startup (`init_profile_state()`) and `switch_profile(process_wide=True)` both go
  through it, so `get_process_profile_home()` and the Agent pin
  (`hermes_constants.pin_process_hermes_home()`, when the Agent provides it) are updated
  in the same step as `HERMES_HOME`. Streaming turns still mirror their profile into
  `os.environ['HERMES_HOME']` for legacy readers; without the pin, that mirror makes a
  turn's own profile look like the process profile, so same-named servers of different
  profiles share one bare-name connection.
- `/api/mcp/servers`, `/api/mcp/tools`, `/api/notes/sources` and `/reload-mcp` run
  their Agent calls inside `api.mcp_runtime.mcp_runtime_scope()`, which binds the
  request profile's home and secret scope (root profile included) without touching
  `os.environ`. Status, tool count and inventory are read from one scope; the inventory
  lists only tools in that profile's own registry slot for servers it configures.
- A connection *serves* a profile when that profile owns it (`_server_scope_keys`, else
  the scope in the key) or adopted it (`_server_tool_scopes`, an identical shared
  connection). `api.mcp_runtime.ledger_key_serves_view()` is the one predicate for
  status, inventory and the reload summary. The Agent's launch-profile view (scope
  `None`) is process-wide, so `filter_runtime_status_to_view()` downgrades
  `get_mcp_status()` rows of servers the root profile does not serve to `configured`
  rather than showing a routed profile's connection, tool count or connect error.
- The scope is trusted only when the Agent's routing decision matches the profile WebUI
  resolved. When it cannot be confirmed (for example a same-profile turn's mirror on an
  Agent without the pin), status and inventory withhold runtime data
  (`runtime_scope: "unavailable"`, shown as a notice in the MCP panel) and
  `/reload-mcp` refuses instead of resetting another owner's connection.
- `/reload-mcp` calls `shutdown_mcp_servers(scope=..., names=...)` with the profile's
  own scope and the names of its live connections; `scope=None` without `names` is the
  process-wide wildcard and is used only with Agents that predate profile-scoped MCP
  (`runtime_scope: "legacy_process"`). A scoped shutdown only clears connect backoff for
  the live keys it tears down, so WebUI also drops the profile's own cooldown/error
  entries (`clear_profile_connect_cooldowns()`) before rediscovery: a server that failed
  to spawn is retried by the reload, as the wildcard did, without touching another
  owner's backoff.

Status and inventory stay passive: they never start or probe an MCP server. Ledger key
helpers resolve to Hermes Agent's `tools.mcp_tool_scope` when present so the key shape
has one owner; the local fallbacks only cover Agents that predate that module.

---

## 5. Frontend Architecture: Current State

### 5.1 Structure

The frontend is served from static/ as separate files: one HTML template, one CSS file,
and multiple JavaScript modules. External dependencies include Prism.js (syntax
highlighting), Mermaid.js (diagrams), xterm.js, and KaTeX assets loaded with the
current static template's integrity/CSP assumptions.

Core JS modules loaded by the app include:
  1. ui.js         (~7216 lines) DOM helpers, renderMd, tool card rendering, global state
  2. workspace.js  (~369 lines) File tree, preview, file operations
  3. sessions.js  (~3517 lines) Session CRUD, list rendering, search, SVG icons, dropdown actions, project picker
  4. messages.js  (~2301 lines) send(), SSE event handlers, approval, transcript
  5. panels.js    (~6480 lines) Cron, skills, memory, workspace, profiles, todo, settings
  6. commands.js  (~1302 lines) Slash command registry, parser, autocomplete dropdown
  7. boot.js      (~1607 lines) Event wiring + boot IIFE

sessions.js defines an `ICONS` constant at module level with hardcoded SVG strings for all
session action buttons (pin, unpin, folder, archive, unarchive, duplicate, trash). All icons
inherit `currentColor` for consistent theming.

Three-panel layout (in static/index.html):

    <aside class="sidebar">    Left panel: session list, nav tabs, sidebar-footer Hermes WebUI trigger
    <main class="main">        Center: topbar, messages area, approval card, composer
    <aside class="rightpanel"> Right panel: workspace file tree and file preview

Composer footer layout (current):

    left cluster   attach button, mic button, per-conversation model selector
    right cluster  compact circular context-usage badge, send button

The model selector is still the authoritative control for new-session creation
and session updates; it was moved out of the sidebar so model choice feels scoped
to the active conversation rather than a global app setting.

### 5.2 Global State

    const S = {
      session:      null,   // current Session compact dict (includes model, workspace, title)
      messages:     [],     // full messages array for current session
      entries:      [],     // current directory listing
      busy:         false,  // true while agent is running (disables Send button)
      pendingFiles: []      // File objects queued for upload with next message
    }

    const INFLIGHT = {}
    // keyed by session_id while a request is in-flight for that session
    // value: {messages: [...snapshot...], uploaded: [...filenames...]}
    // Purpose: if user switches sessions while a request is pending,
    //   switching back shows the in-progress state instead of the saved state

### 5.3 Key Functions Reference

Session management:
    newSession()          POST /api/session/new, update S.session, save to localStorage
    loadSession(sid)      GET /api/session?session_id=X (initial load uses the
                          bounded tail `msg_limit=30`; jump-to-start and outline
                          jump pass `msg_limit=all`), check INFLIGHT first, update S
    deleteSession(sid)    POST /api/session/delete, handle active/inactive cases correctly
    renderSessionList()   GET /api/sessions, rebuild #sessionList DOM

Chat:
    send()                Main action: upload files, POST /api/chat/start, open EventSource
    uploadPendingFiles()  Upload each file in S.pendingFiles, return filenames array
    appendThinking()      Adds three-dot animation to message list
    removeThinking()      Removes thinking dots (called on first token or on error)

Rendering:
    renderMessages()      Full rebuild of #msgInner from S.messages
    renderMd(raw)         Homegrown markdown renderer (see 5.4 for known gaps)
    syncTopbar()          Updates topbar title, meta, model chip, workspace chip
    renderTray()          Updates attach tray showing pending files

Approval:
    showApprovalCard(p)   Shows the approval card with command/description text
    hideApprovalCard()    Hides approval card, clears text
    respondApproval(ch)   POST /api/approval/respond, hide card
    startApprovalPolling  setInterval 1500ms GET /api/approval/pending
    stopApprovalPolling   clearInterval

UI helpers:
    setStatus(t)          Fallback helper: shows a toast for non-chat status/error messages
    setComposerStatus(t)  Updates the inline composer status label for turn-scoped states
    setBusy(v)            Sets S.busy, disables/enables Send button, clears status on false
    showToast(msg, ms)    Bottom-center fade toast (default 2800ms)
    showConfirmDialog(o)  Shared in-app confirmation modal, resolves true/false
    showPromptDialog(o)   Shared in-app input modal, resolves string/null
    autoResize()          Auto-resize #msg textarea up to 200px

Dialog policy:
    Native browser confirm()/prompt() are not used in the Web UI.
    Destructive actions use showConfirmDialog(...), then a toast on success.
    Lightweight naming flows (new file/folder/project) use showPromptDialog(...).

Files:
    loadDir(path)         GET /api/list, rebuild #fileTree
    openFile(path)        GET /api/file, show in #previewArea

Transcript:
    transcript()          Builds markdown string from S.messages for download

Boot IIFE:
    localStorage key 'hermes-webui-session' stores last session_id
    On load: try to loadSession(saved), fall back to empty state if missing or fails
    NEVER auto-creates a session on boot

### 5.4 Markdown Renderer (renderMd)

A hand-rolled regex chain with HTML safety. Processes in this order:

Pre-pass (v0.18.1):
0a. Stash fenced code blocks and backtick spans (fence_stash array)
0b. Convert safe HTML tags to markdown equivalents:
    <strong>/<b> -> **text**, <em>/<i> -> *text*, <code> -> `text`, <br> -> newline
0c. Restore stashed code blocks

Pipeline:
1. Mermaid blocks (```mermaid ... ```) -> <div class="mermaid-block">
2. Code blocks (``` lang ... ```) -> <pre><code> with language header
3. Inline code (`...`) -> <code>
4. Bold+italic (***..***) -> <strong><em>
5. Bold (**...**) -> <strong>
6. Italic (*...*) -> <em>
7. Headings (# ## ###) -> <h1> <h2> <h3> (uses inlineMd() for content)
8. Horizontal rules (---+) -> <hr>
9. Blockquotes (> ...) -> <blockquote> (uses inlineMd() for content)
10. Unordered lists (- or * or + at line start) -> <ul><li> (uses inlineMd())
11. Ordered lists (N. at line start) -> <ol><li> (uses inlineMd())
12. Links ([text](https://...)) -> <a href target=_blank>
13. Tables (| col | col |) -> <table>
14. Safety net: escape any HTML tag not in SAFE_TAGS allowlist via esc()
15. Paragraph wrapping: remaining double-newline-separated blocks -> <p>

inlineMd() helper (v0.18.1):
    Processes inline bold/italic/code/links within list items, blockquotes,
    and headings. Escapes unknown tags via SAFE_INLINE allowlist. Replaces
    the old direct esc() calls which would double-escape pre-pass output.

SAFE_TAGS allowlist:
    strong, em, code, pre, h1-6, ul, ol, li, table, thead, tbody, tr, th,
    td, hr, blockquote, p, br, a, div. Everything else is escaped.

Known gaps:
- Nested lists: single regex pass, multi-level indentation not handled
- Mixed bold+link in same line: may produce garbled output

### 5.5 Model Label Resolution (Fixed in Sprint 1, reused by composer selector)

B3 was resolved in Sprint 1. Current code uses a MODEL_LABELS dict:

    const MODEL_LABELS = {
      'openai/gpt-5.4-mini': 'GPT-5.4 Mini', 'openai/gpt-4o': 'GPT-4o',
      'openai/o3': 'o3', 'openai/o4-mini': 'o4-mini',
      'anthropic/claude-sonnet-4.6': 'Sonnet 4.6', 'anthropic/claude-sonnet-4-5': 'Sonnet 4.5',
      'anthropic/claude-haiku-3-5': 'Haiku 3.5', 'google/gemini-2.5-pro': 'Gemini 2.5 Pro',
      'deepseek/deepseek-chat-v3-0324': 'DeepSeek V3', 'meta-llama/llama-4-scout': 'Llama 4 Scout',
    };
    getModelLabel(m) => MODEL_LABELS[m] || (m.split('/').pop() || 'Unknown');

Fallback: any unlisted model shows its short ID (after the last /) rather than a wrong label.
To add a new model: add an entry to MODEL_LABELS and add an <option> to the composer footer <select>.

### 5.6 Session Delete Rules (from skill)

These rules are critical. GPT-5.4-mini has repeatedly re-introduced broken versions.

1. deleteSession() NEVER calls newSession(). Deleting does not create.
2. If deleted session was active AND other sessions exist: load sessions[0] (most recent).
3. If deleted session was active AND no sessions remain: show empty state.
4. If deleted session was not active: just re-render the list.
5. Always show toast("Conversation deleted") after any delete.

### 5.7 Send() Session Guard

Before any async operations in send():
    const activeSid = S.session.session_id;

After the agent completes:
    if (S.session && S.session.session_id === activeSid) {
      // apply result, re-render
      setBusy(false);
    } else {
      // user switched sessions mid-flight
      // only refresh sidebar, do NOT call setBusy(false) on the new session
      await renderSessionList();
    }

This prevents a session switch mid-flight from either clobbering the new session's state
or unlocking the Send button on the wrong session.

---

## 6. Data Flow: Full Chat Round Trip

Step-by-step trace of what happens when you type a message and press Send:

1.  User types, presses Enter. send() is called.
2.  Guard: return if (!text && !pendingFiles) || S.busy
3.  If S.session is null: await newSession(), await renderSessionList()
4.  Capture activeSid = S.session.session_id (before any awaits)
5.  uploadPendingFiles(): POST each file in S.pendingFiles to /api/upload
    - Shows upload progress bar
    - Clears S.pendingFiles on completion
    - Returns array of uploaded filenames
6.  Build msgText from text + file note
7.  Build userMsg {role:'user', content: displayText, attachments?: filenames}
8.  Push userMsg to S.messages, call renderMessages(), appendThinking()
9.  setBusy(true), setStatus('Hermes is thinking...')
10. INFLIGHT[activeSid] = {messages: [...S.messages], uploaded}
11. startApprovalPolling(activeSid)
12. POST /api/chat/start {session_id, message, model, workspace}
    Server: saves session, creates queue.Queue, starts daemon thread, returns {stream_id}
13. Browser opens EventSource('/api/chat/stream?stream_id=X')
14. In the SSE loop:
    - 'token': assistantText += d.text, ensureAssistantRow(), render markdown
    - 'tool': setStatus('tool name...')
    - 'approval': showApprovalCard(d)
    - 'done': sync S from d.session, renderMessages(), loadDir, renderSessionList,
               setBusy(false), delete INFLIGHT[activeSid]
    - 'error': show error message, setBusy(false)
    - es.onerror: handle network drops (show error, setBusy(false))
15. If approval needed: user clicks a button, respondApproval() fires
    POST /api/approval/respond -> server pops _pending, calls approve_*
    Agent retries the command (now is_approved() returns True) and continues

---

## 7. Dependency Map

server.py imports from api/ modules (config, helpers, models, workspace, upload, streaming).
The api/ modules in turn import Hermes internals:

    api/streaming.py imports:
      run_agent.AIAgent              Main agent class. Wraps LLM + tool execution.
    api/config.py imports:
      yaml                           Config loading.
    server.py imports:
      tools.approval.*               Module-level approval state (with graceful fallback).
    Standard library across all modules: json, os, re, sys, threading, time, traceback,
      uuid, http.server, pathlib, urllib.parse, email.parser, queue, collections

AIAgent constructor parameters used:

    model=               OpenRouter model ID string
    platform='cli'       Sets the platform context for tool selection
    quiet_mode=True      Suppresses agent's own stdout output
    enabled_toolsets=    List of toolset names from config.yaml
    session_id=          Used for tool state keying (memory, todos, etc.)
    stream_delta_callback=   Called per token delta (or None as sentinel)
    tool_progress_callback=  Called per tool invocation (name, preview, args)

AIAgent.run_conversation() parameters:

    user_message=           The human turn text
    conversation_history=   Prior messages list (OpenAI format)
    task_id=                Session ID (NOTE: NOT session_id=, it is task_id=)

Return value:

    {
      'messages': [...],          Full conversation including new turns
      'final_response': '...',    Last assistant text response
      'completed': True/False,    Whether the conversation completed normally
      ...other fields
    }

---

## 8. Configuration Loading

On startup, server.py reads ~/.hermes/config.yaml:

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    CLI_TOOLSETS = cfg.get('platform_toolsets', {}).get('cli', [...default...])

Default toolset list (hardcoded fallback):
    browser, clarify, code_execution, cronjob, delegation, file,
    image_gen, memory, session_search, skills, terminal, todo, tts, vision, web

The web UI always runs with the full CLI toolset. There is no per-session toolset
restriction from the UI yet.

Many Profiles are in use at once, so a request reads its own Profile's config.
`api/config.py` keeps the shared cache (`cfg`, `_cfg_cache`) for the process
Profile. A request whose Profile is another one reads its own config view,
keyed by config path (`_cfg_views`): `get_config()`, `get_config_snapshot()`
and the module's config readers (through `_active_cfg()`) answer from it. A
view is rebuilt as a new dict when its file changes, never refilled in place,
so a request keeps reading the config it was handed while a request in another
Profile loads its own. Code in `api/config.py` reads config through
`_active_cfg()`, not the `cfg` alias.

---

## 9. How To Add a New API Endpoint

Every route is one row in the route table (`api/route_table.py`) plus one function in
`api/routes.py`. The server dispatches every request by looking up its row; there is no
other place to register a route.

### Backend (api/route_table.py + api/routes.py)

1. Add the row. It must say who may call it (`USER` or `ADMIN`; Admin-only is the
   safe choice), and, if the route names a session, whether it reads or writes it:

        _get("/api/your/endpoint", USER, handler="_get_api_your_endpoint"),
        _post("/api/your/endpoint", USER, session=WRITE, handler="_post_api_your_endpoint"),

2. Add the handler in `api/routes.py`. A GET handler takes `(handler, parsed)`; a POST,
   PUT, PATCH or DELETE handler takes `(handler, parsed, body, diag)`, with the JSON body
   already read, CSRF already checked and the session guard already run:

        def _get_api_your_endpoint(handler, parsed):
            qs = parse_qs(parsed.query)
            param = qs.get('param', [''])[0]
            if not param:
                return j(handler, {'error': 'param is required'}, status=400)
            # do work
            return j(handler, {'result': value})

        def _post_api_your_endpoint(handler, parsed, body, diag):
            value = body.get('field', '')
            if not value:
                return j(handler, {'error': 'field is required'}, status=400)
            # do work
            return j(handler, {'ok': True, 'data': result})

   A handler that must read its own body (multipart upload) sets `body="own"` on its row.

Endpoint requiring a valid session:

    sid = body.get('session_id', '')
    try:
        s = get_session(sid)
    except KeyError:
        return j(self, {'error': 'Session not found'}, status=404)

Endpoint that calls Hermes Python modules:

    # Example: calling cron.jobs
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from cron.jobs import list_jobs
    jobs = list_jobs(include_disabled=True)
    return j(self, {'jobs': jobs})

### Frontend (6 static JS modules: ui.js, workspace.js, sessions.js, messages.js, panels.js, boot.js)

Simple GET fetch:

    const data = await api('/api/your/endpoint?param=' + encodeURIComponent(value));
    // data is parsed JSON response, throws on error

POST:

    const data = await api('/api/your/endpoint', {
      method: 'POST',
      body: JSON.stringify({field: value})
    });

The api() helper:

    async function api(path, opts={}) {
      const r = await fetch(path, {headers:{'Content-Type':'application/json'},...opts});
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      return d;
    }

---

## 10. Common Debugging Commands

    # Server health and session count
    curl -s http://127.0.0.1:8787/health | python3 -m json.tool

    # Tail the server log live
    tail -f ~/.hermes/webui/bootstrap-8787.log
    tail -f ~/.hermes/webui.log  # when launched through ctl.sh

    # List all sessions (metadata only)
    curl -s http://127.0.0.1:8787/api/sessions | python3 -m json.tool

    # Inspect a full session with messages
    SID=your_session_id_here
    curl -s "http://127.0.0.1:8787/api/session?session_id=$SID" | python3 -m json.tool

    # Kill and restart server cleanly
    pkill -f "python.*server.py"
    <repo>/start.sh

    # Check if server process is running
    ps aux | grep "server.py"

    # Inspect session files on disk
    ls -lt ~/.hermes/webui/sessions/
    cat ~/.hermes/webui/sessions/SESSION_ID.json | python3 -m json.tool

    # Count messages in a session
    python3 -c "import json; d=json.load(open('sessions/SID.json')); print(len(d['messages']))"

    # Check approval module state
    cd <agent-dir>
    venv/bin/python -c "from tools.approval import _pending; print(_pending)"

    # Check active SSE streams (requires server access)
    curl -s http://127.0.0.1:8787/health  # streams not exposed yet, add in Phase G

    # Find all sessions with messages (not Untitled empty)
    ls ~/.hermes/webui/sessions/ | xargs -I{} python3 -c "
    import json, sys
    d = json.load(open('~/.hermes/webui/sessions/{}'))
    if d['messages']: print('{}', d['title'][:50])
    " 2>/dev/null

---

## 11. Architecture Decision Records

### ADR-001: Single-File Server
Decision: All code in server.py
Rationale: No build step, easy agent modification, zero deployment complexity.
Trade-off: Maintenance burden grows with file size.
Resolution: Phase A splits the file.

### ADR-002: HTML as Python Raw String
Decision: Frontend embedded in server.py as r"""..."""
Rationale: Simplest way to serve frontend without static file server or build system.
Trade-off: No editor syntax highlighting, complex patching, base64 gymnastics for large edits.
Resolution: Phase A moves to static/index.html served from disk.

### ADR-003: ThreadingHTTPServer
Decision: Python stdlib, synchronous threads, not asyncio.
Rationale: No dependencies, synchronous agent calls fit naturally in threads.
Trade-off: Memory scales linearly with concurrent users. Thread pool is unbounded.
Resolution: Acceptable for single-user. Phase J adds concurrency limits if needed.

### ADR-004: SSE over WebSockets
Decision: Server-Sent Events for streaming.
Rationale: Simpler than WebSockets, unidirectional, no upgrade handshake, EventSource is
standard browser API.
Trade-off: Server-to-client only. Approval events use SSE from agent thread + polling fallback.
Resolution: No plan to switch. SSE is sufficient.

### ADR-005: Module-Level Approval State
Decision: tools/approval.py uses module-level _pending dict shared across all threads.
Rationale: The approval system was pre-existing; sharing state via same Python process works.
Trade-off: Breaks if ever moved to multi-process (gunicorn workers) or subprocess.
Resolution: Document the constraint. Move to SQLite if scaling is ever needed.

### ADR-006: No Authentication
Decision: No auth initially.
Rationale: Localhost-only via SSH tunnel. Auth adds complexity without security benefit
when the transport layer (SSH) is already authenticated.
Trade-off: Anyone on the VPS with localhost access can use the server.
Resolution: Phase H adds optional password gate for direct-access deployments.

### ADR-007: Approval State via Environment Variables
Decision: HERMES_EXEC_ASK and HERMES_SESSION_KEY passed via os.environ.
Rationale: tools/approval.py and terminal_tool.py already read these env vars.
Trade-off: Process-global. Two concurrent chat requests clobber each other.
Resolution: Phase B replaces with thread-local or explicit parameter passing.

---

## 12. Working Conventions for Agent Contributors

This section is specifically for agents (Hermes instances, subagents, Codex, etc.) that
will be working on this codebase. Read this before touching any file.

### Before Making Any Change

1. Read this document (ARCHITECTURE.md) fully. Especially sections 4, 5, and the ADRs.
2. Inspect the relevant module under `api/` or `static/`; `server.py` is only the routing shell.
3. Check `git log` for the files you are touching to understand what was recently changed.
4. Run the relevant test slice first to confirm baseline, for example:
   ./scripts/test.sh tests/test_regressions.py -q
5. Check server health: curl -s http://127.0.0.1:8787/health

### Making Changes

Keep edits scoped to the module that owns the behavior. Use exact string
matching when making mechanical patches and verify that the intended old string
was found before replacing it.

After any change:
    venv/bin/python -m py_compile server.py             # syntax check
    curl -s http://127.0.0.1:8787/health                # server still alive
    venv/bin/python -m pytest tests/ -v                 # tests still pass

### Critical Rules (do NOT regress these)

These patterns have been broken and fixed multiple times. Do not re-introduce them.

RULE-1: deleteSession() must NEVER call newSession().
    Deleting does not create. If the deleted session was active and others remain,
    load sessions[0]. If none remain, show empty state. See Section 5.6.

RULE-2: /api/upload must be checked BEFORE read_body() in do_POST.
    read_body() consumes the request body. Upload parsing also needs the body.
    Order matters. See Section 4.1.

RULE-3: run_conversation() takes task_id=, NOT session_id=.
    task_id is the correct keyword argument. session_id= raises TypeError silently.

RULE-4: stream_delta_callback receives None as end-of-stream sentinel.
    The on_token callback must guard: if text is None: return

RULE-5: send() must capture activeSid BEFORE any await.
    The active session can change while awaits are pending. Capture first, guard on return.

RULE-6: Boot IIFE must never auto-create a session.
    Only two places create sessions: the + button and send() when S.session is null.

RULE-7: All SESSIONS dict accesses must hold LOCK.
    LOCK is a module-level threading.Lock(). Use: with LOCK: ...

RULE-8: do NOT expose tracebacks to API clients.
    500 responses should return {"error": "Internal server error"}, not the full traceback.
    (Currently traceback is exposed; fix in Phase D. Do not make it worse.)

RULE-9: Pattern_keys, not pattern_key, for multi-pattern approvals.
    The approval module may include both pattern_key (singular, legacy) and pattern_keys
    (plural, all matched patterns). Always iterate pattern_keys when approving.

### Adding New API Endpoints

See Section 11 for the exact code pattern. Short version:
- GET: add before the 404 fallback in do_GET
- POST: add after /api/upload check and after read_body(), before 404 fallback in do_POST
- Always validate required fields, return 400 for missing/invalid input
- Always use get_session(sid) with try/except KeyError -> 400 or 404
- Add a test in test_sprint1.py or a new test file

### Updating This Document

Update ARCHITECTURE.md whenever you:
- Add a new endpoint (add to Section 4.1 routing table)
- Discover a new pitfall or rule (add to Section 12)

This document is the memory of the codebase. If it is not updated, future agents will
make the same mistakes again.

---

## 13. Endpoint Reference (Current)

Complete list of all HTTP endpoints as of Sprint 1 (v0.3).

### GET Endpoints

    /                          Returns full HTML app (index page)
    /index.html                Same as /
    /health                    {"status":"ok","sessions":N}
    /api/session               ?session_id=X -> session + messages. 400 if no ID.
                               Bare (no msg_limit) keeps the historical full-transcript
                               contract. Recovery paths request a bounded tail
                               (`msg_limit=30`) and restore `_messages_truncated` /
                               `_messages_offset` before persisting anchor-scene
                               metadata. Outline jump and jump-to-start opt in to the
                               full transcript via the explicit `msg_limit=all`
                               escape hatch. See #7310 / #7625 / #7628.
    /api/sessions              List of all session compact() dicts, sorted by updated_at
    /api/list                  ?session_id=X&path=. -> directory listing for session workspace
    /api/file                  ?session_id=X&path=rel -> file content (text, 200KB limit)
    /share/<token>             Public read-only HTML shell for a sanitized shared transcript snapshot
    /api/share/<token>         Public JSON payload for a sanitized shared transcript snapshot
    /api/chat/stream           ?stream_id=X -> SSE stream. Long-lived. Emits token/tool/
                               approval/done/error events.
    /api/chat/stream/status    ?stream_id=X -> {"active": true/false, "stream_id": X}
    /api/approval/pending      ?session_id=X -> {"pending": entry_or_null}. The approval/clarify
                               fallback pollers stop on a 409 session_profile_mismatch.
    /api/git-info              ?session_id=X -> {"git": status_or_null}. State.db-only sessions
                               (CLI, subagents) use their stored workspace if it resolves via
                               resolve_trusted_workspace; missing/untrusted -> {"git": null}.
    /api/approval/inject_test  ?session_id=X&pattern_key=K&command=C -> test-only endpoint.
                               Injects a pending approval entry into the server process.
    /api/file/raw              ?session_id=X&path=P -> raw file bytes with correct MIME type.
                               Used for image preview. Path traversal protected via resolve_in_workspace.
                               Returns 404 JSON if file not found.

### POST Endpoints

    /api/upload                multipart/form-data. Fields: session_id, file. Returns filename.
    /api/session/new           {"model"?, "workspace"?} -> new session
    /api/session/update        {"session_id", "workspace"?, "model"?} -> updated session
    /api/session/delete        {"session_id"} -> {"ok": true}
    /api/chat/start            {"session_id", "message", "model"?, "workspace"?}
                               -> {"stream_id", "session_id"}. Starts agent daemon thread.
    /api/chat                  (fallback, sync) {"session_id", "message", "model"?, "workspace"?}
                               -> blocks until agent finishes. Returns full result.
    /api/share/create          {"session_id"} -> creates or refreshes a public read-only snapshot link
    /api/share/revoke          {"session_id"} -> revokes the current public snapshot link
    /api/approval/respond      {"session_id", "choice": once|session|always|deny}
                               -> {"ok": true, "choice": choice}

### GET Endpoints Added in Sprint 3

    /api/crons                 All cron jobs. Returns {jobs: [...]}.
    /api/crons/output          ?job_id=X&limit=N -> {outputs: [{filename, content}]}
    /api/skills                All skills. Returns {skills: [{name, description, category}]}
    /api/skills/content        ?name=X -> full skill data including SKILL.md content
    /api/memory                MEMORY.md + USER.md + SOUL.md. Returns {memory, user, soul, *_path, *_mtime}

### POST Endpoints Added in Sprint 3

    /api/crons/run             {job_id} -> triggers run in daemon thread. Returns {ok, status}.
    /api/crons/pause           {job_id} -> {ok, job} or 404.
    /api/crons/resume          {job_id} -> {ok, job} or 404.

---

## Workspace path trust levels

`api/workspace.py` has two distinct trust functions — do not collapse them:

**`validate_workspace_to_add(path)`** — used by `/api/workspaces/add` (explicit user registration).
Permissive: blocks only non-existent, non-directory, and system root paths. The user is
consciously registering an external path (e.g. `/mnt/d/Projects` in WSL), so we trust intent.

**`resolve_trusted_workspace(path)`** — used for actual file read/write operations inside
an existing workspace. Strict: path must be under home, in the saved workspace list, or under
`BOOT_DEFAULT_WORKSPACE`. Prevents path traversal and unauthorized file access.

The distinction matters because add uses permissive validation to avoid the circular
dependency: you cannot get a path into the saved list if you need the saved list to add it.

**GFIT-CoWork Users.** Both functions ask the request's Workspace policy
(`api/workspace_policy.py`). For a User's request it applies one stricter rule instead:
the path must resolve (after `..` and symlinks) inside `<Profile>/workspace`.
`workspace.resolve_in_workspace` applies it again to every file operation (the unconfined
primitive followed by the policy's `confine`), so a Workspace root that is somehow outside
still grants nothing. `helpers.resolve_inside` is the unconfined primitive, for roots that are
not Workspaces (the session attachment inbox). The Admin is not confined.

## GFIT-CoWork access control

- `api/directory.py` — the Directory seam (username + password → Identity); `api/ldap_directory.py`
  is the company-AD implementation (LDAPS or StartTLS only).
- `api/login.py` — the login flow (rate limit → Directory → Admission → session) and the
  rate limit itself (per person behind a trusted proxy, via `api/trusted_proxy.py`, kept
  in `STATE_DIR/.login_attempts.json` across restarts), and the
  startup check: login is on exactly when a Directory is configured, and with none the
  server serves only on the loopback address. Leftover Upstream login settings are ignored
  and reported.
- `api/auth.py` — only Directory sessions are honoured; every request re-asks Admission
  (`access.admit_request`) and ends the session when it no longer gives the session's role
  and Profile; otherwise it runs the request in the Admission's Profile.
- `api/access.py` — Admission (`admit`: from a confirmed employee ID, Admin at `default`, User
  at their own active Profile, or refused with a reason). The answer confirmed for a request is
  kept as **the request's Admission** (`request_admission`, with `caller_is_user` and
  `caller_bound_profile`): the one answer to "who is calling?" for the rest of that request.
  The Admin gate, the page shell's role, the login status role, the profile-name guard, the
  session-ownership answer, the file viewer and Workspace confinement all ask it; none reads
  the role from the session record. A User's request is bound to their Profile exactly
  because its Admission is a User's; there is no separate pin. Upstream's isolated profile
  mode (`profiles._is_isolated_profile_mode`) is a process posture only and knows nothing of
  the caller. It lives on the request thread (worker
  threads carry none) and is cleared with the request Profile
  (`profiles.clear_request_profile`) on every exit, before the next keep-alive request.
  `tests/test_gfit_request_admission_guard.py` fails when code outside
  `api/access.py` reads the session role or a pin, or when anything stores a pin again. The
  Admin gate (`user_may_call`, enforced in `check_auth`) answers from the route table: a User
  may call a route whose row says `USER`; everything else, including a path with no row, is
  Admin-only (fail closed).
- `api/route_table.py` — the route table: one row per HTTP route, the one place that says
  which handler serves it, who may call it (`USER`/`ADMIN`), whether it needs a CSRF token,
  how its body is read, whether the session guard runs, and whether it reads or writes a
  session. One matcher (exact path, then `<id>` segments, then the longest prefix) chooses the
  row; the dispatchers in `api/routes.py`, the Admin gate, session ownership and the CSRF
  check all read it. A User prefix row needs its reason in `VARIABLE_PATH_PREFIXES`.
  `tests/test_gfit_route_table.py` checks every row and that each answers as before.
- `api/workspace_policy.py` — the Workspace policy: the one answer to "what may this request
  touch?", as the request's Admission is the one answer to "who is calling?". It is chosen once
  per request from the request's Admission (`request_workspace_policy`, the only mapping): a
  User's policy (everything inside `<Profile>/workspace`), the unconfined policy (the Admin,
  login turned off, worker threads), or the refusing answer (a Directory session with no
  Admission: `access.request_has_directory_session`). Choosing and listing Workspaces, file
  roots and confinement, and the git, media, rollback (through the saved list) and worktree
  checks all ask it; no other code asks "is the caller a User?" to decide confinement, and
  `tests/test_gfit_workspace_policy_guard.py` fails when it does (its allowlist is empty).
  A *profile* argument to a Workspace function is the Admin's; for a User the policy's own
  Profile wins. Login is the one place that makes a User's Workspace from an explicit
  Profile (`workspace.ensure_user_workspace`), because it runs before the request has an
  Admission.
  A User's wiki lives in their Workspace: `<Profile>/workspace/wiki` by default, or their own
  Profile's `WIKI_PATH`/`wiki.path` when the policy lets them use it (otherwise no wiki). The
  process environment and the server account's `~/wiki` are never a User's wiki. Login also
  records the default as `WIKI_PATH` in the Profile's `.env` when it has none
  (`workspace.ensure_user_wiki`, without touching `os.environ`), so the Agent's llm-wiki skill
  writes where the WebUI reads.
- `api/session_ownership.py` — session ownership: the one answer to "whose session is
  this?". Like the Workspace policy, one adapter is chosen per request from the request's
  Admission (`request_session_ownership`): a User's adapter, the Admin's adapter, the
  unconfined adapter (login turned off, worker threads: Upstream's rules, including the 409
  that names the owning Profile) or the refusing answer (a Directory session with no
  Admission). The Admin's adapter is the unconfined rules for a Directory Admin who stays in
  `default` (ADR 0004): it never switches Profile, names only `default` as a request's
  `profile` (Profile management names Profiles under `name`), keeps none of Upstream's route
  exemptions, and another Profile's session is read-only in place. Each session-naming route's route-table row classifies it as a read or a write
  (`tests/test_gfit_session_route_kinds.py` keeps it complete); a read is answered, a write
  gets 403 `session_read_only` naming the owner, and the detail load marks the session
  `read_only` with `read_only_reason: "other_profile"` and `owner_profile`. The request's
  route comes from `access.request_route()`, recorded with the request's Profile by
  `access.settle_request` (the only setter of the request's Profile;
  `tests/test_gfit_request_profile_guard.py`). It answers whether
  a session id or stream id is the caller's (a `Refusal` writes its own 404 or 409); whether a
  session the route has already found (a record or a listed CLI row) is; whether a
  session-list event or a listed row may go to the caller; and whether the request may see
  Profile-less sessions. The dispatch guard (top-level and `/api/sessions/<id>/events` ids),
  the upload routes, the file-manager lookup, the session list and search, the detail load
  and export, the session-list events stream, approvals and clarify, stream ids, chat start,
  CLI import, compression recovery, anchor scenes and share links all ask it.
  - A User's adapter owns exactly their Profile's sessions: the WebUI record, then the
    Profile's own `state.db`. Another Profile's session, and an id or stream it cannot place,
    get 404 "Session not found", the same as a session that does not exist. For a User no route
    is exempt apart from the JSON import, which ignores any id in its body. The session-list
    events stream carries a User only their own Profile's events and the nudges that name no
    Profile and no session. Claude Code and Codex rows (scanned from the server account's home,
    no Profile) are left out.
  - **Bound**: the same module says a User's request may name no other Profile and may not
    switch Profile (`may_name_profile`, `may_switch_profile`); `routes._guard_bound_profile_request`
    asks it.
  - **Profile reach**: the same module says which Profiles a request may read, as a
    `ProfileReach`. `request_profile_reach(active, all_profiles=...)` answers for a view and
    follows Upstream's isolated profile mode: the session list and search, projects, the cron
    list, the Profile list and CLI import ask it, with the "N from other Profiles" count and
    `single_profile_mode`. `request_caller_reach()` answers what the caller may read at all,
    whatever the view: insights, cron status, the cron Profile picker, the per-Profile cron
    scan and every Profile-home lookup ask it. A User reaches only their own Profile; a
    Profile-home lookup outside the reach raises `profiles.ProfileNotReadable`, which
    `server.py` answers with 404 (no quiet retarget to the User's own home).
  - The session list cache is keyed by the view, not the caller: it is built inside
    `access.without_request_admission()` (the unconfined rule, wherever it is built) and each
    caller's rows and count are applied after the cache.
  - `tests/test_gfit_profile_reach_guard.py` fails when code outside the policy modules asks
    whether the caller is a User, reads isolated profile mode, or filters rows by comparing a
    row's Profile with the active Profile (each remaining match is listed with its reason;
    the allowlist is empty).
  - `tests/test_gfit_session_ownership_guard.py` fails when code outside the module compares a
    session's Profile with the active or bound Profile, or names a removed ownership helper
    (its allowlist is empty). `tests/test_gfit_session_route_answers.py` places every User
    route as naming a session or not, and checks each one that does.
- Naming: GFIT-CoWork code uses `CONTEXT.md`'s words, "User" (not Member) and "bound" (a
  User's request is bound to their Profile; not pinned). `tests/test_gfit_naming_guard.py`
  reads GFIT-CoWork's modules and fails on a module, function, class, parameter or
  module-level name that says "member" or "pinned", apart from upstream's pinned names it
  keeps on purpose. The User role is stored as `user`; a login stored with the old value
  `member` is read as `user` (`api/auth.py`).
- What the web app shows: `api/access.py` `SHELL_FEATURES` names each Admin-gated feature of
  the web app by its route, and `shell_features(role)` answers which ones the caller may use
  from the same gate. The app shell carries them on `<html data-gfit-may="...">` (none with
  login off, meaning all); `static/style.css` hides each feature's controls by feature, and
  `gfitMay(feature)` in `static/ui.js` keeps polls and pickers off routes the caller may not
  call. `tests/test_gfit_shell_features.py` checks the list against the gate and that the
  browser names features, not the role.
- `api/roster.py` — the Profile roster (display name, active/disabled, last login) in the
  state directory, and the owner of the Profile lifecycle: each Admin action on a Profile
  (`create_profile`, `disable_profile`, `enable_profile`, `delete_profile`) is one function
  that checks it is allowed, keeps the Hermes Profile and its record in step and returns
  the roster view, or raises `ProfileRefused` (a message and a kind, which the handler maps
  to 400/403/404/409/500). Names (and the clone-from name) are checked with the Hermes
  Profile layer's rule (`profiles._validate_profile_name`), and create refuses an Admin's ID
  (an Admin logs in to `default`). The steps are ordered so a failure part way leaves the Profile
  shut: create writes the record disabled first and makes it active only once the Hermes
  Profile exists; delete disables the Profile (ending its sessions) before deleting it, and a
  deletion that cannot finish leaves it disabled. A disabled Profile's sessions are also
  refused on every request (`auth._reconcile_directory_session`).
- `api/login.py` also writes the Directory display name into the roster on every
  User login (an Admin's rides on the session record, since an Admin has no Profile);
  `session_identity` gives `/api/auth/status` the `display_name` and "name (ID)" `label`
  the Profile chip shows. The chip opens an identity menu (`openIdentityMenu`) with Sign
  Out instead of the Profile switcher.
- `deploy/` — the Deployment kit: `docker-compose.yml` (one Team), `team.env.example`,
  and `caddy/` (the HTTPS reverse proxy shared by every Deployment on a server).
