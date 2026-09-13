# Hermes gateway API (verified)

Reference for building issues 03–07 against the **real** Hermes gateway. Verified
against the locally installed **Hermes Agent v0.21.2 (2026.9.11, upstream
a84a2223)** by reading its source; citations are package-relative paths inside
the Hermes install (`hermes --version` prints the install directory), stable
across machines for this version.

This is a `reference` document: confirm each method still exists in the running
Hermes before relying on it, since Hermes owns this surface and may change it.

## Transport

The gateway (`hermes serve`, default `127.0.0.1:9119`) speaks **JSON-RPC 2.0 over
a WebSocket**, the same surface the desktop app uses.

- Liveness: `GET http://127.0.0.1:9119/api/health` (already used by issue 01).
- Session token: `GET http://127.0.0.1:9119/` returns HTML embedding
  `window.__HERMES_SESSION_TOKEN__="<token>"`.
- WebSocket: `ws://127.0.0.1:9119/api/ws?token=<token>`.
- Requests: `{ "jsonrpc": "2.0", "id": <rid>, "method": "<name>", "params": {…} }`.
- Server→client pushes (streaming, approvals) arrive as JSON-RPC **notifications**
  on the same socket (no `id`).

Issue 01 already implements token read + WS connect + one request/response
(`setup.runtime_check`) in `src/host/local-hermes-workspace-gateway.ts` — the
same plumbing extends to every method below.

Method handlers live in `tui_gateway/` and are registered with an `@method("<name>")`
decorator (e.g. `tui_gateway/methods_session.py`, `methods_prompt.py`,
`methods_session_control.py`).

## Method map by issue

### Issue 02 — list Threads for a Workspace (supersedes the provisional guess)

- **`session.list`** — `tui_gateway/methods_session.py:425`
  - params: `limit` (default 200), `include_hidden` (bool), `title` (lookup).
  - returns: `{ "sessions": [ <row summary>, … ] }` via `_session_row_summary`
    (`methods_session.py:124`); each summary carries id, title, timestamps, source,
    and **cwd** info (`_cwd_info`, `methods_session.py:116`).
  - **Scope:** `session.list` has **no `workspace` param**. Scope to a Workspace by
    filtering rows whose cwd is under the Workspace path (the CLI
    `hermes sessions list --workspace NEEDLE` does a path substring/basename match).
  - Replaces the provisional `POST /api/sessions` call in
    `src/host/local-hermes-workspace-gateway.ts` (`listThreads`).

### Issue 03 — open and read a Thread

**Verified against a running Hermes (2026-09-13):** `session.list` returns the
**stored session key**; `session.history` and `session.activate` on that key both
fail with `4001 "session not found"` because they resolve a **runtime** session.
The working call is **`session.resume`**:

- **`session.resume`** — params: `session_id` = the stored key from `session.list`.
  - returns: `{ session_id (NEW runtime id), session_key (the stored key),
    resumed, running, status, message_count, messages, ... }`.
  - `messages[]` shape (verified): `{ role, text, timestamp, row_id }` — `text`
    is already a string; `row_id` is a number.
  - **The returned `session_id` is a new runtime id** (e.g. stored
    `20260913_182338_5c7588` → runtime `6d7e75e1`). Every later `prompt.submit`,
    `session.interrupt`, `approval.respond`, and stream filter must use this
    runtime id, not the stored key. The local gateway remembers the
    key → runtime mapping (`runtimeSessionByKey`).
  - `running`/`status` also report whether a turn is already active (useful for
    Live Thread detection, issue 06).
- `session.history` (`methods_session.py:1697`) works only for an already-live
  runtime session, so it is not used for opening a stored Thread.

### Issue 04 — create, prompt, stream, stop

- **`session.create`** — `methods_session.py:325`
  - params: `cwd`, `title`, `parent_session_id`, `profile`, `model`, `hidden`,
    `messages` (seed history), `cols`, `close_on_disconnect`.
  - Note: no DB row is written until the first prompt (drafts stay unpersisted).
- **`prompt.submit`** — `methods_prompt.py:544`
  - params: `session_id`, `text`, optional `surface`, `display_kind`, `interrupted`.
  - Prompt text is sanitized server-side (`sanitize_user_prompt_text`).
- **Streaming (VERIFIED 2026-09-13 against a live turn).** Two facts the source
  reading missed, confirmed by probing:
  1. **A single persistent WebSocket is required.** A gateway session is bound to
     the connection that created it. `session.create` then `prompt.submit` on the
     **same** socket returns `{"status":"streaming"}` and streams; doing each on a
     fresh socket (connect-per-call) silently no-ops — the created session is gone
     when its socket closes, and a separate stream socket receives nothing.
  2. **Events are wrapped.** Streaming updates arrive as a JSON-RPC notification
     with `method: "event"` and the specifics in `params`:
     `{ "method": "event", "params": { "type": "<type>", "session_id": "<runtime id>",
     "payload": { ... }, "seq": N } }`.
     Observed `params.type` values: `gateway.ready`, `session.info`,
     `message.start`, `session.title` (its `payload.session_id` is the STORED key),
     `thinking.delta` (`payload.text`), `message.delta` (`payload.text` — the answer),
     `reasoning.available`, `message.complete` (`payload.text` + `payload.usage`).
     Map `payload.text`, not a top-level `text`; filter by `params.session_id`
     (the runtime id from `session.resume`/`session.create`).
  3. **Turn boundary is `session.info.running`** — Hermes emits no `turn.*` event.
     `session.info` `payload.running` flips `true` (turn active) then `false`
     (turn done), and `payload` also carries `stored_session_id` and `title`.
  4. **Tool activity (verified live):**
     `tool.start` `payload` = `{ tool_id, name (e.g. "terminal"), context (the
     command), args }`; `tool.complete` `payload` = `{ tool_id, name, args:
     {command}, duration_s, result: { output, exit_code, error } }`. The command
     is in `context` on start and `args.command` on complete; output is
     `result.output`.
  5. **Approval (from source `server.py`).** `approval.request` `payload` carries
     `request_id`, `command` (redacted by Hermes), and `choices`
     (`["once","session","always","deny"]`); a timeout emits `approval.expire`
     `{ request_id }`. Resolve with `approval.respond` `{ session_id, request_id,
     choice }`. Not yet live-triggered: the local Hermes runs `approval_mode:
     "smart"`, which auto-allowed every command tried (including `rm -rf`), so no
     prompt fired — mapping is source-verified and unit-tested.
