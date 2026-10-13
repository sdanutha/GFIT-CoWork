# Setup

This guide is for the **Operator**: the person with shell access to the
server who sets up a GFIT-CoWork Deployment and its first Users. There is no
setup wizard in the web app and no Admin login (ADR 0006); everything below is
done on the server.

If an AI assistant is helping with install, reinstall, provider
setup, or Setup support, read
[`docs/setup-agent-checklist.md`](setup-agent-checklist.md) before
running commands or inspecting logs.

GFIT-CoWork runs only in containers, from the `deploy/` kit
([ADR 0009](adr/0009-every-way-to-run-is-a-container.md)):

- **For a Team on a server**, follow [`deploy/README.md`](../deploy/README.md):
  `deploy/new-deployment <team> <N>`, fill in the AD values, `deploy/cowork
  <team> up -d`, then Setup and a Profile for each person.
- **For development, debugging or a trial on one machine**, make a
  [Development Deployment](#a-development-deployment).

Either way the steps are the same:

1. Give the `default` Profile a provider and model (`hermes setup`).
2. The Directory: the company AD for a Team, the mock Directory for a
   Development Deployment. Without one the server does not start.
3. Create a Profile for each person (`operator_cli create <employee ID>
   --clone-from default`).
4. The person logs in with their employee ID and password.

## Before you start

GFIT-CoWork is only the browser interface. The agent runtime, memory, skills,
config, cron jobs and provider credentials belong to Hermes Agent, one Profile
per User.

There is no native way to run GFIT-CoWork any more: no `start.sh`,
`start.ps1`, `ctl.sh` or `bootstrap.py`. The host needs Docker (Docker
Desktop on Windows and macOS) and a bash (Git Bash on Windows).

## A Development Deployment

A Deployment on your own machine, made by the same kit as a Team's, with the
mock Directory instead of AD, your checkout as the app, a debugger, and
plain HTTP on `localhost`:

```bash
deploy/new-deployment --dev dev 0        # N=0: http://localhost:8787
deploy/cowork dev up -d                  # the first start builds the dev image and installs the venv
deploy/cowork dev exec hermes-agent hermes setup     # once: provider, key and model
deploy/cowork dev exec -T -u hermeswebui -w /app gfit-cowork \
  /app/venv/bin/python -m api.operator_cli create 521740 --clone-from default
```

Then open `http://localhost:8787` and log in as `521740` / `Somchai-Pass-1`
(the sample Users are in [`dev/mock-ldap`](../dev/mock-ldap/README.md)).

- **Editing:** your checkout is mounted at `/app`. JS and CSS changes show on
  reload; after a Python change run `deploy/cowork dev restart gfit-cowork`.
- **Debugging:** the server runs under `debugpy` on `DEBUG_PORT` (5678 for
  N=0). In VS Code, run **Attach to the Development Deployment**
  (`.vscode/launch.json`) and set breakpoints in the checkout.
- **State:** everything lives in `deploy/deployments/dev/` and survives
  `down`. Setup is needed again only if you delete `home/`.
- **Network:** every port (the app, the Dashboard, the gateway, A2A, the
  debugger, the mock Directory) is on `127.0.0.1`. `DEV_BIND=0.0.0.0` in the
  Deployment's `.env` opens them to your network, with the sample passwords
  from the repo; use it only to test between machines.
- `deploy/cowork` refuses a Development Deployment whose `GFIT_HOSTNAME` is not
  `localhost`, so it cannot run on a Team's server.
- On Docker Desktop, Hermes Agent turns off SQLite WAL in `home/` (ADR 0008):
  do not measure capacity on a Development Deployment.

Never point a Development Deployment at your real Hermes home: it gives the
folder to the container's user and changes its SQLite journal mode. Copy what
you need into its `home/` instead.

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
password is wrong. A Development Deployment runs an OpenLDAP stand-in, the
mock Directory (see [`dev/mock-ldap`](../dev/mock-ldap/README.md)).

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

A Deployment keeps all of it in its `home/` (`HERMES_HOME_DIR` can move it),
web state in `home/webui`.

## When to file an issue

File an issue when the diagnostics point to GFIT-CoWork rather than local
configuration. Include the install path, the output of `/health` (or the
startup banner if the server never starts), the provider and the Base URL
shape with secrets redacted, and relevant logs (`deploy/cowork <team> logs`). Never paste API keys, OAuth
tokens, passwords or full `.env` contents into an issue.
