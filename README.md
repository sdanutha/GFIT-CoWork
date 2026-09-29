# GFIT-CoWork

GFIT-CoWork is a multi-user web workspace for working with
[Hermes Agent](https://hermes-agent.nousresearch.com/), run on a shared server
for one GFIT team. Each person logs in with their company AD account and works
in their own Hermes Agent Profile; the team's Admin runs the server, the API key
and the list of Profiles. See [CONTEXT.md](CONTEXT.md) for the vocabulary and
[docs/adr/](docs/adr/) for the design decisions.

GFIT-CoWork is a hard fork of
[hermes-webui](https://github.com/nesquena/hermes-webui) (MIT License), taken at
`c296673e`. Changes are no longer merged from upstream
([ADR 0001](docs/adr/0001-hard-fork-from-hermes-webui.md)). The original
copyright notice is kept in [LICENSE](LICENSE). Environment variables keep their
upstream `HERMES_WEBUI_*` names.

### Login with an employee ID

A User signs in with their employee ID (`521740`, `GFIT\521740` or
`521740@gfit.co.th` all work) and password. The password is checked by the
**Directory**; GFIT-CoWork never stores or logs it. Login succeeds only when the
Directory accepts the password **and** a Profile named after the employee ID
exists. The session is then bound to that Profile. An employee ID on the Admin
list logs in to the `default` Profile as the **Admin** instead.

| Variable | Meaning |
| --- | --- |
| `HERMES_WEBUI_DIRECTORY` | Which Directory to use. Unset turns Directory login off. `ldap` uses the company AD; `memory` uses the in-memory Directory. Any other value refuses every login. |
| `HERMES_WEBUI_ADMIN_USERS` | Comma-separated employee IDs of the Deployment's Admins, e.g. `521740,671278`. |
| `HERMES_WEBUI_LDAP_URL` | For `ldap`: `ldaps://ad-host` or `ldap://ad-host` with `HERMES_WEBUI_LDAP_STARTTLS=1`. Plain LDAP is refused. |
| `HERMES_WEBUI_LDAP_BIND_FORMAT`, `HERMES_WEBUI_LDAP_DOMAIN` | For `ldap`: `upn` (`521740@domain`) or `domain` (`DOMAIN\521740`), plus the domain. |
| `HERMES_WEBUI_LDAP_BASE_DN`, `HERMES_WEBUI_LDAP_USER_FILTER` | For `ldap`: where and how to look the user up to read `displayName` (filter default `(sAMAccountName={username})`). |
| `HERMES_WEBUI_LDAP_CA_CERT` | For `ldap`: the CA certificate of the AD server, when the system does not trust it. |
| `HERMES_WEBUI_DIRECTORY_USERS` | For `memory`: a JSON file standing in for AD, e.g. `{"521740": {"password": "dev-only", "display_name": "Somchai Jaidee"}}`. For tests and local development only. |

Wrong-password attempts are rate-limited per IP (5 per minute). Behind a
reverse proxy, set `HERMES_WEBUI_TRUST_FORWARDED_FOR=1` (and
`HERMES_WEBUI_TRUSTED_PROXY_CIDRS` for a proxy not on loopback) so attempts are
counted per person's forwarded address, not per proxy. When AD cannot
be reached, login says the directory is unavailable rather than that the
password is wrong. For local development, `dev/mock-ldap/` runs an OpenLDAP
stand-in (see its README).

The Directory is the only way in, and whether login is on depends on it alone.
The upstream login methods (the shared `HERMES_WEBUI_PASSWORD` or Settings
password, passkeys, OIDC and the trusted header) are removed: a leftover
setting of theirs neither turns login on nor lets anyone in, and startup
reports it as ignored. A stored upstream password hash or passkey file is left
on disk and ignored. The trusted proxy settings
(`HERMES_WEBUI_TRUST_FORWARDED_FOR`, `HERMES_WEBUI_TRUSTED_PROXY_CIDRS`) stay
for the rate limit.

On a network address (anything but loopback, e.g. `0.0.0.0` in a container) the
server does not start without a Directory: it exits and names the Directory
settings, so a Deployment is never served with login off. On the loopback
address with no Directory, the server starts with login off, for local
development, and says so at startup.

### Users and the Admin

A **User** works only in their own Profile. Every request runs in the Profile
bound to their session, whatever Profile the client names; naming another one
is refused, and a User cannot switch Profiles. A User's Workspaces live in
`<Profile>/workspace`, created at first login. Registering a Workspace outside
it, or any file operation that resolves outside it (through `..` or a symlink),
is refused.

The **Admin** is not confined and alone may use the server-level features:
terminal, changing workspace git, extensions, shutdown and reload,
server logs, YOLO mode, providers, models and MCP servers, Settings (they are
shared by the whole Deployment), onboarding, gateway control, Profile
management and public share links. The server refuses these to Users with
403 (`api/access.py` lists what a User may call; anything else is refused),
and the web UI hides their menus.

### Managing Profiles

The Admin adds a colleague in the Profiles panel by creating a Profile named
after their employee ID, with an optional display name. Each Profile is listed
as "name (ID)" (or just the ID) with its status and last login. The Admin can
**disable** a Profile — the person is signed out at once and later logins are
told their access is suspended, but their data stays — **enable** it again, or
**delete** it permanently (the Profile and its record) after confirming.

Display name, status and last login live in the **Profile roster**
(`gfit_roster.json` in the state directory), not in the Hermes Profile config.

### Name and Sign Out

After login the Profile chip shows who is signed in as "name (ID)", e.g.
"สมชาย ใจดี (521740)". Every login takes the name from the Directory and saves
it in the Profile roster. Until someone's first login, their chip shows the
name the Admin typed, or just the ID if the Admin typed none. `/api/auth/status`
sends `user`, `display_name` and `label`. Clicking the chip opens a menu with
the name and **Sign Out**, which ends that browser's session only.

### Deploying for a Team

`deploy/` is the Deployment kit: one Docker Compose file and one config file
per Team (Hermes Agent + GFIT-CoWork), plus a Caddy reverse proxy that serves
every Deployment on the server over HTTPS, each on its own hostname. The
step-by-step guide, including how to add a Team, add a User and run a pilot,
is [deploy/README.md](deploy/README.md).

The rest of this README is the upstream Hermes WebUI documentation, kept for
reference.

---

## About Hermes Web UI (upstream)

[Hermes Agent](https://hermes-agent.nousresearch.com/) is a sophisticated autonomous agent that lives on your server, accessed via a terminal or messaging apps, that remembers what it learns and gets more capable the longer it runs.

Hermes WebUI is a lightweight, dark-themed web app interface in your browser for [Hermes Agent](https://hermes-agent.nousresearch.com/).
Full parity with the CLI experience - everything you can do from a terminal, you can do from this UI. No build step, no framework, no bundler. Just Python and vanilla JS.

Layout: three-panel. Left sidebar for sessions and navigation, center for chat,
right for workspace file browsing. Model, profile, and workspace controls live in
the **composer footer** — always visible while composing. A circular context ring
shows token usage at a glance. All settings and session tools are in the
**Hermes Control Center** (launcher at the sidebar bottom).

Setup Hermes so you can access it natively on every device:

<img width="1467" height="881" alt="image" src="https://github.com/user-attachments/assets/9a72cdf3-a5b4-45ed-a836-a715ce46287e" />

<table>
  <tr>
    <td width="50%" align="center">
      <img width="2940" height="1848" alt="Light mode with full profile support" src="https://github.com/user-attachments/assets/4ef3a59c-7a66-4705-b4e7-cb9148fe4c47" />
      <br /><sub>Light mode with full profile support</sub>
    </td>
    <td width="50%" align="center">
      <img alt="Customize your settings" src="https://github.com/user-attachments/assets/941f3156-21e3-41fd-bcc8-f975d5000cb8" />
      <br /><sub>Customize your settings</sub>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td width="50%" align="center">
      <img alt="Workspace file browser with inline preview" src="docs/images/ui-workspace.png" />
      <br /><sub>Workspace file browser with inline preview</sub>
    </td>
    <td width="50%" align="center">
      <img alt="Session projects, tags, and tool call cards" src="docs/images/ui-sessions.png" />
      <br /><sub>Session projects, tags, and tool call cards</sub>
    </td>
  </tr>
</table>

This gives you nearly **1:1 parity with Hermes CLI from a convenient web UI** which you can access securely through an SSH tunnel from your Hermes setup. Single command to start this up, and a single command to SSH tunnel for access on your computer. Every single part of the web UI uses your existing Hermes agent and existing models, without requiring any additional setup.

---

## Contents

[<img width="750" alt="image" src="https://github.com/user-attachments/assets/7e9544a7-ba47-4fc7-8142-1d9d16b17065" />
](https://get-hermes.ai/setup/) 

- [Why Hermes](#why-hermes) — what it is and how it compares
- [Quick start](#quick-start) — clone + `bootstrap.py` / `start.sh` / `ctl.sh`
- [Features](#features) — chat, sessions, workspace, voice, profiles, security, themes, panels, mobile
- [Configuration & access](#configuration--access) — auto-discovery, overrides, remote/Tailscale/phone, manual launch
- [Docker](#docker) — the `deploy/` kit, one Deployment per Team
- [Running tests](#running-tests)
- [Architecture](#architecture) — backend/frontend layout, state dir
- [Docs](#docs) — the full documentation index

---

## Why Hermes

Most AI tools reset every session. They don't know who you are, what you worked on, or what
conventions your project follows. You re-explain yourself every time.

Hermes retains context across sessions, runs scheduled jobs while you're offline, and gets
smarter about your environment the longer it runs. It uses your existing Hermes agent setup,
your existing models, and requires no additional configuration to start.

What makes it different from other agentic tools:

- **Persistent memory** — user profile, agent notes, and a skills system that saves reusable
  procedures; Hermes learns your environment and does not have to relearn it
- **Self-hosted scheduling** — cron jobs that fire while you're offline and deliver results to
  Telegram, Discord, Slack, Signal, email, and more
- **10+ messaging platforms** — the same agent available in the terminal is reachable from your phone
- **Self-improving skills** — Hermes writes and saves its own skills automatically from experience;
  no marketplace to browse, no plugins to install
- **Provider-agnostic** — OpenAI, Anthropic, Google, DeepSeek, OpenRouter, and more
- **Orchestrates other agents** — can spawn Claude Code or Codex for heavy coding tasks and bring
  the results back into its own memory
- **Self-hosted** — your conversations, your memory, your hardware

**vs. the field** *(landscape is actively shifting)*:

| | OpenClaw | Claude Code | Codex CLI | OpenCode | Hermes |
|---|---|---|---|---|---|
| Persistent memory (auto) | Yes | Partial† | Partial | Partial | Yes |
| Scheduled jobs (self-hosted) | Yes | No‡ | No | No | Yes |
| Messaging app access | Yes (15+ platforms) | Partial (Telegram/Discord preview) | No | No | Yes (10+) |
| Web UI (self-hosted) | Dashboard only | No | No | Yes | Yes |
| Self-improving skills | Partial | No | No | No | Yes |
| Python / ML ecosystem | No (Node.js) | No | No | No | Yes |
| Provider-agnostic | Yes | No (Claude only) | Yes | Yes | Yes |
| Open source | Yes (MIT) | No | Yes | Yes | Yes |

† Claude Code has CLAUDE.md / MEMORY.md project context and rolling auto-memory, but not full automatic cross-session recall  
‡ Claude Code has cloud-managed scheduling (Anthropic infrastructure) and session-scoped `/loop`; no self-hosted cron

**The closest competitor is OpenClaw** — both are always-on, self-hosted, open-source agents
with memory, cron, and messaging. The key differences: Hermes writes and saves its own skills
automatically as a core behavior (OpenClaw's skill system centers on a community marketplace);
Hermes is more stable across updates (OpenClaw has documented release regressions and ClawHub
has had security incidents involving malicious skills); and Hermes runs natively in the Python
ecosystem.

---

## Quick start

Run the repo bootstrap:

```bash
git clone https://github.com/nesquena/hermes-webui.git hermes-webui
cd hermes-webui
python3 bootstrap.py
```

Or keep using the shell launcher:

```bash
./start.sh
```

For self-hosted VM or homelab installs, `ctl.sh` wraps the common daemon lifecycle commands without requiring `fuser` or `pkill`:

```bash
./ctl.sh start              # background daemon, PID at ~/.hermes/webui.pid
./ctl.sh status             # PID, uptime, bound host/port, log path, /health
./ctl.sh logs --lines 100   # tail ~/.hermes/webui.log
./ctl.sh restart
./ctl.sh stop
```

`ctl.sh start` runs the bootstrap in foreground/no-browser mode behind the daemon wrapper, writes logs to `~/.hermes/webui.log`, and respects `.env` plus inline overrides such as `HERMES_WEBUI_HOST=0.0.0.0 ./ctl.sh start`.

> **Stopping the server.** Each launch method has its own stop path because only `ctl.sh start` writes a PID file (`~/.hermes/webui.pid`):
>
> | Launch method | How to stop |
> |---|---|
> | `python3 bootstrap.py` | **Ctrl-C** in the terminal (runs in the foreground) |
> | `./ctl.sh start` | `./ctl.sh stop` (sends SIGTERM, waits, then SIGKILL) |
> | Detached `bootstrap.py` (no `--foreground`) or `./start.sh` | Find the PID via `lsof -i :8787` (or `ss -tlnp`) and `kill` it |
>
> `./ctl.sh stop` cannot stop a server launched by `bootstrap.py` or `start.sh` directly — it only manages processes it started itself.

> **How chat runs by default.** WebUI runs the Hermes agent in-process, reading
> your `HERMES_HOME` config directly. It does not connect to an external
> Hermes/agent OpenAI-compatible API server to run chat. `HERMES_API_URL` is only
> read by the Tasks/cron health probe and does not route chat.
>
> Two options if you run an external endpoint:
>
> 1. **Use its models as a chat provider** (supported today): add it in
>    **Settings → Providers** as a custom OpenAI-compatible provider with
>    `base_url = http://127.0.0.1:8642/v1` and your bearer token.
> 2. **Route chat through a Hermes Gateway API server** (supported today via
>    `HERMES_WEBUI_CHAT_BACKEND=gateway`): see [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md).
>    Full agent-loop delegation is not yet shipped; tracked in [#1925](https://github.com/nesquena/hermes-webui/issues/1925).

### Advanced: dynamic recall prefill & Gateway-backed chat

Two optional, self-hosted-deployment features — attaching dynamic **session-recall prefill** to browser turns (Joplin/Obsidian/Notion/llm-wiki routers), and routing browser chat through a running **Hermes Gateway** — are documented in [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md). Most users need neither.

The bootstrap will:

1. Detect Hermes Agent and, if missing, attempt the official installer (`curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`).
2. Find or create a Python environment with the WebUI dependencies.
3. Start the web server and wait for `/health`.
4. Open the browser unless you pass `--no-browser`.
5. Drop you into a first-run onboarding wizard inside the WebUI.

> Native Windows is not supported for this bootstrap yet. Use Linux, macOS, or WSL2.

A community-maintained native Windows setup is documented at [@markwang2658/hermes-windows-native-guide](https://github.com/markwang2658/hermes-windows-native-guide) (companion setup repo: [@markwang2658/hermes-windows-native](https://github.com/markwang2658/hermes-windows-native)). Notes from the community report in [#1952](https://github.com/nesquena/hermes-webui/issues/1952):

- **Memory:** community-measured ~330 MB native vs ~1080 MB with WSL2+Docker (varies by configuration).
- **What works:** chat, workspace browser, session management, all themes.
- **Known limitations:** some POSIX-style file paths surface in the workspace browser; bash-assuming agent tools may not work natively.
- **Native Windows setup:** install Python 3.11+, then from the hermes-agent root in PowerShell: `python -m venv venv` → `pip install -r requirements.txt` → `pwsh .\start.ps1` (it auto-discovers `venv\Scripts\python.exe`).
- **WSL2 relationship:** not a prerequisite — a WSL2-built venv (`venv/bin/python`, ELF) isn't invokable by native Windows Python, so use the native setup above. WSL2 stays useful as a parallel install if you want the full `bootstrap.py` + Linux runtime.

If provider setup is still incomplete after install, the onboarding wizard will point you to finish it with `hermes model` instead of trying to replicate the full CLI setup in-browser.
For a step-by-step walkthrough of the wizard, provider choices, local model server Base URLs, and safe re-runs, see [`docs/onboarding.md`](docs/onboarding.md).
If an AI assistant is helping with install, reinstall, bootstrap, provider setup, or first-run support, have it read [`docs/onboarding-agent-checklist.md`](docs/onboarding-agent-checklist.md) before running commands or inspecting logs.

---

## Features

### Chat and agent
- Streaming responses via SSE (tokens appear as they are generated)
- Multi-provider model support -- any Hermes API provider (OpenAI, Anthropic, Google, DeepSeek, Nous Portal, OpenRouter, MiniMax, Xiaomi MiMo, Z.AI); dynamic model dropdown populated from configured keys
- Send a message while one is processing -- it queues automatically
- Edit any past user message inline and regenerate from that point
- Retry the last assistant response with one click
- Cancel a running task directly from the composer footer (Stop button next to Send)
- Tool call cards inline -- each shows the tool name, args, and result snippet; expand/collapse all toggle for multi-tool turns
- Subagent delegation cards -- child agent activity shown with distinct icon and indented border
- Mermaid diagram rendering inline (flowcharts, sequence diagrams, gantt charts)
- Thinking/reasoning display -- collapsible gold-themed cards for Claude extended thinking and o3 reasoning blocks
- Approval card for dangerous shell commands (allow once / session / always / deny)
- SSE auto-reconnect on network blips (SSH tunnel resilience)
- File attachments persist across page reloads and are stored outside the active workspace by default (`~/.hermes/webui/attachments/<session_id>/`, or `HERMES_WEBUI_ATTACHMENT_DIR/<session_id>/` when configured)
- Message timestamps (HH:MM next to each message, full date on hover)
- Code block copy button with "Copied!" feedback
- Syntax highlighting via Prism.js (Python, JS, bash, JSON, SQL, and more)
- Safe HTML rendering in AI responses (bold, italic, code converted to markdown)
- rAF-throttled token streaming for smoother rendering during long responses
- Context usage indicator in composer footer -- token count, cost, and fill bar (model-aware)

### Sessions
- Create, rename, duplicate, delete, search by title and message content
- Session actions via `⋯` dropdown per session — pin, move to project, archive, duplicate, delete
- Pin/star sessions to the top of the sidebar (gold indicator)
- Archive sessions (hide without deleting, toggle to show)
- Session projects -- named groups with colors for organizing sessions; delegated subagent sessions have no project of their own and follow their nearest ancestor's project in the project filter and the Unassigned chip; forks and other child sessions keep their own project, so a fork moved to "No project" stays Unassigned
- Session tags -- add #tag to titles for colored chips and click-to-filter
- Grouped by Today / Yesterday / Earlier in the sidebar (collapsible date groups)
- Download as Markdown transcript, full JSON export, or import from JSON
- Create a public read-only share link for the active conversation from the Control Center; shared pages show a sanitized transcript snapshot without workspace, profile, or live controls
- Sessions persist across page reloads and SSH tunnel reconnects
- Browser tab title reflects the active session name
- CLI session bridge -- CLI sessions from hermes-agent's SQLite store appear in the sidebar with a gold "cli" badge; click to import with full history and reply normally
- Token/cost display -- input tokens, output tokens, estimated cost shown per conversation (toggle in Settings or `/usage` command)

### Workspace file browser
- Directory tree with expand/collapse (single-click toggles, double-click navigates)
- Breadcrumb navigation with clickable path segments
- Preview text, code, Markdown (rendered), and images inline
- Chat links using `workspace://path/to/file` open files in the right-side preview pane
- Edit, create, delete, and rename files; create folders
- Binary file download (auto-detected from server)
- File preview auto-closes on directory navigation (with unsaved-edit guard)
- Git detection -- branch name and dirty file count badge in workspace header
- Right panel is drag-resizable
- Syntax highlighted code preview (Prism.js)

### Voice input
- Microphone button in the composer (Web Speech API)
- Tap to record, tap again or send to stop
- Live interim transcription appears in the textarea
- Auto-stops after ~2s of silence
- Appends to existing textarea content (doesn't replace)
- Hidden when browser doesn't support Web Speech API (Chrome, Edge, Safari)

### Profiles
- Profile chip in the **composer footer** -- dropdown showing all profiles with gateway status and model info
- Gateway status dots (green = running), model info, skill count per profile
- Profiles management panel -- create, switch, and delete profiles from the sidebar
- Clone config from active profile on create
- Optional custom endpoint fields on create -- Base URL and API key written into the profile's `config.yaml` at creation time, so Ollama, LMStudio, and other local endpoints can be configured without editing files manually
- Seamless switching -- no server restart; reloads config, skills, memory, cron, models
- Per-session profile tracking (records which profile was active at creation)

### Authentication and security
- Login through the company Directory (see "Login" above) -- off on the loopback address with no Directory, for local development
- Installed PWAs work best with WebUI's own login. Reverse proxies are supported, but proxy basic auth can block the service-worker update fetches an installed app needs and leave it on a blank screen after an update; see `docs/troubleshooting.md` for recovery steps.
- Signed HMAC HTTP-only cookie with 24h TTL
- Minimal dark-themed login page at `/login`
- Security headers on all responses (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)
- 20MB POST body size limit
- CDN resources pinned with SRI integrity hashes

### Themes
- Appearance is split into two axes: Theme (`system`, `dark`, `light`) and Skin
  (`default`, `ares`, `mono`, `slate`, `poseidon`, `sisyphus`, `charizard`,
  `sienna`, `catppuccin`, `nous`, `geist-contrast` / Geist Contrast)
- Switch via Settings -> Appearance (instant live preview) or `/theme <theme-or-skin>`
- Persists across reloads (server-side in settings.json + localStorage for flicker-free loading)
- Skins use `data-skin` plus CSS variables; dark mode resolves through the
  `.dark` class, not a `data-theme` custom-theme axis — see [THEMES.md](THEMES.md)

### Settings and configuration
- **Hermes Control Center** (sidebar launcher button) -- Conversation tab (export/import/clear), Preferences tab (model, send key, theme, language, all toggles), System tab (version, sign out)
- Send key: Enter (default) or Ctrl/Cmd+Enter
- Send-key on touch devices: plain Enter inserts a newline on phones (iPhone/iPod, Android phones) and on tablets / iPadOS / touch-capable Macs that only expose a coarse pointer (no attached hardware keyboard), matching the software keyboard's return key. Devices that report a fine pointer (for example, a tablet with a hardware keyboard) keep the configured physical-keyboard send-key behavior. The configured shortcut and the Send button remain available for sending.
- Show/hide CLI sessions toggle (enabled by default)
- Token usage display toggle (off by default, also via `/usage` command)
- Control Center always opens on the Conversation tab; resets on close
- Unsaved changes guard -- discard/save prompt when closing with unpersisted changes
- Cron completion alerts -- toast notifications and unread badges scoped to the active profile on the Tasks tab and session sidebar
- Background agent error alerts -- banner when a non-active session encounters an error
- Approval and clarification browser alerts notify once per pending owner when its session is not actively viewed. Local approval dismissal hides attention without resolving the prompt; resolution, replacement, and cancellation retire that owner's alert state. Denied or failed notification delivery can retry while the prompt remains pending.

### Slash commands
- Type `/` in the composer for autocomplete dropdown
- Plain skills match case-insensitive keywords in their name or description; built-in, agent/plugin, and bundle commands keep prefix matching and take precedence over a same-slug skill
- Built-in: `/help`, `/clear`, `/compress [focus topic]`, `/compact` (alias), `/model <name>`, `/workspace <name>`, `/new`, `/usage`, `/theme`
- Arrow keys navigate, Tab/Enter select, Escape closes
- Unrecognized commands pass through to the agent

### Panels
- **Chat** -- session list, search, pin, archive, projects, new conversation
- **Tasks** -- view, create, edit, run, pause/resume, delete cron jobs; run history; completion alerts
- **Skills** -- list all skills by category, search, preview, create/edit/delete; linked files viewer
- **Memory** -- view and edit MEMORY.md and USER.md inline
- **Profiles** -- create, switch, delete agent profiles; clone config
- **Todos** -- live task list from the current session
- **Spaces** -- add, rename, remove workspaces; quick-switch from topbar

### Mobile responsive
- Hamburger sidebar -- slide-in overlay on mobile (<640px)
- Sidebar top tabs stay available on mobile; no fixed bottom nav stealing chat height
- Files slide-over panel from right edge
- Touch targets minimum 44px on all interactive elements
- Full-height chat/composer on phones without bottom-nav spacing
- Desktop layout completely unchanged

---

## Configuration & access

`start.sh` auto-detects almost everything; the subsections below cover the knobs for when it can't, and how to reach the UI remotely.

### What start.sh discovers automatically

| Thing | How it finds it |
|---|---|
| Hermes agent dir | `HERMES_WEBUI_AGENT_DIR`, then known checkout paths, the `hermes` launcher on `PATH`, and finally the installed `run_agent` module exposed by `HERMES_WEBUI_PYTHON` |
| Python executable | Agent venv first, then `.venv` in this repo, then system `python3` |
| State directory | `HERMES_WEBUI_STATE_DIR` env, then `$HERMES_HOME/webui` (Windows default `%LOCALAPPDATA%\hermes\webui`, POSIX default `~/.hermes/webui`) |
| Default workspace | `HERMES_WEBUI_DEFAULT_WORKSPACE` env, then `~/workspace`, then state dir |
| Port | `HERMES_WEBUI_PORT` env or first argument, default `8787` |

If discovery finds everything, nothing else is required.

---

### Overrides (only needed if auto-detection misses)

```bash
export HERMES_WEBUI_AGENT_DIR=/path/to/hermes-agent
export HERMES_WEBUI_PYTHON=/path/to/python
export HERMES_WEBUI_PORT=9000
export HERMES_WEBUI_AUTO_INSTALL=1  # enable auto-install of agent deps (disabled by default)
./start.sh
```

Or inline:

```bash
HERMES_WEBUI_AGENT_DIR=/custom/path ./start.sh 9000
```

Full list of environment variables:

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEBUI_AGENT_DIR` | auto-discovered | Path to the Hermes Agent source or installed module root |
| `HERMES_WEBUI_PYTHON` | auto-discovered | Python executable |
| `HERMES_WEBUI_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for all IPv4, `::` for all IPv6, `::1` for IPv6 loopback) |
| `HERMES_WEBUI_PORT` | `8787` | Port |
| `HERMES_WEBUI_STATE_DIR` | `$HERMES_HOME/webui` (Windows default `%LOCALAPPDATA%\hermes\webui`, POSIX default `~/.hermes/webui`) | Where sessions and state are stored. **Note (upgrade):** the default now follows `HERMES_HOME` — if you previously relocated `HERMES_HOME` to a non-default base **without** setting `HERMES_WEBUI_STATE_DIR`, your WebUI state now resolves to `$HERMES_HOME/webui` instead of the old platform-default `~/.hermes/webui`. To keep using the old location, set `HERMES_WEBUI_STATE_DIR` to it (or move the directory). Installs with `HERMES_HOME` unset or at the default base are unaffected. |
| `HERMES_WEBUI_SETTINGS_FILE` | `<state dir>/settings.json` | Optional path for this instance's WebUI settings file (sessions, workspaces and projects stay in the state directory). Read once at startup, so restart after changing it |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | `~/workspace` | Default workspace |
| `HERMES_WEBUI_DEFAULT_MODEL` | *(provider default)* | Optional model override; leave unset to use the active Hermes provider default |
| `HERMES_WEBUI_CSP_CONNECT_EXTRA` | *(unset)* | Optional space-separated `http(s)://` or `ws(s)://` origins to append to the enforced and report-only CSP `connect-src` directives for trusted reverse-proxy, tunnel, or extension sidecar deployments |
| `HERMES_WEBUI_SSE_CHUNKED` | *(unset)* | Set truthy (`1`/`true`/`yes`/`on`) to send SSE with `Transfer-Encoding: chunked`. Needed behind buffering reverse proxies (e.g. `jupyter-server-proxy`) that otherwise buffer the whole stream; harmless but unnecessary for directly-served deployments |
| `HERMES_WEBUI_EXTENSION_DIR` | *(unset)* | Optional local directory served at `/extensions/`; must point to an existing directory before extension injection is enabled |
| `HERMES_WEBUI_EXTENSION_MANIFEST` | *(unset)* | Optional relative JSON manifest inside `HERMES_WEBUI_EXTENSION_DIR` listing bundled scripts/styles to inject; see [WebUI Extensions](docs/EXTENSIONS.md) |
| `HERMES_WEBUI_EXTENSION_SCRIPT_URLS` | *(unset)* | Optional comma-separated same-origin script URLs to inject; appended after manifest scripts; see [WebUI Extensions](docs/EXTENSIONS.md) |
| `HERMES_WEBUI_EXTENSION_STYLESHEET_URLS` | *(unset)* | Optional comma-separated same-origin stylesheet URLs to inject; appended after manifest stylesheets; see [WebUI Extensions](docs/EXTENSIONS.md) |
| `HERMES_HOME` | Windows: `%LOCALAPPDATA%\hermes`; POSIX: `~/.hermes` | Base directory for Hermes state (affects all paths) |
| `HERMES_CONFIG_PATH` | `$HERMES_HOME/config.yaml` | Path to Hermes config file |
| `HERMES_WEBUI_SERVER_CWD` | *(unset)* | Working directory for the server process. Defaults to the agent dir; point it at a writable workspace when the agent dir is read-only so fallback relative writes land somewhere writable |
| `HERMES_WEBUI_VISIBLE_SESSION_LIMIT` | `20` | Size of the sidebar's interactive recency window (how many recent non-cron/webhook sessions are listed). Also bounds how many delegated subagent children can nest at once, since a child only renders when its row wins a slot in the window — raise it for wide fan-outs. Non-integer or non-positive values fall back to the default. Values above 200 are clamped. Resolved before profile init, so a profile `.env` cannot override it |
| `HERMES_WEBUI_AGENT_CACHE_MAX` | `25` | Max live agent instances kept warm in the in-memory LRU. Each pins a full conversation transcript, so this is the dominant lever on resident memory — lower it on installs with many long sessions to cap RAM (at the cost of more cold reloads) |
| `HERMES_WEBUI_SESSIONS_MAX` | `100` | Legacy operator override for the max compact `Session` objects held in the in-memory LRU. Prefer the `webui.sessions_cache_max` key in `config.yaml` (which takes precedence); this env var remains a fallback. Bounds resident memory so long-running installs cannot accumulate every session ever touched and eventually crash (#4765/#2233/#4633). Eviction only ever drops clean, persisted, non-active sessions; an evicted session lazily reloads from its JSON sidecar on next access |

Extension deployments can inspect sanitized, authenticated diagnostics at `GET /api/extensions/status`; see [WebUI Extensions](docs/EXTENSIONS.md#diagnostics).

---

### Remote access (SSH tunnel, Tailscale, phone)

The server binds to `127.0.0.1` by default. To reach it from another machine,
use an SSH tunnel (`ssh -N -L 8787:127.0.0.1:8787 user@host`, which `start.sh`
prints for you over SSH) or, on a single-operator or access-restricted tailnet,
use the preferred [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
flow, which keeps WebUI on loopback behind tailnet-only HTTPS. Direct access to
`http://<server-tailscale-ip>:8787` with `HERMES_WEBUI_HOST=0.0.0.0` and a
Directory configured is a fallback when Serve is unavailable.

### Manual launch (without start.sh)

If you prefer to launch the server directly:

```bash
cd /path/to/hermes-agent          # or wherever sys.path can find Hermes modules
HERMES_WEBUI_PORT=8787 venv/bin/python /path/to/hermes-webui/server.py
```

Note: use the agent venv Python (or any Python environment that has the Hermes agent dependencies installed). System Python will be missing `openai`, `httpx`, and other required packages.

Health check:

```bash
curl http://127.0.0.1:8787/health
```

---

## Docker

GFIT-CoWork runs in Docker one way: the Deployment kit in [`deploy/`](deploy/),
one Deployment per Team (Hermes Agent + GFIT-CoWork) behind an HTTPS reverse
proxy ([ADR 0005](docs/adr/0005-supported-ways-to-run.md)). Build the image once
per server, then add each Team:

```bash
docker build -t gfit-cowork:latest /opt/gfit-cowork/src
```

The step-by-step guide is [`deploy/README.md`](deploy/README.md). For the image
itself (volumes, UID/GID, the read-only agent source, the gateway, upgrades and
common failure modes), see [`docs/docker.md`](docs/docker.md).

---

## Running tests

Tests discover the repo and the Hermes agent dynamically -- no hardcoded paths.
Use the repo test runner so local runs do not accidentally use an unsupported
system Python. It creates/uses `.venv` with Python 3.11, 3.12, or 3.13 and
installs the dev test dependencies from `requirements-dev.txt` when missing.

```bash
cd hermes-webui
./scripts/test.sh
```

Pass normal pytest arguments after the script for focused runs:

```bash
./scripts/test.sh tests/test_regressions.py -v
```

Or seed the repo `.venv` from an explicit supported base interpreter:

```bash
HERMES_WEBUI_TEST_PYTHON=/path/to/python3.12 ./scripts/test.sh tests/ -v
```

The override selects the Python used to create or rebuild `.venv`; dependencies
are still installed into the repo-local virtual environment, not into the
system/Homebrew interpreter.

Tests run against an isolated server with a separate state directory.
Production data and real cron jobs are never touched. Current snapshot:
**~11,500 tests collected** across **~1,150 test files**, run in CI on Python 3.11,
3.12, and 3.13 (3 parallel shards each).

---

## Architecture

No build step, no framework, no bundler — a Python standard-library HTTP server
and vanilla JS. The backend lives in `api/`, the frontend in `static/`.

**Backend (`api/`)**

```
server.py         HTTP routing shell + auth middleware
api/
  auth.py         Session store and cookie, CSRF, signed Profile cookie, per-request gate
  login.py        Directory login, rate limit, startup login check
  trusted_proxy.py  Client address behind a trusted reverse proxy
  config.py       Discovery, globals, model detection, reloadable config
  helpers.py      HTTP helpers, security headers
  models.py       Session model + CRUD + CLI/state.db bridge
  onboarding.py   First-run onboarding wizard, OAuth provider support
  profiles.py     Profile state management, hermes_cli wrapper
  routes.py       All GET + POST route handlers (if/elif dispatch, no decorators)
  state_sync.py   /insights sync — message_count to state.db
  streaming.py    SSE engine, run_agent, cancellation, compression
  version.py      Running GFIT-CoWork and Hermes Agent versions
  upload.py       Multipart parser, file upload handler
  workspace.py    File ops, workspace helpers, git detection
```

**Frontend (`static/`)**

```
index.html        HTML template
style.css         All CSS incl. mobile responsive, themes + skins
ui.js             DOM helpers, renderMd, tool cards, context indicator
workspace.js      File preview, file ops, git badge, central api() fetch wrapper
sessions.js       Session CRUD, collapsible groups, search, reload recovery
messages.js       send(), SSE handlers, live streaming, session recovery
panels.js         Cron, skills, memory, profiles, settings (Control Center)
commands.js       Slash command autocomplete
boot.js           Mobile nav, voice input, theme/skin boot, bfcache handler
```

**Tests + packaging**

```
tests/            Pytest suite (~11,500 tests; isolated server/state fixtures)
pyproject.toml    Standard build metadata plus the Ruff lint gate; checkout launch surface still centers on bootstrap.py / start.sh / ctl.sh
Dockerfile        python:3.12-slim container image
deploy/           The Deployment kit: Compose file, Team config example, Caddy proxy
.github/workflows/  CI: ruff + sharded pytest, browser smoke, Docker smoke,
                    multi-arch Docker build + GitHub Release on tag
```

State lives outside the repo at `~/.hermes/webui/` by default
(sessions, workspaces, settings, projects, last_workspace). Override with `HERMES_WEBUI_STATE_DIR`.
Full design notes and the endpoint catalog are in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Compatibility

The version shown in the WebUI runtime status is the **WebUI version only** (build/image/tag currently running). It is not a full compatibility map.

The WebUI is still coupled to Hermes Agent internals for runtime execution, provider/model access, and state/schema usage until the stable agent boundary work in [#1925](https://github.com/nesquena/hermes-webui/issues/1925) and [#2491](https://github.com/nesquena/hermes-webui/issues/2491) land. In practice, the WebUI imports Agent modules directly (`api/config.py`, `api/providers.py`, `api/streaming.py`) and reads Agent state layout directly, so version skew can cause import or behavior drift.

**Compatibility policy**
- WebUI release branches are tested against the matching Hermes Agent release available at that WebUI release time.
- **Upgrade both together**: upgrade or pin WebUI and hermes-agent together (same release train/version/date), especially before enabling production traffic.
- Running pinned older/newer combinations is **untested and unsupported** until the stable API boundary work in [#1925](https://github.com/nesquena/hermes-webui/issues/1925) / [#2491](https://github.com/nesquena/hermes-webui/issues/2491) is in place.
- Record the full `hermes-agent` + `hermes-webui` versions in issue reports when upgrade mismatches are suspected.

**Docker users**: pin both image tags (or corresponding pinned source revisions) rather than using `latest` on one side and a fixed tag on the other. When upgrading a Deployment, follow the agent-image upgrade procedure in [`docs/docker.md`](docs/docker.md) (which requires dropping the `hermes-agent-src` volume before recreating). The current source-boundary status is tracked in [`docs/rfcs/agent-source-boundary.md`](docs/rfcs/agent-source-boundary.md).

---

## Docs

**Start here**
- [`docs/onboarding.md`](docs/onboarding.md) — first-run wizard, provider setup, local model server Base URLs, and safe re-runs
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — diagnostic flows for common failures (e.g. "AIAgent not available")

**Using & customizing**
- [`THEMES.md`](THEMES.md) — theme + skin system, custom theme guide
- [`docs/workspace-git.md`](docs/workspace-git.md) — the workspace Git controls
- [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md) — administrator-controlled WebUI extension injection

**Deploying & operating**
- [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md) — optional dynamic recall-prefill and Gateway-backed browser chat for self-hosted deployments
- [`docs/docker.md`](docs/docker.md) — the GFIT-CoWork image in a Deployment: volumes, UID/GID, gateway, upgrades, common failures
- [`docs/onboarding-agent-checklist.md`](docs/onboarding-agent-checklist.md) — safety rules and pass/fail checks for assistant-led install/reinstall support

**Contributing & design**
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution style, PR expectations, and local verification
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design, all API endpoints, implementation notes
- [`TESTING.md`](TESTING.md) — manual browser test plan and automated coverage reference
- [`DESIGN.md`](DESIGN.md) — design tokens and the calm-console direction
- [`docs/UIUX-GUIDE.md`](docs/UIUX-GUIDE.md) — UI/UX principles sourced from the design docs and visual inventories
- [`docs/sse-streams.md`](docs/sse-streams.md) — cross-client SSE endpoint reference: session streaming, gateway SSE probe scope, heartbeats, and proxy behavior
- [`docs/CONTRACTS.md`](docs/CONTRACTS.md) — project contract/RFC/design index for contributors and agents
- [`docs/rfcs/README.md`](docs/rfcs/README.md) — RFC index for larger architecture and durability proposals

**Release history & plan**
- [`CHANGELOG.md`](CHANGELOG.md) — release notes per version

---

## Repo

```
git@github.com:nesquena/hermes-webui.git
```
