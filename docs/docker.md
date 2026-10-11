# GFIT-CoWork — Docker image guide

GFIT-CoWork runs in Docker one way only: the Deployment kit in
[`deploy/`](../deploy/), one Deployment per Team (ADR 0005). The step-by-step
guide for building the image, starting the reverse proxy and adding a Team is
[`deploy/README.md`](../deploy/README.md).

This guide covers the image itself and how it behaves inside a Deployment:
the security model, the volumes, UID/GID, the read-only agent source, the
gateway, upgrades, and what goes wrong.

## Production image security model

The production Docker image is hardened for the normal single-tenant container threat model:
GFIT-CoWork assumes one operator controls the container, mounted Hermes home, and workspace.
The image does **not** install `sudo`, does not add runtime users to a sudo group, and does not
grant `NOPASSWD` escalation. If an agent/tool process gains a shell as `hermeswebui`, it should
not be able to become root with a passwordless sudo command.

The entrypoint still starts as `root` for a narrow init phase because Docker mounts often need
UID/GID alignment and ownership preparation before the app can read `~/.hermes`, `/workspace`,
`/app`, and `/uv_cache`. After that setup, `docker_init.bash` re-execs itself as the unprivileged
`hermeswebui` user and starts the server there. Init scratch files under `/tmp/hermeswebui_init`
are owner-only (`0700` directory, `0600` files), not world-writable.

For multi-tenant or hostile-container environments, rebuild with your own runtime user, mount policy,
and supervisor assumptions. Development images that need package-manager convenience should add
those tools in a dev-only Dockerfile instead of reintroducing passwordless sudo to production.

## How a Deployment is laid out

A Deployment is two containers, `hermes-agent` and `gfit-cowork`, sharing
**named Docker volumes** (not bind mounts). Named volumes solve the UID/GID
problem by construction: Docker creates the volume's root directory, both
containers read and write the same files, and no host-side permission setup is
needed. Every volume is prefixed with the project name `gfit-<TEAM>`, so
several Deployments share one server without touching each other.

```
                 ┌─────────────────────────────────┐
                 │      hermes-home (volume)       │
                 │ (Profiles, sessions, state, ...) │
                 └─────────────────────────────────┘
                          ↑              ↑
                          │ rw           │ rw
                          │              │
      ┌──────────────┐    │              │    ┌──────────────┐
      │ hermes-agent │────┘              └────│ gfit-cowork  │
      │  (port 8642) │                        │  (port 8787) │
      └──────────────┘                        └──────────────┘
              │                                       ↑
              │ rw                                    │ ro
              ↓                                       │
      ┌─────────────────────────┐                     │
      │ hermes-agent-src (vol)  │─────────────────────┘
      │ (agent's Python source) │
      └─────────────────────────┘
```

- `hermes-home` holds everything a Deployment keeps. Hermes Agent mounts it at
  `/home/hermes/.hermes`, GFIT-CoWork at `/home/hermeswebui/.hermes`.
- `hermes-agent-src` is filled from the agent image's `/opt/hermes` on first
  start. The GFIT-CoWork image doesn't ship the agent's Python deps: at
  startup it runs `uv pip install` against this volume to install them. The
  GFIT-CoWork mount is read-only; the agent container is the only writer.
- `admin-workspace` is the server's default Workspace at `/workspace`; no User
  works in it. A User's Workspaces live in their Profile.
- `./certs` (read-only) holds the AD CA certificate.

GFIT-CoWork publishes port 8787 on `127.0.0.1:<GFIT_PORT>` only, so people
reach it through the HTTPS reverse proxy. Hermes Agent's gateway (8642) is not
published on the host; GFIT-CoWork reaches it over the Deployment's own
network.

## What the multi-container setup isolates (and what it doesn't)

The two containers give you **process, network, and resource isolation** between the gateway and the chat UI:

- Each service has its own PID namespace and lifecycle — the agent process can crash without taking down the chat UI and vice versa.
- The gateway API (port 8642) is bound by the agent service only; GFIT-CoWork cannot bind it and reaches it over the Deployment's network.
- Restart policies, log streams, and container health checks are scoped per service.

What it does **not** isolate:

- **Filesystem boundary.** Both services share `hermes-home`, and GFIT-CoWork mounts the agent's installed source from `hermes-agent-src`. The GFIT-CoWork mount is read-only, but the agent service still has write access, and both services share the home volume.
- **UID/GID boundary.** Both services take their IDs from the same `UID`/`GID` in `.env`, so files written by one are readable by the other. If you align them to different UIDs you'll get permission errors on the shared volume.
- **Trust boundary on the agent source.** GFIT-CoWork installs Python dependencies from the shared `hermes-agent-src` volume at startup. The read-only mount means a compromised GFIT-CoWork cannot rewrite the agent source, but it does run code from that volume.
- **Users from each other.** All Profiles in one Deployment run their agents as the same OS user (ADR 0002).

The direct source mount is a compatibility bridge, not the long-term API contract. The current source/API boundary inventory and decoupling task list live in [`docs/rfcs/agent-source-boundary.md`](rfcs/agent-source-boundary.md) for [#2453](https://github.com/nesquena/hermes-webui/issues/2453). Keep the GFIT-CoWork-side agent source mount read-only unless you are intentionally doing local development; `docker_init.bash` warns at startup when that path is writable.

## How the image picks its UID and GID

The Deployment kit sets `WANTED_UID`/`WANTED_GID` for GFIT-CoWork and
`HERMES_UID`/`HERMES_GID` for Hermes Agent from the same `UID`/`GID` in `.env`
(the server's service user, `id -u` / `id -g`), so both containers agree on
who owns `hermes-home` (#1399). An explicitly supplied `WANTED_UID`/`WANTED_GID`
always wins and is never overwritten by detection — including the value
`1024`, which earlier versions treated as "unset".

Without explicit IDs the entrypoint detects them from the first mount that
resolves (#668, #569, #7027):

1. `$HERMES_WEBUI_STATE_DIR` (default `/app/data`)
2. `/home/hermeswebui/.hermes`, `$HERMES_HOME`, `/opt/data` — the shared
   hermes-home volume
3. `/workspace` — used only when nothing above resolves
4. `1024` — fallback default

Root-owned candidates (UID 0, e.g. a freshly created named volume) are skipped
at every step.

## Scheduled jobs and the gateway daemon

Scheduled cron ticks are not driven by GFIT-CoWork itself. Hermes Agent's
gateway daemon ticks the scheduler every 60 seconds; without one running,
scheduled jobs sit idle. "Run now" / "Trigger" buttons still work because
GFIT-CoWork handles those in-process. The Deployment kit runs the gateway
(`gateway run`) in the `hermes-agent` container, so scheduled jobs require the Hermes gateway daemon
that the kit already starts.

The kit wires the two containers together:

- Hermes Agent gets `API_SERVER_KEY` from `.env`. The agent only starts the
  gateway API listener (port 8642) when `API_SERVER_KEY` is a usable value
  (16+ characters) — `API_SERVER_ENABLED` alone does nothing.
- GFIT-CoWork gets the same value as `HERMES_WEBUI_GATEWAY_API_KEY`, so its
  health probe authenticates, and `HERMES_API_URL=http://hermes-agent:8642`,
  so it reaches the gateway over the Deployment's network.

The cron list itself is read from the shared `hermes-home` volume, not from the
gateway HTTP API. If the Tasks panel shows a gateway warning while the job list
loads, the warning is about scheduled ticking / gateway health, not about the
list endpoint.

**Symptom**: Cron jobs created in the Tasks panel never fire. System Settings or Tasks shows:

- Orange "Gateway not configured", or
- Red "Gateway metadata stale" when runtime metadata is stale, or
- Red "Gateway endpoint not reachable" when GFIT-CoWork has a gateway URL configured but cannot reach its health endpoint.

In older gateway builds, `gateway_state.json` can become stale and GFIT-CoWork may lose confidence even if the daemon is up.

**Fix**: Check `API_SERVER_KEY` in `.env` is set and at least 16 characters, then recreate the Deployment with `docker compose up -d --force-recreate`.

**Verify**: Once the gateway is up, the System Settings pill should turn green and the Tasks banner disappear. In the Deployment's folder:

```bash
docker compose exec hermes-agent hermes gateway status
docker compose logs --tail 200 hermes-agent
```

For container-to-container diagnostics, set one of `HERMES_API_URL` or `HERMES_WEBUI_GATEWAY_BASE_URL` in the `gfit-cowork` environment, then restart GFIT-CoWork.

If browser chat is routed through that gateway and you expect approval prompts for guarded tools, set the gateway chat backend and opt into the runs API path in the **WebUI service** (`gfit-cowork`):

```yaml
services:
  gfit-cowork:
    environment:
      - HERMES_WEBUI_CHAT_BACKEND=gateway
      - HERMES_WEBUI_GATEWAY_BASE_URL=http://hermes-agent:8642
      - HERMES_WEBUI_GATEWAY_USE_RUNS_API=true
```

`HERMES_WEBUI_GATEWAY_USE_RUNS_API=true` is required for gateway approval cards because approval-capable gateway runs emit approval requests on the runs API transport. Leaving it unset keeps browser chat on the legacy chat-completions path.

Refs #2785, #4483.

## Upgrading

### Upgrading GFIT-CoWork

Pull the source, rebuild the image, then recreate each Team's Deployment:

```bash
git -C /opt/gfit-cowork/src pull
docker build -t gfit-cowork:latest /opt/gfit-cowork/src
# in each Team's folder:
docker compose up -d
```

### Upgrading the agent container

The `hermes-agent-src` named volume is initialised from the agent image's `/opt/hermes` on first `up`. Docker reuses the volume verbatim on every subsequent `up` — **even after `docker pull` of a newer agent image**. The cached volume content masks the new image's source tree, so a fresh `docker pull` of `nousresearch/hermes-agent:latest` does not by itself give you the new agent code, dependencies, or entrypoint.

This is the root cause of [#1416](https://github.com/nesquena/hermes-webui/issues/1416): the symptom looked like a missing entrypoint, but the entrypoint was actually present in the new image and hidden behind the stale named volume.

To upgrade the agent image cleanly, drop the source volume before recreating. In the Team's folder:

```bash
docker compose down
docker volume rm gfit-<TEAM>_hermes-agent-src
docker compose pull
docker compose up -d
```

The `hermes-home` volume (Profiles, sessions, state) is left untouched — only `hermes-agent-src` (the agent's installed Python source) is recreated.

### Compatibility and version pinning

GFIT-CoWork is coupled to Hermes Agent internals until the compatibility boundary work in [#1925](https://github.com/nesquena/hermes-webui/issues/1925) and [#2491](https://github.com/nesquena/hermes-webui/issues/2491) lands. Treat the two images as a release pair: upgrade or pin them together. If you pin `HERMES_AGENT_IMAGE` to a fixed tag in `.env`, rebuild GFIT-CoWork from the matching source revision, and perform the agent-volume refresh above whenever you upgrade the agent image.

If you see behavior issues after an upgrade, capture both the GFIT-CoWork and Hermes Agent versions.

## Optional GPU runtime image

The default GFIT-CoWork Docker image stays CPU-only. GPU user-space packages
are installed only when you build a custom image with the opt-in build arg:

```bash
docker build --build-arg INSTALL_GPU_LIBS=1 -t gfit-cowork:gpu .
```

Point the Deployment at it with `GFIT_COWORK_IMAGE=gfit-cowork:gpu` in `.env`.

That build path installs VA-API basics (`libva2`, `vainfo`), AMD Mesa VA-API
drivers (`mesa-va-drivers`), and the Intel non-free media driver when that
package is available from the configured Debian repositories. NVIDIA host
runtime tooling is not installed into the app image; use the NVIDIA Container
Toolkit on the host and pass GPUs through at runtime.

GPU passthrough still depends on host drivers, Docker runtime support, and
device mappings. The commands below are configuration guidance for a suitable
Linux Docker host; they are not a claim that native GPU passthrough was verified
in this workspace.

### Intel and AMD VA-API

Expose the host render devices and add the runtime user to the common video and
render groups:

```bash
docker run --rm \
  --device /dev/dri:/dev/dri \
  --group-add video \
  --group-add render \
  gfit-cowork:gpu vainfo
```

For the Deployment, add the same mapping to the `gfit-cowork` service:

```yaml
services:
  gfit-cowork:
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - video
      - render
```

`vainfo` should list the VA-API driver and supported profiles when the host
driver stack and container permissions are correct. The container entrypoint
preserves Docker-provided supplemental groups before it drops privileges to the
`hermeswebui` runtime user, so the GFIT-CoWork process keeps access to `/dev/dri`.

### NVIDIA

Install and configure the NVIDIA Container Toolkit on the host first, then use
Docker's GPU runtime flag:

```bash
docker run --rm --gpus all gfit-cowork:gpu nvidia-smi
```

For the Deployment, enable GPU access on the `gfit-cowork` service:

```yaml
services:
  gfit-cowork:
    gpus: all
```

If `nvidia-smi` is unavailable or reports no devices, fix the host NVIDIA driver
and container toolkit setup before debugging GFIT-CoWork. The container image
only supplies GFIT-CoWork plus optional user-space media libraries; it cannot
provide host kernel drivers or the NVIDIA runtime.

## What goes wrong (and how to fix it)

### 1. "Permission denied" at startup (#1399)

**Symptom**: A container starts but immediately crashes, logs show:
```
PermissionError: [Errno 13] Permission denied: '/home/hermeswebui/.hermes/...'
```

**Cause**: Hermes Agent and GFIT-CoWork run as different UIDs, so one cannot read what the other wrote to `hermes-home`.

**Fix**: Set `UID` and `GID` in `.env` to the server's service user (`id -u` / `id -g`) and recreate both containers with `docker compose up -d --force-recreate`. Do not set `WANTED_UID` or `HERMES_UID` separately.

### 2. ".env file mode 0640 → permission denied" (#1389)

**Symptom**: A credential `.env` inside `hermes-home` was given a group-readable mode such as `0640`, and startup logs show:
```
[security] fixed permissions on .env (0o640 -> 0600)
failed to load .env: open .env: permission denied
```

**Cause**: GFIT-CoWork's `fix_credential_permissions()` startup hook enforces 0600 by default. This is the right thing for a clean install but conflicts with operator-set modes.

**Fix**: Set one of these under `environment:` of the `gfit-cowork` service in the Deployment's `docker-compose.yml` — **not** in `.env`, which both services read (`env_file: .env`):
- `HERMES_SKIP_CHMOD=1` — bypass the fixer entirely
- `HERMES_HOME_MODE=0640` — allow group bits, only strip world-readable

Both are documented in `api/startup.py::fix_credential_permissions()`.

> ⚠️ **Multi-container warning**: `HERMES_HOME_MODE` has DIFFERENT semantics in the agent image vs. GFIT-CoWork:
> - **GFIT-CoWork**: credential FILE mode threshold (`0640` allows group bits on `.env`)
> - **Agent**: `HERMES_HOME` *directory* mode (default `0700`)
>
> `0640` on a directory has no owner-execute bit, so the agent can't traverse its own home → bricked. Putting `HERMES_HOME_MODE=0640` in `.env` gives it to `hermes-agent` too. If the agent needs one, set it on the `hermes-agent` service only, as `0750` (group-traversable) or `0701` (x-only).

### 3. "GFIT-CoWork can't find agent source" (#858)

**Symptom**: GFIT-CoWork logs at startup:
```
!! WARNING: hermes-agent source not found.
!!   Looked in: /home/hermeswebui/.hermes/hermes-agent
!!              /opt/hermes
```

**Cause**: The agent's source (`/opt/hermes` inside the agent container) reaches GFIT-CoWork through the `hermes-agent-src` named volume. If that volume was replaced with a bind mount, or removed while GFIT-CoWork was running, the path won't resolve.

**Fix**: Use the named volumes in `deploy/docker-compose.yml` as they are. The agent container writes its source to `/opt/hermes`, and GFIT-CoWork mounts that volume read-only at `/home/hermeswebui/.hermes/hermes-agent`. After removing the volume, recreate both containers so the agent fills it again first.

### 4. "Tools (git, node, etc.) missing" (#681)

**Symptom**: You ask the agent to run `git status` in chat and it errors with `command not found`.

**Cause**: This is **architectural, not a bug**. Agent processes started by GFIT-CoWork run **inside the `gfit-cowork` container**, not the agent container. The image doesn't include every tool by design (it's a UI image, not a tool host).

**Fix**: Extend the `Dockerfile` to install the tools you need, rebuild `gfit-cowork:latest`, and recreate the Deployment.

### 5. "config.yaml not loaded"

**Symptom**: GFIT-CoWork shows "no model configured" or doesn't pick up your custom providers.

**Cause**: Either the file isn't readable (UID/GID issue, see #1) or it's not in the expected path inside the container.

**Fix**:
- Verify: `docker compose exec gfit-cowork ls -la /home/hermeswebui/.hermes/config.yaml`
- If it doesn't exist: no model has been set up yet — run `docker compose exec hermes-agent hermes model` (deploy/README.md).
- If it exists but is unreadable: see #1 for the UID/GID fix.

### 6. "API base URL set to localhost fails from Docker" (#3012)

**Symptom**: A provider, local model server, webhook, or custom API works on the server at `http://localhost:<port>`, but fails when the same URL is configured in GFIT-CoWork.

**Cause**: Inside a container, `localhost` means *that container*, not the server. GFIT-CoWork cannot reach server services through `127.0.0.1` unless the service is running inside the same container.

**Fix**: Point GFIT-CoWork at the host gateway name instead:

- Docker Desktop on macOS/Windows: `http://host.docker.internal:<port>`
- Podman: `http://host.containers.internal:<port>`
- Linux Docker Engine: either publish the host service on the Docker bridge address, or add a host-gateway alias to the `gfit-cowork` service:

```yaml
services:
  gfit-cowork:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Then configure the URL as `http://host.docker.internal:<port>`. Also ensure the host service binds to an address reachable from containers (not only a loopback interface the Docker bridge cannot reach) and that the server's firewall allows the connection.

## Reference

- [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) — one Deployment: Hermes Agent + GFIT-CoWork
- [`deploy/team.env.example`](../deploy/team.env.example) — the config file for one Deployment
- [`deploy/README.md`](../deploy/README.md) — the step-by-step guide
- [`Dockerfile`](../Dockerfile) — the GFIT-CoWork image
- [`docker_init.bash`](../docker_init.bash) — container entrypoint script

## Related issues

- #1416 — agent-image upgrade requires removing `hermes-agent-src` named volume (see [Upgrading the agent container](#upgrading-the-agent-container))
- #1389 — `HERMES_HOME_MODE` override (agent honors `HERMES_SKIP_CHMOD` and `HERMES_HOME_MODE`)
- #1399 — UID alignment between the agent and GFIT-CoWork containers
- #3012 — host `localhost` API URLs fail from Docker containers (use `host.docker.internal` / `host.containers.internal`)
- #3243 — optional GPU runtime image/docs for containerized acceleration workloads
- #858 — `/opt/hermes` agent source path confusion
- #681 — tools running in the GFIT-CoWork container, not the agent container (architectural)
- #668 — auto-detect UID/GID from mounted volume
- #569 — UID/GID detection priority order
- #7027 — state dir probed before `/workspace` in UID/GID detection
