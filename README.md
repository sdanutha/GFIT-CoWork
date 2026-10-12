# GFIT-CoWork

GFIT-CoWork is a multi-user web workspace for working with
[Hermes Agent](https://hermes-agent.nousresearch.com/), run on a shared server
for one GFIT team. Each person logs in with their employee ID and company AD
password and works in their own Hermes Agent Profile. There is no Admin in the
web app: the team's **Operator** runs the server, the API key and the list of
Profiles from the server's shell
([ADR 0006](docs/adr/0006-no-admin-in-the-web-app.md)).

GFIT-CoWork is a hard fork of
[hermes-webui](https://github.com/nesquena/hermes-webui) (MIT License), taken at
`c296673e`. Changes are no longer merged from upstream
([ADR 0001](docs/adr/0001-hard-fork-from-hermes-webui.md)). The original
copyright notice is kept in [LICENSE](LICENSE). Environment variables keep their
upstream `HERMES_WEBUI_*` names.

The vocabulary (User, Operator, Profile, Deployment, Team, ...) is defined in
[CONTEXT.md](CONTEXT.md), and the design decisions are in [docs/adr/](docs/adr/).

## What a User sees

A three-panel web app: sessions on the left, the chat with the agent in the
middle, and the Workspace file browser on the right. Model and Workspace
controls sit in the composer footer, and the agent's tool calls, reasoning and
file changes show inline as cards. Sessions can be pinned, archived, grouped
into projects, searched and exported. Light and dark themes, voice input and a
phone layout are built in.

A User works only in their own Profile and its Workspaces; they cannot see or
switch to anyone else's. How login, Profiles and the Operator's command line
work is in [docs/operator.md](docs/operator.md) and
[docs/setup.md](docs/setup.md#login).

## Quick start (development)

You need Python 3 and Hermes Agent installed (the launcher finds it in
`~/.hermes/hermes-agent` or `HERMES_WEBUI_AGENT_DIR`). Use an isolated Hermes
home and state directory so a trial never touches your real `~/.hermes`, and the
in-memory Directory instead of the company AD:

```bash
mkdir -p ~/gfit-trial
cat > ~/gfit-trial/users.json <<'JSON'
{"521740": {"password": "trial-only", "display_name": "Trial User"}}

JSON
export HERMES_HOME=~/gfit-trial/.hermes
export HERMES_WEBUI_STATE_DIR=~/gfit-trial/webui
export HERMES_WEBUI_PORT=8789
export HERMES_WEBUI_DIRECTORY=memory
export HERMES_WEBUI_DIRECTORY_USERS=~/gfit-trial/users.json

hermes setup                                                     # provider and model for the default Profile
python3 -m api.operator_cli create 521740 --clone-from default   # a Profile for the test User
./start.sh                                                       # or: python3 bootstrap.py
```

Open `http://127.0.0.1:8789` and log in as `521740` / `trial-only`. The server
does not start without a Directory.

Other ways to run it ([ADR 0005](docs/adr/0005-supported-ways-to-run.md)):
`./ctl.sh start|stop|status|logs` runs it as a background daemon; on native
Windows use `pwsh .\start.ps1`; [`dev/mock-ldap/`](dev/mock-ldap/) is a stand-in
AD for testing the `ldap` Directory; and a Team's server uses the
[`deploy/`](deploy/README.md) kit. All settings are in
[docs/configuration.md](docs/configuration.md), and
[docs/setup.md](docs/setup.md) covers providers and the first Profile. If an AI
assistant is helping with install or Setup, have it read
[docs/setup-agent-checklist.md](docs/setup-agent-checklist.md) first.

## Repository layout

There is no build step, no framework and no bundler: a Python
standard-library server and vanilla JavaScript.

| Folder | What it holds |
| --- | --- |
| [`api/`](api/) | The backend. Route handlers (`routes.py`) and the route gate (`route_table.py`, `access.py`), Directory login, the Profile roster and the Operator's command line (`operator_cli.py`), running the agent and streaming its output (`streaming.py`), the session store, and Workspace file operations. File by file: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). |
| [`static/`](static/) | The web app, served as-is: `index.html`, `style.css`, and one vanilla JS file per area (`messages.js` chat, `sessions.js` sidebar, `workspace.js` file browser, `panels.js` settings, `boot.js` startup, `i18n.js` translations, `sw.js` service worker). |
| [`static/vendor/`](static/vendor/) | Third-party browser libraries checked in at fixed versions (KaTeX, js-yaml). |
| [`tests/`](tests/) | The pytest suite, plus browser checks (`browser_*.py`). `conftest.py` starts isolated test servers with their own state; `fixtures/` holds test data. |
| [`docs/`](docs/) | Guides for running and developing GFIT-CoWork; the [Docs](#docs) list below is the index. |
| [`docs/adr/`](docs/adr/) | Architecture decision records: why GFIT-CoWork is built the way it is. |
| [`docs/rfcs/`](docs/rfcs/) | Contracts and larger designs for the runtime (sessions, streaming, turns); [docs/CONTRACTS.md](docs/CONTRACTS.md) says which one covers which subsystem. |
| [`docs/architecture/`](docs/architecture/) | Focused design notes on single mechanisms (model caches, the agent API contract, ...). |
| [`docs/ui-ux/`](docs/ui-ux/) | HTML mock-ups for UI proposals. |
| [`deploy/`](deploy/) | The Deployment kit: one Docker Compose file, `new-deployment` and `cowork` to create and run one Deployment per Team in `deploy/deployments/<team>/`, and the Caddy reverse proxy that serves every Deployment over HTTPS. |
| [`deploy/caddy/`](deploy/caddy/) | The shared reverse proxy: its Compose file and an example Caddyfile. |
| [`deploy/docker/`](deploy/docker/) | The container entry script (`docker_init.bash`) used by the `Dockerfile`. |
| [`dev/mock-ldap/`](dev/mock-ldap/) | An OpenLDAP server with sample users, standing in for the company AD during development. |
| [`scripts/`](scripts/) | Developer tools: the test runner (`test.sh`), lint gates, and one-off maintenance scripts. |
| [`.claude/`](.claude/) | Settings and rules for AI coding assistants (issue tracker, labels, domain docs). |
| `.scratch/` | Local issues and specs, one folder per feature. Not committed. |

Files at the root:

| File | What it is |
| --- | --- |
| `server.py` | The HTTP server: the routing shell that hands every request to `api/`. |
| `bootstrap.py`, `start.sh`, `ctl.sh`, `start.ps1` | Launchers: find Hermes Agent and Python, then start the server (foreground, daemon, or Windows). |
| `Dockerfile`, `.dockerignore` | The GFIT-CoWork container image used by `deploy/`. |
| `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `package.json` | Python runtime and dev dependencies, project metadata and lint config, and the dev-only ESLint guard for `static/*.js`. |
| `.env.example` | A template for local settings: copy it to `.env`, which the launchers read at startup. |
| `CONTEXT.md` | The project's vocabulary. |
| `AGENTS.md`, `CONTRIBUTING.md` | How to contribute, for AI assistants and for people. |
| `CHANGELOG.md`, `LICENSE` | Release notes and the MIT license. |

## Running tests

```bash
./scripts/test.sh                                  # the whole suite, sharded
./scripts/test.sh tests/test_gfit_no_admin.py -v   # one file
./scripts/test.sh --serial                         # the whole suite in one process
```

The runner creates or reuses `.venv` with Python 3.14 and installs
`requirements-dev.txt` when something is missing (`HERMES_WEBUI_TEST_PYTHON`
picks the base interpreter). Tests run against isolated servers with their own
state directories; real sessions and cron jobs are never touched. There is no
hosted CI; this local run is the gate. See [docs/TESTING.md](docs/TESTING.md)
for how the tests log in and for manual checks.

## Compatibility

GFIT-CoWork imports Hermes Agent's modules directly and reads its state layout,
so the two must match. **Upgrade both together**, to versions tested together.
In Docker, pin both image tags (or the source revisions they are built from)
and follow [docs/docker.md](docs/docker.md) and
[deploy/README.md](deploy/README.md) when upgrading the agent. The boundary
between the two is in
[docs/rfcs/agent-source-boundary.md](docs/rfcs/agent-source-boundary.md). When
reporting an upgrade problem, give both versions (Settings → System).

## Docs

**Running a Deployment**
- [`deploy/README.md`](deploy/README.md) — adding a Team, adding a User, running a pilot
- [`docs/operator.md`](docs/operator.md) — what Users can do, and the Operator's command line for Profiles
- [`docs/setup.md`](docs/setup.md) — Setup: login and the Directory, provider setup on the server, the first Profile
- [`docs/configuration.md`](docs/configuration.md) — environment variables and how the launchers find things
- [`docs/docker.md`](docs/docker.md) — the image: volumes, UID/GID, the gateway, upgrades, common failures
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — diagnostic flows for common failures
- [`docs/setup-agent-checklist.md`](docs/setup-agent-checklist.md) — safety rules for assistant-led install support
- [`docs/advanced-chat-setup.md`](docs/advanced-chat-setup.md) — Gateway-backed chat and recall prefill
- [`docs/remote-workspaces.md`](docs/remote-workspaces.md) — remote terminal (SSH, Docker) Workspaces

**Using and customizing**
- [`docs/THEMES.md`](docs/THEMES.md) — themes and skins
- [`docs/workspace-git.md`](docs/workspace-git.md) — the read-only Workspace Git view

**Contributing and design**
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution style and PR expectations
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, file inventory and every API endpoint
- [`docs/TESTING.md`](docs/TESTING.md) — manual and automated verification
- [`docs/GUIDELINES.md`](docs/GUIDELINES.md) — bug classes, state ownership and evidence standards
- [`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/UIUX-GUIDE.md`](docs/UIUX-GUIDE.md) — design tokens and UI/UX principles
- [`docs/sse-streams.md`](docs/sse-streams.md) — the SSE endpoints
- [`docs/CONTRACTS.md`](docs/CONTRACTS.md) and [`docs/rfcs/README.md`](docs/rfcs/README.md) — contracts and RFCs
- [`CHANGELOG.md`](CHANGELOG.md) — GFIT-CoWork's release notes
