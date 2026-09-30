# The GFIT-CoWork Deployment kit

A **Deployment** is one Team's GFIT-CoWork: one Hermes Agent, one GFIT-CoWork,
and one config file with the Team's AD settings, Admins and API keys. Several
Deployments run on the same server. Each one has its own URL, such as
`https://sales.cowork.gfit.co.th`, and its own data.

| File | What it is |
|------|------------|
| `docker-compose.yml` | One Deployment: Hermes Agent, GFIT-CoWork, and their volumes |
| `team.env.example` | The config file for one Deployment: copy it to `.env` and fill in every value |
| `caddy/docker-compose.yml` | The HTTPS reverse proxy, run once per server |
| `caddy/Caddyfile.example` | Which hostname goes to which Deployment |

People only ever reach GFIT-CoWork through the reverse proxy over HTTPS.
GFIT-CoWork listens on `127.0.0.1` only, and Hermes Agent's gateway is not
published on the host at all.

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

### 1. Build the GFIT-CoWork image

```sh
git clone <gfit-cowork repo> /opt/gfit-cowork/src
docker build -t gfit-cowork:latest /opt/gfit-cowork/src
```

Every Deployment on the server uses this image (`GFIT_COWORK_IMAGE`).

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

Do this once for each Team. The example uses `sales`; for the second Team,
use another name, another port and another hostname (e.g. `account`, `8802`,
`account.cowork.gfit.co.th`).

1. **Make the Deployment's folder** and copy the kit into it:

   ```sh
   mkdir -p /opt/gfit-cowork/teams/sales/certs
   cd /opt/gfit-cowork/teams/sales
   cp /opt/gfit-cowork/src/deploy/docker-compose.yml .
   cp /opt/gfit-cowork/src/deploy/team.env.example .env
   chmod 600 .env
   cp ad-ca.crt certs/          # the AD CA certificate
   ```

2. **Fill in `.env`.** Every value in `team.env.example` is explained there.
   The ones that must be different for every Team:

   | Value | Example | Note |
   |-------|---------|------|
   | `TEAM` | `sales` | Names the containers and volumes (`gfit-sales-...`) |
   | `GFIT_PORT` | `8801` | Not used by any other Deployment |
   | `GFIT_HOSTNAME` | `sales.cowork.gfit.co.th` | The same hostname as in the Caddyfile |
   | `HERMES_WEBUI_ADMIN_USERS` | `521740,671278` | This Team's Admins. Name two so there is a backup |
   | the provider key, e.g. `OPENROUTER_API_KEY` | | This Team's own key |
   | `API_SERVER_KEY` | `openssl rand -hex 24` | A new random value for every Deployment |

   The AD values (`HERMES_WEBUI_LDAP_*`) are usually the same for every Team.

3. **Start it:**

   ```sh
   docker compose up -d
   docker compose ps          # both services "running"
   docker compose logs -f gfit-cowork
   ```

4. **Add it to the reverse proxy.** Add a block to
   `/opt/gfit-cowork/proxy/Caddyfile`:

   ```
   sales.cowork.gfit.co.th {
   	import gfit_tls
   	reverse_proxy 127.0.0.1:8801
   }
   ```

   Then reload Caddy:

   ```sh
   cd /opt/gfit-cowork/proxy
   docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
   ```

5. **Log in as the Admin.** Open `https://sales.cowork.gfit.co.th` and log in
   with an Admin's employee ID and AD password. Admins land in the `default`
   Profile. In **Settings → Providers**, save the Team's provider key, then
   choose the model the Team will use. This saves both in the `default`
   Profile, and new Profiles copy them from there. A User's Profile never
   uses a key from `.env` directly, because each Profile keeps its own
   credentials.

6. **Check the Admin gate.** Log in with a User's account (after adding one,
   below). They must not see Settings, the terminal or the Profiles panel.

## Add a User

The Admin does this in the web UI. There is no sign-up and no automatic
Profile: a person can log in only after an Admin has created their Profile.

1. Log in as an Admin and open the **Profiles** panel.
2. Create a Profile. The **name must be the person's employee ID** (e.g.
   `600001`). The display name is optional: it shows until they log in for
   the first time, and after that GFIT-CoWork takes their name from AD on every
   login. Tick **Clone config from active profile**. This copies the provider
   key and model the Admin set in `default`; without it, the Profile has no
   key and the agent cannot answer.
3. Tell them the URL. They log in with their employee ID (`600001`,
   `GFIT\600001` or `600001@gfit.co.th` all work) and their AD password.

To shut someone out, **Disable** their Profile. This ends their sessions at
once and keeps their data. **Enable** lets them back in. **Delete** removes the
Profile and its data for good, after you type its name to confirm.

A person who has a Profile in one Team's Deployment cannot log in to another
Team's Deployment. They get "You don't have access to this system yet". Give
them a Profile in each Deployment they need.

## Start with a pilot

Run one Team with 5–10 people for two to four weeks before adding more people
or more Teams.

1. **Pick the pilot group:** one Team, its two Admins, and 5–10 Users who
   will use it every day and say what goes wrong.
2. **Set up** that Team as above, and create Profiles for the pilot Users only.
3. **Check on day one** that every pilot User can log in, sees their name
   in the Profile chip, can start a conversation, and can use Sign Out.
4. **Watch during the pilot:**
   - `docker stats` for memory and CPU with everyone working at once. Size
     the server for the full Team from this.
   - `docker compose logs gfit-cowork` for AD errors ("directory is
     unavailable") and refused logins.
   - The model provider's usage and cost for the Team's key.
   - What Users ask the Admins for.
5. **Roll out further** when the pilot has run without AD or capacity problems.
   Add the rest of the Team's Users, then add the next Team as a new
   Deployment.

## Look after a Deployment

- **Logs:** `docker compose logs --tail 200 gfit-cowork` (or `hermes-agent`).
- **Back up** the volumes `gfit-<TEAM>_hermes-home` (Profiles, sessions,
  memory, User Workspaces, the Profile roster) and
  `gfit-<TEAM>_admin-workspace`, e.g. with
  `docker run --rm -v gfit-sales_hermes-home:/data -v "$PWD":/backup alpine tar czf /backup/sales-home.tgz -C /data .`
- **Upgrade GFIT-CoWork:** pull the source, rebuild the image (see "Once per
  server"), then `docker compose up -d` in each Team's folder.
- **Upgrade Hermes Agent:** the agent source volume is filled only on its first
  start, so remove it when upgrading:

  ```sh
  docker compose down
  docker volume rm gfit-sales_hermes-agent-src
  docker compose pull
  docker compose up -d
  ```

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
