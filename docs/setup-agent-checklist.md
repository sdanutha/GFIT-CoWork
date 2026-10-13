# Agent-assisted Setup checklist

This checklist is for an AI assistant helping a human install, reinstall, or
debug a GFIT-CoWork Setup ([`docs/setup.md`](setup.md)). There is
no setup wizard in the web app: the Operator sets up providers and Profiles on
the server (ADR 0006). GFIT-CoWork runs only in containers made by the
`deploy/` kit (ADR 0009); there is no native launcher.
Use it before creating a Deployment, inspecting logs, or recommending a
cleanup path.

If you are an AI assistant, read this file before assisting with Setup,
a Development Deployment, provider setup, reinstall, or support for a failed
first start.

## Role split

The human operator owns:

- choosing the Deployment and its N
- choosing the provider and model
- entering API keys, OAuth codes, and passwords
- approving any cleanup of a real Hermes home or a real Deployment's folder
- approving any external exposure outside localhost (including `DEV_BIND`)

The assistant owns:

- using an isolated Development Deployment unless the human explicitly says
  otherwise
- checking non-secret status endpoints and logs
- explaining which step passed or failed
- collecting redacted evidence for Discord or GitHub support
- stopping before destructive cleanup, credential handling, or public exposure

## Hard safety rules

- Do not delete, move, or overwrite the real `~/.hermes` directory, or a real
  Deployment's folder in `deploy/deployments/<team>/` (or the `home/` and
  `workspace/` its `.env` points at), unless the human explicitly asks for
  that exact action.
- Never point a trial Deployment's `HERMES_HOME_DIR` at a real Hermes home:
  the kit gives the folder to the container's user and changes its SQLite
  journal mode.
- Do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes. `deploy/new-deployment` prints the
  Dashboard password; do not repeat it in a report.
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
docker version --format '{{.Server.Version}}'
docker compose version
ls deploy/deployments/
```

`ls deploy/deployments/` shows the Deployments that already exist on this
machine. Do not touch them. To see which N each one uses, read only its port:

```bash
grep -H '^GFIT_PORT=' deploy/deployments/*/.env
```

Do not print a Deployment's full `.env`. Inspect only the specific non-secret
keys you need (`GFIT_PORT`, `GFIT_HOSTNAME`, `GFIT_DEV`, `HERMES_HOME_DIR`,
`HERMES_WORKSPACE_DIR`).

## Isolated Development Deployment

Use a new Development Deployment with its own name and a free N for a
reinstall or support trial. It keeps everything in its own folder,
`deploy/deployments/<name>/`, away from the operator's real memory, sessions,
Profiles, credentials, and cron state. N=7 is used below; pick another if it
is taken (`new-deployment` refuses an N already in use).

The server needs a Directory to start; a Development Deployment runs the mock
Directory with the sample Users in
[`dev/mock-ldap`](../dev/mock-ldap/README.md).

```bash
deploy/new-deployment --dev trial 7                   # http://localhost:20787
deploy/cowork trial up -d                             # the first start builds the dev image
deploy/cowork trial exec hermes-agent hermes setup    # the human picks the provider and enters the key
deploy/cowork trial exec -T -u hermeswebui -w /app gfit-cowork \
  /app/venv/bin/python -m api.operator_cli create 521740 --clone-from default
```

Open:

```text
http://localhost:20787
```

and log in as `521740` / `Somchai-Pass-1` (a public sample password).

When the trial is over, `deploy/cowork trial down` stops it. Delete
`deploy/deployments/trial/` only when the human agrees.

## Non-secret evidence commands

After the Deployment starts, collect status without secrets:

```bash
deploy/cowork trial ps
curl -sS http://localhost:20787/health
deploy/cowork trial logs --tail 120 gfit-cowork
deploy/cowork trial logs --tail 60 hermes-agent
deploy/cowork trial exec -T -u hermeswebui -w /app gfit-cowork \
  /app/venv/bin/python -m api.operator_cli list
```

The startup banner in the `gfit-cowork` log names the Directory, the agent
directory and the config file. `operator_cli list` shows each Profile's status
and last login. Do not paste paths or values that look sensitive. Redact paths
and provider details when the human asks for a public GitHub or Discord
support report.

## Pass criteria

A Setup trial passes when:

- `deploy/cowork trial ps` shows the services running.
- `/health` returns successfully.
- The trial User logs in and lands in their own Profile (the name in the
  Profile chip).
- A chat in that Profile gets an answer from the provider the human chose.
- The Profile's `config.yaml` and `.env` are inside
  `deploy/deployments/trial/home/profiles/<employee ID>`.
- GFIT-CoWork files are written under `deploy/deployments/trial/home/webui`.

## Failure triage

If the Deployment does not start:

- check `deploy/cowork trial ps` and `deploy/cowork trial logs gfit-cowork`
- check for a port conflict on the trial's ports (`new-deployment` printed
  them; they are also in its `.env`)
- confirm Docker is running (Docker Desktop on Windows and macOS)
- confirm `GFIT_HOSTNAME` is `localhost`: `deploy/cowork` refuses a
  Development Deployment on any other hostname

If the startup banner says the agent was not found:

- check the `gfit-cowork` log for `hermes-agent source not found` or a failed
  install
- use `docs/troubleshooting.md`, especially the `AIAgent not available` flow

If chat fails with a provider or credential error:

- confirm the Profile has the key: it was created with `--clone-from default`
  after `hermes setup`, or the human ran
  `deploy/cowork trial exec hermes-agent hermes -p <employee ID> model`
- confirm whether the provider is API-key based, OAuth based, or local
- let the human enter credentials or run the CLI auth flow
- do not ask the human to paste secrets into chat

If a local model server does not probe successfully:

- the agent runs in a container, so `localhost` there is the container itself
- for a server on the same machine under Docker Desktop, use
  `http://host.docker.internal:<port>/v1`
- for a server on another LAN machine, use its LAN IP and `/v1`

If login or reverse-proxy behavior is confusing:

- keep the first pass on `localhost`
- a server with no Directory refuses to start
- a login refused with "no access yet" means no Profile exists for that
  employee ID; "suspended" means the Profile is disabled (`operator_cli list`)
- include the reverse proxy shape in the support report without pasting tokens
  or cookies

## Final support report

Use this shape when reporting results to the human, Discord, or GitHub:

```text
Deployment (name, N, Development or Team):
OS / Docker version:
Repo commit:
Command used:
WebUI URL:
State isolation:
Health result:
Services (deploy/cowork <team> ps):
Profiles (operator_cli list):
Files created or changed:
Log excerpt:
Pass/fail:
Next recommended action:
```

Redact secrets and private paths before posting publicly.
