# Agent-assisted Setup checklist

This checklist is for an AI assistant helping a human install, reinstall, or
debug a GFIT-CoWork Setup ([`docs/setup.md`](setup.md)). There is
no setup wizard in the web app: the Operator sets up providers and Profiles on
the server (ADR 0006).
Use it before running bootstrap commands, inspecting logs, or recommending a
cleanup path.

If you are an AI assistant, read this file before assisting with Setup,
bootstrap, provider setup, reinstall, or support for a failed first start.

## Role split

The human operator owns:

- choosing the install path
- choosing the provider and model
- entering API keys, OAuth codes, and passwords
- approving any cleanup of a real Hermes home
- approving any external exposure outside localhost

The assistant owns:

- using isolated trial directories unless the human explicitly says otherwise
- checking non-secret status endpoints and logs
- explaining which step passed or failed
- collecting redacted evidence for Discord or GitHub support
- stopping before destructive cleanup, credential handling, or public exposure

## Hard safety rules

- Do not delete, move, or overwrite the real `~/.hermes` directory unless the
  human explicitly asks for that exact action.
- Do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes.
- Do not modify real cron jobs, real sessions, real profiles, or real memory
  files during a Setup trial.
- Do not expose WebUI on a public interface without a Directory login and
  explicit human approval.
- Do not proxy or tunnel local service checks such as `localhost`,
  `127.0.0.1`, private LAN addresses, or Docker container loopback paths.

## Pre-flight

Confirm the basic context:

```bash
pwd
git branch --show-current
git rev-parse --short HEAD
python3 --version
```

Check whether repo-local environment overrides will affect bootstrap:

```bash
test -f .env && grep -n 'HERMES_HOME\|HERMES_WEBUI_STATE_DIR\|HERMES_WEBUI_PORT\|HERMES_WEBUI_HOST' .env
```

If `.env` exists, do not print the full file. Inspect only the specific
non-secret keys needed to understand the active Hermes home, WebUI state
directory, port, or host.

## Isolated local trial

Use an isolated Hermes home and WebUI state directory for a reinstall or support
trial. This keeps the test away from the operator's real memory, sessions,
profiles, credentials, and cron state.

The server needs a Directory to start; a trial uses the in-memory Directory
with one trial User. Let the human choose the trial password.

```bash
mkdir -p ~/hermes-setup-test
export HERMES_HOME=~/hermes-setup-test/.hermes
export HERMES_WEBUI_STATE_DIR=~/hermes-setup-test/webui
export HERMES_WEBUI_PORT=8789
export HERMES_WEBUI_DIRECTORY=memory
export HERMES_WEBUI_DIRECTORY_USERS=~/hermes-setup-test/users.json
# users.json: {"<employee ID>": {"password": "<chosen by the human>", "display_name": "..."}}
hermes setup                                   # the human picks the provider and enters the key
python3 -m api.operator_cli create <employee ID> --clone-from default
python3 bootstrap.py
```

Open:

```text
http://127.0.0.1:8789
```

The bootstrap writes a port-specific log under the selected WebUI state
directory:

```text
~/hermes-setup-test/webui/bootstrap-8789.log
```

For daemon-style installs, `ctl.sh` writes the daemon log to the active
`HERMES_HOME` by default:

```text
~/.hermes/webui.log
```

When using the isolated trial environment, prefer the bootstrap command above
unless the human specifically wants to validate `ctl.sh`.

## Non-secret evidence commands

After the server starts, collect status without secrets:

```bash
curl -sS http://127.0.0.1:8789/health
python3 -m api.operator_cli list
find ~/hermes-setup-test -maxdepth 3 -type f | sort
tail -n 120 ~/hermes-setup-test/webui/bootstrap-8789.log
```

The startup banner in the log names the Directory, the agent directory and
the config file. `operator_cli list` shows each Profile's status and last
login. Do not paste paths or values that look sensitive. Redact paths and provider details when the human asks for a public
GitHub or Discord support report.

## Pass criteria

A local Setup trial passes when:

- `/health` returns successfully.
- The trial User logs in and lands in their own Profile (the name in the
  Profile chip).
- A chat in that Profile gets an answer from the provider the human chose.
- The Profile's `config.yaml` and `.env` are inside the intended isolated
  `HERMES_HOME/profiles/<employee ID>` during a trial.
- GFIT-CoWork files are written under the intended `HERMES_WEBUI_STATE_DIR`.

## Failure triage

If the server does not start:

- check the bootstrap log
- check for a port conflict on `8789`
- confirm Python can run `bootstrap.py`
- confirm `.env` is not overriding the isolated directories or port

If the startup banner says the agent was not found:

- confirm the bootstrap found or installed Hermes Agent
- check whether the running Python can import `run_agent.AIAgent`
- use `docs/troubleshooting.md`, especially the `AIAgent not available` flow

If chat fails with a provider or credential error:

- confirm the Profile has the key: it was created with `--clone-from default`
  after `hermes setup`, or the human ran `hermes -p <employee ID> model`
- confirm whether the provider is API-key based, OAuth based, or local
- let the human enter credentials or run the CLI auth flow
- do not ask the human to paste secrets into chat

If a local model server does not probe successfully:

- from native macOS/Linux, use `http://127.0.0.1:<port>/v1` when the server is
  on the same host
- from Docker Desktop, use `http://host.docker.internal:<port>/v1`
- from another LAN machine, use the server's LAN IP and `/v1`
- remember that `localhost` inside a container is the container itself

If login or reverse-proxy behavior is confusing:

- keep the first pass on `127.0.0.1`
- a server with no Directory (`HERMES_WEBUI_DIRECTORY`) refuses to start
- a login refused with "no access yet" means no Profile exists for that
  employee ID; "suspended" means the Profile is disabled (`operator_cli list`)
- include the reverse proxy shape in the support report without pasting tokens
  or cookies

## Final support report

Use this shape when reporting results to the human, Discord, or GitHub:

```text
Install path:
OS / Python:
Repo commit:
Command used:
WebUI URL:
State isolation:
Health result:
Profiles (operator_cli list):
Files created or changed:
Log excerpt:
Pass/fail:
Next recommended action:
```

Redact secrets and private paths before posting publicly.
