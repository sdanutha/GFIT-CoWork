# Setup

This guide is for the **Operator**: the person with shell access to the
server who sets up a GFIT-CoWork Deployment and its first Users. There is no
setup wizard in the web app and no Admin login (ADR 0006); everything below is
done on the server.

If an AI assistant is helping with install, reinstall, bootstrap, provider
setup, or Setup support, read
[`docs/setup-agent-checklist.md`](setup-agent-checklist.md) before
running commands or inspecting logs.

The short version:

1. Install Hermes Agent and give the `default` Profile a provider and model
   (`hermes setup` or `hermes model`).
2. Configure the Directory (`HERMES_WEBUI_DIRECTORY`): the company AD for a
   Deployment, or the in-memory Directory for a trial. Without one the server
   does not start.
3. Start GFIT-CoWork.
4. Create a Profile for each person:
   `python3 -m api.operator_cli create <employee ID> --clone-from default`.
5. The person logs in with their employee ID and AD password.

For a Team on a server, the `deploy/` kit does steps 2–3 for you; follow
[`deploy/README.md`](../deploy/README.md).

## Before you start

GFIT-CoWork is only the browser interface. The agent runtime, memory, skills,
config, cron jobs and provider credentials belong to Hermes Agent, one Profile
per User.

The bootstrap supports Linux, macOS and WSL2. Native Windows runs with
`pwsh .\start.ps1`.

## Install path choices

| Path | Use it when | Notes |
|---|---|---|
| Docker Deployment kit (`deploy/`) | You deploy GFIT-CoWork for a Team on a server | One Deployment per Team behind the HTTPS reverse proxy; see [`deploy/README.md`](../deploy/README.md). |
| Local bootstrap | Development, or a trial on one machine | `./start.sh` or `python3 bootstrap.py`, with the in-memory Directory. |

## A trial on one machine

Do not delete `~/.hermes` to start over. It holds the real Hermes config,
credentials, memory, skills, Profiles, sessions and cron state. Use an
isolated Hermes home and state directory, and the in-memory Directory:

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

