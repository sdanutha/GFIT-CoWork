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

- **`session.info`** — session metadata / cwd (`methods_session.py`, emitted at :871).
- **`session.history`** — `methods_session.py:1697`
  - params: `session_id`.
  - returns: `{ "count": n, "messages": [...] }` via `_history_to_messages`;
    pass `include_row_ids` so each turn keeps its durable row id.
- **`session.activate`** — `methods_session.py` (near :930): attach the frontend to a
  live session and get its payload (optionally `omit_messages`).

### Issue 04 — create, prompt, stream, stop

- **`session.create`** — `methods_session.py:325`
  - params: `cwd`, `title`, `parent_session_id`, `profile`, `model`, `hidden`,
    `messages` (seed history), `cols`, `close_on_disconnect`.
  - Note: no DB row is written until the first prompt (drafts stay unpersisted).
- **`prompt.submit`** — `methods_prompt.py:544`
  - params: `session_id`, `text`, optional `surface`, `display_kind`, `interrupted`.
  - Prompt text is sanitized server-side (`sanitize_user_prompt_text`).
- **Streaming** (server→client notifications, same socket):
  - `turn.start` / `turn.started`, `turn.end`, `turn.error`.
  - `message.start`, `message.delta` (incremental text), `message.interim`,
    `message.complete`.
- **Stop / interrupt a running turn:** **`session.interrupt`** — `methods_session.py:1987`
  (takes `session_id`). Note: `session.control` (`methods_session_control.py:251`) is
  only for goal/loop/subgoal/heartbeat actions, **not** turn interruption.

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

## Caveats

- Everything here is Hermes-owned and version-specific (v0.21.2). Treat method names
  and shapes as data to re-verify, not a stable contract.
- `session.list` returns recent sessions across sources; Workspace scoping is a
  client-side cwd filter, not a server param.
- A newly created session has no DB row until its first prompt; a fresh Workspace
  may legitimately show Threads that only exist in-memory until prompted.
