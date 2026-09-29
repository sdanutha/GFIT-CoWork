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

## What a User sees

A three-panel web app: sessions and navigation on the left, the chat with the
agent in the middle, and the Workspace file browser on the right. The model,
Profile and Workspace controls sit in the composer footer, and a context ring
shows token use. Sessions can be pinned, archived, grouped into projects,
searched and exported. The agent's tool calls, reasoning and file changes show
inline as cards. Voice input, light and dark themes, and a phone layout are
built in. There is no build step, no framework and no bundler: a Python
standard-library server and vanilla JavaScript.

## Running it locally

GFIT-CoWork runs in three ways ([ADR 0005](docs/adr/0005-supported-ways-to-run.md)):
the `deploy/` kit on a server (above), and two ways for development.

**Linux or macOS**, with Hermes Agent installed (the launcher finds it in
`~/.hermes/hermes-agent` or `HERMES_WEBUI_AGENT_DIR`):

```bash
./start.sh                  # foreground; or python3 bootstrap.py
./ctl.sh start              # background daemon, PID at ~/.hermes/webui.pid
./ctl.sh status             # PID, uptime, bound host/port, log path, /health
./ctl.sh logs --lines 100   # tail ~/.hermes/webui.log
./ctl.sh stop
```

**Native Windows**: `pwsh .\start.ps1` (it finds `venv\Scripts\python.exe` in the
Hermes Agent folder).

On the loopback address with no Directory, the server starts with login off.
To try login locally, run the development Directory in
[`dev/mock-ldap/`](dev/mock-ldap/) and point `HERMES_WEBUI_DIRECTORY=ldap` at it,
or use `HERMES_WEBUI_DIRECTORY=memory` with a `HERMES_WEBUI_DIRECTORY_USERS`
file. `.env` in the repo root is read at startup; see
[`.env.example`](.env.example) for the variables.

State lives outside the repo, in `~/.hermes/webui/` by default (sessions,
Workspaces, settings, projects, the Profile roster). Override it with
`HERMES_WEBUI_STATE_DIR`. For first-run onboarding and provider setup, see
[`docs/onboarding.md`](docs/onboarding.md); if an AI assistant is helping with
install or first-run support, have it read
[`docs/onboarding-agent-checklist.md`](docs/onboarding-agent-checklist.md) first.

By default GFIT-CoWork runs Hermes Agent in-process, reading the Profile's
config directly. Routing chat through a running Hermes Gateway instead is
described in [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md).

## Running tests

```bash
./scripts/test.sh                              # the whole suite
./scripts/test.sh tests/test_gfit04_admin_gate.py -v   # one file
```

The runner creates or reuses `.venv` with Python 3.11, 3.12 or 3.13 and installs
`requirements-dev.txt` when something is missing. `HERMES_WEBUI_TEST_PYTHON`
picks the base interpreter for `.venv`. Tests run against isolated servers with
their own state directories; real sessions and cron jobs are never touched.
CI runs the same suite on every pull request to `main` and `dev`. See
[`TESTING.md`](TESTING.md) for manual checks.

## Architecture

The backend is in `api/` and the frontend in `static/`; `server.py` is the
routing shell.

| Where | What |
| --- | --- |
| `api/routes.py` | Every GET and POST route handler |
| `api/access.py` | The Admin gate: what a User may call |
| `api/login.py`, `api/directory.py`, `api/ldap_directory.py` | Directory login and Admission |
| `api/session_ownership.py`, `api/workspace_policy.py` | Which sessions and paths a request may touch |
| `api/roster.py`, `api/profiles.py` | The Profile roster and Profile state |
| `api/streaming.py`, `api/models.py` | Running the agent and the session store |
| `api/version.py` | The running GFIT-CoWork and Hermes Agent versions |
| `static/*.js`, `static/index.html`, `static/style.css` | The web app |
| `deploy/` | The Deployment kit |

Design notes and the endpoint catalogue are in
[`ARCHITECTURE.md`](ARCHITECTURE.md); the vocabulary is in
[`CONTEXT.md`](CONTEXT.md) and the decisions in [`docs/adr/`](docs/adr/).

## Compatibility

GFIT-CoWork imports Hermes Agent's modules directly (`api/config.py`,
`api/providers.py`, `api/streaming.py`) and reads its state layout, so the two
must match.

- **Upgrade both together**: move GFIT-CoWork and Hermes Agent to versions
  tested together, especially before a Team uses them.
- **Docker**: pin both image tags (or the source revisions they are built from)
  rather than using `latest` on one side. When upgrading Hermes Agent in a
  Deployment, follow [`docs/docker.md`](docs/docker.md) and
  [`deploy/README.md`](deploy/README.md), which drop the agent source volume
  before recreating it.
- The boundary between the two is described in
  [`docs/rfcs/agent-source-boundary.md`](docs/rfcs/agent-source-boundary.md).
- When reporting an upgrade problem, give both versions (Settings → System).

## Docs

**Running a Deployment**
- [`deploy/README.md`](deploy/README.md) — adding a Team, adding a User, running a pilot
- [`docs/docker.md`](docs/docker.md) — the image: volumes, UID/GID, the gateway, upgrades, common failures
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — diagnostic flows for common failures
- [`docs/onboarding.md`](docs/onboarding.md) — first-run wizard and provider setup
- [`docs/onboarding-agent-checklist.md`](docs/onboarding-agent-checklist.md) — safety rules for assistant-led install support
- [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md) — Gateway-backed chat and recall prefill

**Using and customizing**
- [`THEMES.md`](THEMES.md) — themes and skins
- [`docs/workspace-git.md`](docs/workspace-git.md) — the Workspace Git controls
- [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md) — extensions from the Admin's extension folder

**Contributing and design**
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution style and PR expectations
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design and every API endpoint
- [`TESTING.md`](TESTING.md) — manual and automated verification
- [`DESIGN.md`](DESIGN.md) and [`docs/UIUX-GUIDE.md`](docs/UIUX-GUIDE.md) — design tokens and UI/UX principles
- [`docs/sse-streams.md`](docs/sse-streams.md) — the SSE endpoints
- [`docs/CONTRACTS.md`](docs/CONTRACTS.md) and [`docs/rfcs/README.md`](docs/rfcs/README.md) — contracts and RFCs
- [`CHANGELOG.md`](CHANGELOG.md) — GFIT-CoWork's release notes