- **Stop / interrupt a running turn:** **`session.interrupt`** — `methods_session.py:1987`
  (takes `session_id`). Note: `session.control` (`methods_session_control.py:251`) is
  only for goal/loop/subgoal/heartbeat actions, **not** turn interruption.
- **`prompt.submit` returns `{status: "streaming"}`** on success (over the session's
  own socket).

### Issue 05 — Approval requests

- Server emits **`approval.request`** as a notification when Hermes needs a decision
  (`tui_gateway/server.py:688`, payload via `_approval_request_payload`).
- **`approval.pending`** — `methods_prompt.py:1134`: list pending approvals for a
  session (`list_gateway_approvals(session_key)`).
- **`approval.respond`** — `methods_prompt.py:1184`
  - params: `session_id`, `choice` (default `"deny"`), `request_id`, `all` (bool,
    resolve every pending).
- `approval.received` — `methods_prompt.py:1147`: fetch one request by `request_id`.

This matches the product's approval model in `CONTEXT.md` (allow/deny before a
dangerous action) — GFIT CoWork surfaces `approval.request`, lists via
`approval.pending`, and decides via `approval.respond`.

### Issue 06 — Live Thread ownership

- **`session.active_list`** — `methods_session.py:914`
  - returns: `{ "sessions": [ <live item>, … ] }` — "Live TUI sessions in this
    process (not a DB browser)", i.e. sessions currently attached/executing through
    this gateway. Takes `current_session_id`.
  - A Thread present here (running a turn) is a **Live Thread**: GFIT CoWork shows
    its activity but must not submit a competing prompt (per `CONTEXT.md`).
  - `turn.start` / `turn.end` notifications also signal when a Thread goes live/idle.

## Two Hermes surfaces (choose the WS gateway)

Hermes exposes two HTTP surfaces; GFIT CoWork must use the first:

- **tui_gateway WS JSON-RPC** — `hermes serve`, `127.0.0.1:9119`, token from `GET /`.
  The desktop-app protocol; issue 01 already attaches here; no extra credentials.
  **This is the surface GFIT CoWork uses** (matches `CONTEXT.md`: a gateway client).
- **OpenAI-compatible REST/SSE API server** — `gateway/platforms/api_server.py:1`,
  default port `8642`, auth via `API_SERVER_KEY`. It is a *messaging-gateway platform
  adapter* for generic OpenAI-compatible frontends (`GET/POST /api/sessions`,
  `GET /api/sessions/{id}/messages`, `POST /api/sessions/{id}/chat/stream` SSE). Do **not**
  use it: it must be separately enabled as a gateway platform and needs an API key,
  which conflicts with "GFIT CoWork does not ask for or store Hermes credentials" and
  abandons issue 01's surface.

## Open gap: Workspace-scoped Thread listing

`CONTEXT.md` requires the Thread list to contain **only** sessions belonging to the
selected Workspace. The WS gateway does **not** offer this:

- `session.list` returns compact rows `{id, title, preview, started_at,
  message_count, source}` — **no cwd** (`_session_row_summary`, `methods_session.py:124`).
- The handler forwards only `include_hidden`; it does not accept a `cwd_prefix`
  (`_listing_rows`, `methods_session.py:141`), even though `list_sessions_rich`
  (`hermes_state_sessions.py:1189`) supports `cwd_prefix`.
- `hermes sessions list --workspace` filters by reading the SQLite store **directly**
  (`hermes_cli/sessions_cmd.py:252`), not through the gateway.
- `session.info` is a push notification after activate/create, not a per-id lookup
  that returns cwd for an arbitrary stored session.

So there is no single gateway RPC that lists sessions scoped to a Workspace path.
This is a product decision (see the session notes / handoff); options considered:
(a) v1 shows recent sessions best-effort and documents scoping as a Hermes-surface
limitation; (b) the host reads the Hermes session SQLite store read-only to filter by
cwd (couples to Hermes internals, tension with the gateway-client boundary);
(c) use the REST API server (rejected above — needs a credential).

## Caveats

- Everything here is Hermes-owned and version-specific (v0.21.2). Treat method names
  and shapes as data to re-verify, not a stable contract.
- `session.list` returns recent sessions across sources; Workspace scoping is a
  client-side cwd filter, not a server param.
- A newly created session has no DB row until its first prompt; a fresh Workspace
  may legitimately show Threads that only exist in-memory until prompted.
