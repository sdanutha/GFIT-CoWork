# The GFIT-CoWork Deployment kit

A **Deployment** is one Team's GFIT-CoWork: one Hermes Agent, one GFIT-CoWork,
Hermes Agent's dashboard, and one folder holding the Team's config file (AD
settings and API keys) and everything the Deployment keeps. Several
Deployments run on the same server. Each one has its own URL, such as
`https://sales.cowork.gfit.co.th`, and its own data.

Everyone who logs in is a **User** with their own Profile. There is no Admin in
the web app (ADR 0006): the **Operator** — whoever runs the server — sets up the
provider key and manages Profiles from the server's shell, with the commands
below.

| File | What it is |
|------|------------|
| `docker-compose.yml` | One Deployment: Hermes Agent, GFIT-CoWork and the dashboard. Run it only through `cowork` |
| `docker-compose.dev.yml` | The overlay `cowork` adds for a Development Deployment (`GFIT_DEV=1`); never on a server |
| `team.env.example` | The template for one Deployment's `.env`; every value is explained there |
| `new-deployment` | Creates a Deployment's folder in `deployments/<team>/`, with its ports and random secrets |
| `cowork` | Runs `docker compose` for one Deployment: `deploy/cowork <team> <compose args>` |
| `caddy/docker-compose.yml` | The HTTPS reverse proxy, run once per server |
| `caddy/Caddyfile.example` | Which hostname goes to which Deployment |

People only ever reach GFIT-CoWork through the reverse proxy over HTTPS.
GFIT-CoWork and the dashboard listen on `127.0.0.1` only, and Hermes Agent's
gateway is not published on the host at all.

