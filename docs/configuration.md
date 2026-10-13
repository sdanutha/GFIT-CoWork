# Configuration

How GFIT-CoWork finds what it needs, and the environment variables that change
it. The Directory and login variables are in [setup.md](setup.md#login); the
`deploy/` kit sets the ones a Deployment needs in `team.env`. Other settings go
in the Deployment's `.env` (`deploy/deployments/<team>/.env`), which
`deploy/cowork` passes to the container; [`.env.example`](../.env.example)
lists them.

## What the image sets up

GFIT-CoWork runs only in the container image from the `deploy/` kit
([ADR 0009](adr/0009-every-way-to-run-is-a-container.md)). The image and
`deploy/docker-compose.yml` set up what it needs:

| Thing | Where it is |
|---|---|
| Hermes agent dir | The `hermes-agent-src` volume, filled from the agent image and mounted read-only at `/home/hermeswebui/.hermes/hermes-agent` |
| Python executable | The container's venv, `/app/venv`; the first start installs the agent into it |
| State directory | `/home/hermeswebui/.hermes/webui`, which is the Deployment's `home/webui` |
| Default workspace | `/workspace`, which is the Deployment's `workspace/` |
| Port | `8787` in the container, published on `127.0.0.1:GFIT_PORT` |

State lives in the Deployment's folder, `deploy/deployments/<team>/home/webui`
(sessions, Workspaces, settings, projects, the Profile roster).
`HERMES_HOME_DIR` in its `.env` can move `home/` elsewhere.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEBUI_AGENT_DIR` | auto-discovered | Path to the Hermes Agent source or installed module root |
| `HERMES_WEBUI_PYTHON` | auto-discovered | Python executable |
| `HERMES_WEBUI_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for all IPv4, `::` for all IPv6, `::1` for IPv6 loopback) |
| `HERMES_WEBUI_PORT` | `8787` | Port |
| `HERMES_WEBUI_STATE_DIR` | `$HERMES_HOME/webui` (Windows default `%LOCALAPPDATA%\hermes\webui`, POSIX default `~/.hermes/webui`) | Where sessions and state are stored. **Note (upgrade):** the default now follows `HERMES_HOME` — if you previously relocated `HERMES_HOME` to a non-default base **without** setting `HERMES_WEBUI_STATE_DIR`, your GFIT-CoWork state now resolves to `$HERMES_HOME/webui` instead of the old platform-default `~/.hermes/webui`. To keep using the old location, set `HERMES_WEBUI_STATE_DIR` to it (or move the directory). Installs with `HERMES_HOME` unset or at the default base are unaffected. |
| `HERMES_WEBUI_SETTINGS_FILE` | `<state dir>/settings.json` | Optional path for this instance's settings file (sessions, workspaces and projects stay in the state directory). Read once at startup, so restart after changing it |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | `~/workspace` | Default workspace |
| `HERMES_WEBUI_DEFAULT_MODEL` | *(provider default)* | Optional model override; leave unset to use the active Hermes provider default |
| `HERMES_WEBUI_CSP_CONNECT_EXTRA` | *(unset)* | Optional space-separated `http(s)://` or `ws(s)://` origins to append to the CSP `connect-src` directive for trusted reverse-proxy or tunnel deployments |
| `HERMES_WEBUI_SSE_CHUNKED` | *(unset)* | Set truthy (`1`/`true`/`yes`/`on`) to send SSE with `Transfer-Encoding: chunked`. Needed behind buffering reverse proxies (e.g. `jupyter-server-proxy`) that otherwise buffer the whole stream; harmless but unnecessary for directly-served deployments |
| `HERMES_HOME` | Windows: `%LOCALAPPDATA%\hermes`; POSIX: `~/.hermes` | Base directory for Hermes state (affects all paths) |
| `HERMES_CONFIG_PATH` | `$HERMES_HOME/config.yaml` | Path to Hermes config file |
| `HERMES_WEBUI_SERVER_CWD` | *(unset)* | Working directory for the server process. Defaults to the agent dir; point it at a writable workspace when the agent dir is read-only so fallback relative writes land somewhere writable |
| `HERMES_WEBUI_VISIBLE_SESSION_LIMIT` | `20` | Size of the sidebar's interactive recency window (how many recent non-cron/webhook sessions are listed). Also bounds how many delegated subagent children can nest at once, since a child only renders when its row wins a slot in the window — raise it for wide fan-outs. Non-integer or non-positive values fall back to the default. Values above 200 are clamped. Resolved before profile init, so a profile `.env` cannot override it |
| `HERMES_WEBUI_AGENT_CACHE_MAX` | `25` | Max live agent instances kept warm in the in-memory LRU. Each pins a full conversation transcript, so this is the dominant lever on resident memory — lower it on installs with many long sessions to cap RAM (at the cost of more cold reloads) |
| `HERMES_WEBUI_SESSIONS_MAX` | `100` | Legacy operator override for the max compact `Session` objects held in the in-memory LRU. Prefer the `webui.sessions_cache_max` key in `config.yaml` (which takes precedence); this env var remains a fallback. Bounds resident memory so long-running installs cannot accumulate every session ever touched and eventually crash (#4765/#2233/#4633). Eviction only ever drops clean, persisted, non-active sessions; an evicted session lazily reloads from its JSON sidecar on next access |