hermes setup                                   # provider and model for the default Profile
python3 -m api.operator_cli create 521740 --clone-from default
python3 bootstrap.py
```

Then open `http://127.0.0.1:8789` and log in as `521740` / `trial-only`. The
in-memory Directory is for trials and tests only; a Deployment uses the
company AD (`HERMES_WEBUI_DIRECTORY=ldap`, see [Login](#login)).

If your repo has a `.env` file, the bootstrap and the command line load it.
Remove or adjust any `HERMES_HOME`, `HERMES_WEBUI_STATE_DIR` or
`HERMES_WEBUI_PORT` entries there before an isolated trial.

## Providers and models

Provider setup is the Operator's, on the server, with Hermes Agent's own
tools: `hermes setup` for a first install, `hermes model` to change the
provider or model. They write the provider key to the Profile's `.env` and the
default model to its `config.yaml`.

Set them up in the `default` Profile first. `operator_cli create … --clone-from
default` then copies the `default` Profile's `config.yaml` and skills into the
new User's Profile, and from its `.env` only the provider credentials (the
provider keys and the keys of `custom_providers`), so it works from the first
login. A Profile created without `--clone-from` has no provider key until the
Operator gives it one (`hermes -p <employee ID> model`).

The `default` Profile's `.env` is the Deployment's: the server loads it at
startup, so **every User's agent can read it** (ADR 0002: Users in one
Deployment trust one another, and every agent runs as the same OS user). The
provider keys in it are used on the Users' behalf; any other value there (tool
and integration tokens, database URLs) is visible to every User. Put no secret
there that a User must not see. A secret for one User goes in that User's own
Profile: `hermes -p <employee ID> …`. A User may also use the Deployment's
provider logins (the `default` Profile's credential pool and OAuth logins).

Use `-p <employee ID>` for one command rather than `hermes profile use <employee
ID>`. The server ignores Hermes's sticky active profile and always runs as the
Deployment's `default` Profile, so a User's `.env` never becomes the server's;
startup prints a warning while the sticky profile names someone else, and
`hermes profile use default` clears it.

A User can change their own Profile's default model, auxiliary models and
reasoning settings in the web app, among the providers the Operator set up. A
model's endpoint (`base_url`) and API key stay with the Operator.

## Base URL rules for local model servers

For self-hosted providers, the Base URL should point to the OpenAI-compatible
API root. Common examples:

| Server | Typical Base URL |
|---|---|
| LM Studio on the same non-Docker host | `http://127.0.0.1:1234/v1` |
| Ollama on the same non-Docker host | `http://127.0.0.1:11434/v1` |
| LM Studio from Docker Desktop | `http://host.docker.internal:1234/v1` |
| Ollama from Docker Desktop | `http://host.docker.internal:11434/v1` |
| Local server from Linux Docker Engine | `http://api.local:<port>/v1` with `api.local:host-gateway` in Compose `extra_hosts` |
| Local server on another LAN machine | `http://<lan-ip>:<port>/v1` |

Inside Docker, `localhost` means the container itself, not the host or another
machine on your LAN. If LM Studio or Ollama runs outside the container, use
`host.docker.internal` on Docker Desktop, the server's LAN IP address, or a
Linux Docker host alias:

```yaml
services:
  hermes-webui:
    extra_hosts:
      - "api.local:host-gateway"
```

## Login

A User signs in with their employee ID (`521740`, `GFIT\521740` or
`521740@gfit.co.th` all work) and password. The password is checked by the
**Directory**; GFIT-CoWork never stores or logs it. Login succeeds only when the
Directory accepts the password **and** a Profile named after the employee ID
exists and is active. The session is then bound to that Profile. Every person
who logs in is a **User**; nobody logs in to the `default` Profile. An unknown
employee ID is told they have no access yet, and a disabled Profile is told its
access is suspended.

| Variable | Meaning |
| --- | --- |
| `HERMES_WEBUI_DIRECTORY` | Which Directory to use: `ldap` uses the company AD; `memory` uses the in-memory Directory. Required: unset, the server does not start. Any other value refuses every login. |
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
stand-in (see [its README](../dev/mock-ldap/README.md)).

The Directory is the only way in, and login is always on.
The upstream login methods (the shared `HERMES_WEBUI_PASSWORD` or Settings
password, passkeys, OIDC and the trusted header) are removed: a leftover
setting of theirs lets nobody in, and startup reports it as ignored. So does a
leftover `HERMES_WEBUI_ADMIN_USERS` from an earlier version: it grants nothing,
and the people it names log in as Users to their own Profile, if they have one.
A stored upstream password hash or passkey file is left on disk and ignored.
The trusted proxy settings (`HERMES_WEBUI_TRUST_FORWARDED_FOR`,
`HERMES_WEBUI_TRUSTED_PROXY_CIDRS`) stay for the rate limit.

There is no mode with login turned off: with no Directory the server does not
start, on any address, and names the Directory settings.

For installed PWAs, prefer the Directory login over proxy basic auth. Reverse
proxies are supported, but HTTP basic-auth challenges in front of the
GFIT-CoWork origin can interrupt the service-worker and shell-asset fetches the
installed app relies on during updates.

## What gets written

- Each Profile's `config.yaml` and `.env` (Hermes Agent): provider, default
  model, API key.
- The Profile roster (`gfit_roster.json` in the state directory): display
  name, status and last login of each Profile.
- The Deployment's `settings.json` (state directory): settings that belong to
  the whole Deployment, edited by the Operator.
- Each Profile's `webui_state/settings.json`: that User's own web settings.

State normally lives outside the repository: Hermes Agent state in `~/.hermes`
(Windows `%LOCALAPPDATA%\hermes`), web state in `$HERMES_HOME/webui`. Override
them with `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for an isolated install.

## When to file an issue

File an issue when the diagnostics point to GFIT-CoWork rather than local
configuration. Include the install path, the output of `/health` (or the
startup banner if the server never starts), the provider and the Base URL
shape with secrets redacted, and relevant logs. Never paste API keys, OAuth
tokens, passwords or full `.env` contents into an issue.