The same kit makes a **Development Deployment** on a developer's machine
(`deploy/new-deployment --dev dev 0`): see
[docs/setup.md](../docs/setup.md#a-development-deployment) and
[ADR 0009](../docs/adr/0009-every-way-to-run-is-a-container.md). Everything
below is about a Team's Deployment on a server.

## Before you start

- A Linux server with Docker Engine and the Docker Compose plugin
  (`docker compose version`).
- A DNS name for each Team, pointing at the server, e.g.
  `sales.cowork.gfit.co.th`. A wildcard `*.cowork.gfit.co.th` saves asking IT
  for each new Team.
- A TLS certificate for those names from the company CA (a wildcard is
  easiest). For a first pilot, Caddy's own CA works too (`tls internal`), but
  browsers will warn until its root is trusted.
- The AD details from IT: the LDAPS address, the UPN domain or NetBIOS domain,
  the base DN users live under, and the AD CA certificate. The server must
  reach AD on port 636 (LDAPS), or 389 with StartTLS.
- A model provider API key for each Team.

## Once per server

### 1. Get the GFIT-CoWork source

```sh
git clone <gfit-cowork repo> /opt/gfit-cowork/src
```

Every Deployment runs from this checkout. `deploy/cowork <team> up -d` builds
the GFIT-CoWork image (`gfit-cowork:latest`) from it each time, so every
Deployment runs the checkout's current code. The commands below run from
`/opt/gfit-cowork/src`.

### 2. Start the reverse proxy

```sh
mkdir -p /opt/gfit-cowork/proxy/certs
cp /opt/gfit-cowork/src/deploy/caddy/docker-compose.yml /opt/gfit-cowork/proxy/
cp /opt/gfit-cowork/src/deploy/caddy/Caddyfile.example /opt/gfit-cowork/proxy/Caddyfile
# the company certificate and key:
cp cowork.crt cowork.key /opt/gfit-cowork/proxy/certs/
```

Edit `Caddyfile` so it holds one block per Team. Leave out Teams that do not
exist yet. Then:

```sh
cd /opt/gfit-cowork/proxy
docker compose up -d
```

## Add a new Team

Do this once for each Team. The example uses `sales` with N=1; for the second
Team, use another name and another N (e.g. `account`, 2).

1. **Create the Deployment.** N picks the ports (see [Ports](#ports)); the
   hostname defaults to `<team>.cowork.gfit.co.th`:

   ```sh
   sudo deploy/new-deployment sales 1
   ```

   This creates `deploy/deployments/sales/` with `.env` (readable by you
   only), `home/`, `workspace/` and `certs/`, and prints the ports and the
   dashboard login. They are also in `.env`. Everything the Deployment keeps
   is in this folder ([ADR 0008](../docs/adr/0008-deployment-storage-and-dashboard.md)).
   Every container runs as UID/GID 10000, and `new-deployment` gives `home/`
   and `workspace/` to 10000:10000, which is why it needs `sudo`
   ([ADR 0009](../docs/adr/0009-every-way-to-run-is-a-container.md)); reading
   those folders on the server needs `sudo` too. `GFIT_UID=<uid>
   GFIT_GID=<gid>` picks another service user. To keep the data on another
   disk, set `HERMES_HOME_DIR` and `HERMES_WORKSPACE_DIR` in `.env` to absolute
   paths (owned by the same UID/GID) before the first start; then back those
   up too.
   On Docker Desktop (a trial on a laptop), Hermes Agent logs that SQLite WAL
   is off for `home/`: that is expected there and does not happen on the Linux
   server.

2. **Fill in `.env`** in `deploy/deployments/sales/`. `new-deployment`
   already filled in the name, ports, hostname and every random secret. You
   fill in:

   | Value | Note |
   |-------|------|
   | `HERMES_WEBUI_LDAP_URL`, `HERMES_WEBUI_LDAP_DOMAIN`, `HERMES_WEBUI_LDAP_BASE_DN` | From IT; usually the same for every Team |
   | the provider key, e.g. `OPENROUTER_API_KEY` | This Team's own key |

   and copy the AD CA certificate to `certs/ad-ca.crt`. `deploy/cowork`
   refuses to start a Deployment while any of these is missing. Before every
   start it also copies `API_SERVER_KEY` into `home/.env`, the `default`
   Profile's `.env`, because Hermes Agent's gateway reads it from there once
   the Deployment has more than one Profile. Do not change the key in
   `home/.env`: change it in the Deployment's `.env`, then
   `deploy/cowork <team> down` and `up -d`. `cowork` will not replace
   `home/.env` while the Deployment runs, because Hermes Agent writes that
   file too.

3. **Start it:**

   ```sh
   deploy/cowork sales up -d
   deploy/cowork sales ps          # three services "running"
   deploy/cowork sales logs -f gfit-cowork
   ```

4. **Add it to the reverse proxy.** Add a block to
   `/opt/gfit-cowork/proxy/Caddyfile`, with the Deployment's `GFIT_PORT`:

   ```
   sales.cowork.gfit.co.th {
       import gfit_tls
       reverse_proxy 127.0.0.1:20187
   }
   ```

   Then reload Caddy:

   ```sh
   cd /opt/gfit-cowork/proxy
   docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
   ```

5. **Set the provider and model in the `default` Profile.** On the server:

   ```sh
   deploy/cowork sales exec hermes-agent hermes model
   ```

   Choose the Team's provider, enter its key and choose the model. This saves
   both in the `default` Profile, and new Profiles copy them from there. A
   User's Profile never uses a key from `.env` directly, because each Profile
   keeps its own credentials.

6. **Check it.** Add yourself as a User (below), open
   `https://sales.cowork.gfit.co.th` and log in. You land in your own Profile,
   with your name in the Profile chip, and the agent answers.

## Ports

Each Deployment gets its ports from its N (0–99). Only GFIT-CoWork and the
dashboard are published, on `127.0.0.1` only; Hermes Agent's gateway is never
published.

| N | GFIT-CoWork (`GFIT_PORT`) | Dashboard (`DASHBOARD_PORT`) |
|---|---|---|
| 0 | 8787 | 9119 |
| 1–99 | 2NN87 | 2NN19 |

Example: N=1 → 20187 / 20119, N=25 → 22587 / 22519.

## The dashboard

Hermes Agent's dashboard is for the Operator only. It is never behind the
reverse proxy: reach it from your own machine over an SSH tunnel, then open
`http://127.0.0.1:20119` and log in with the dashboard login from `.env`
(`HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `..._PASSWORD`):

```sh
ssh -L 20119:127.0.0.1:20119 <server>
```

Anyone who can log in to the dashboard can change the whole Deployment, so do
not give its login to Users. It can also create and delete Profiles, but it does
not know the Profile roster: create, disable, enable and delete Profiles with
`gfit` (below), not from the dashboard. Why the dashboard is there and what it
may be used for is [ADR 0008](../docs/adr/0008-deployment-storage-and-dashboard.md).

## Add a User

The Operator does this on the server. There is no sign-up and no automatic
Profile: a person can log in only after their Profile exists. In the
checkout, define a shortcut for the GFIT-CoWork command line (it runs inside
the container, as the user the server runs as):

```sh
gfit() { deploy/cowork sales exec -T -u hermeswebui -w /app gfit-cowork /app/venv/bin/python -m api.operator_cli "$@"; }
```

1. Create the Profile. The **name must be the person's employee ID**:

   ```sh
   gfit create 600001 --display-name "Somsri Jaidee" --clone-from default
   ```

   The display name is optional: it shows until they log in for the first
   time, and after that GFIT-CoWork takes their name from AD on every login.
   `--clone-from default` copies the provider key and model set in `default`
   (from its `.env`, only the provider keys); without it, the Profile has no
   key and the agent cannot answer. Every User's agent can read the `default`
   Profile's `.env`, so keep secrets Users must not see out of it.
2. Tell them the URL. They log in with their employee ID (`600001`,
   `GFIT\600001` or `600001@gfit.co.th` all work) and their AD password.

Every command is described in [docs/operator.md](../docs/operator.md).
`gfit list` shows every Profile with its status and last login. It lists
the Profile directories, so a broken Hermes Agent install does not hide
anyone: it prints a `warning:` line naming the problem and lists them
anyway. If the Profile directories themselves cannot be read, it says so and
exits with status 1 instead of showing an empty list.

To shut someone out, **disable** their Profile: `gfit disable 600001`. Their
scheduled jobs pause at once, and within a few seconds the server ends their
sessions and stops any turn still running, so nothing of theirs runs on the
Team's API key; their data stays. `gfit enable 600001` lets them back in and
resumes the jobs the disable paused (a job they had paused themselves stays
paused). `gfit delete 600001 --confirm 600001` removes a disabled Profile and
its data for good.

A person who has a Profile in one Team's Deployment cannot log in to another
Team's Deployment. They get "You don't have access to this system yet". Give
them a Profile in each Deployment they need.

## Start with a pilot

Run one Team with 5–10 people for two to four weeks before adding more people
or more Teams.

1. **Pick the pilot group:** one Team, an Operator and a backup, and 5–10 Users who
   will use it every day and say what goes wrong.
2. **Set up** that Team as above, and create Profiles for the pilot Users only.
3. **Check on day one** that every pilot User can log in, sees their name
   in the Profile chip, can start a conversation, and can use Sign Out.
4. **Watch during the pilot:**
   - `docker stats` for memory and CPU with everyone working at once. Size
     the server for the full Team from this.
   - `deploy/cowork sales logs gfit-cowork` for AD errors ("directory is
     unavailable") and refused logins.
   - The model provider's usage and cost for the Team's key.
   - What Users ask the Operator for.
5. **Roll out further** when the pilot has run without AD or capacity problems.
   Add the rest of the Team's Users, then add the next Team as a new
   Deployment.

## Look after a Deployment

- **List Deployments:** `deploy/cowork` with no arguments.
- **Logs:** `deploy/cowork sales logs --tail 200 gfit-cowork` (or
  `hermes-agent`, `hermes-dashboard`). The containers are named
  `gfit-<team>-agent`, `gfit-<team>-cowork` and `gfit-<team>-dashboard`, so
  `docker logs gfit-sales-cowork` works too.
- **Session store:** `gfit sessions-audit` reports problems (read-only).
  `gfit sessions-repair` and `gfit sessions-cleanup [--empty]` change session
  files the server also writes: run them when nobody is using the Team, then
  `deploy/cowork sales restart gfit-cowork` so it rereads them.
- **Back up** the Deployment's folder, `deploy/deployments/sales/`: `.env`,
  `home/` (Profiles, sessions, memory, User Workspaces, the Profile roster),
  `workspace/` (the server's default Workspace; no User works in it) and
  `certs/`. Stop it first so nothing writes during the copy:

  ```sh
  deploy/cowork sales stop
  tar czf sales-backup.tgz --exclude sales/home/hermes-agent -C deploy/deployments sales
  deploy/cowork sales start
  ```

  The empty `home/hermes-agent/` folder is only where the agent source is
  mounted and can be left out. If `HERMES_HOME_DIR` or `HERMES_WORKSPACE_DIR`
  point outside the folder, back those paths up as well. The volumes
  `gfit-<team>_agent-data` and `gfit-<team>_dashboard-data` (the agent image's
  own `/opt/data`, where the agent installs tools for itself) are not part of
  a backup: like the agent source volume, they are refilled as needed.
- **Upgrade GFIT-CoWork:** `git -C /opt/gfit-cowork/src pull`, then
  `deploy/cowork <team> up -d` for each Team. It rebuilds the image from the
  checkout.
- **Upgrade Hermes Agent:** the agent source volume is filled only on its first
  start, so remove it when upgrading:

  ```sh
  deploy/cowork sales down
  docker volume rm gfit-sales_hermes-agent-src
  deploy/cowork sales pull hermes-agent hermes-dashboard
  deploy/cowork sales up -d
  ```

- **Move a Deployment from the old named volumes.** Deployments made before
  `new-deployment` kept their data in the volumes `gfit-<TEAM>_hermes-home` and
  `gfit-<TEAM>_admin-workspace`. Create the new folder with the same Team name,
  copy the old `.env` values and `certs/` into it, then, with the old
  Deployment stopped (`docker compose down` in its old folder):

  ```sh
  deploy/new-deployment sales 1
  # copy the old .env values and certs/ into deploy/deployments/sales/
  for v in hermes-home:home admin-workspace:workspace; do
    docker run --rm -v "gfit-sales_${v%%:*}":/from -v "$PWD/deploy/deployments/sales/${v##*:}":/to \
      alpine sh -c 'cp -a /from/. /to/ && chown -R 10000:10000 /to'
  done
  deploy/cowork sales up -d
  ```

  Keep the old volumes until the Team has checked their Profiles, then remove
  them with `docker volume rm`.
- **Login rate limit behind the proxy:** wrong passwords are counted per
  person's address, which the proxy forwards. GFIT-CoWork trusts that address
  only from `TRUSTED_PROXY_CIDRS` (the Docker bridge networks by default). If
  your office network also uses `172.16.0.0/12`, narrow it to the Deployment
  network's gateway (`docker network inspect gfit-sales_default`).

## Before you rely on it

- All Profiles in one Deployment run their agents as the same OS user. A User
  can ask the agent to read another User's files (ADR 0002). Only put people
  who trust each other in one Deployment.
- GFIT-CoWork never stores AD passwords. They go to AD over LDAPS or StartTLS
  only; plain LDAP is refused.
