Status: ready-for-agent

# Docker-only development: build, debug and test GFIT-CoWork in Linux containers

## Problem Statement

GFIT-CoWork runs on Linux in Docker on a Team's server, but developers run, debug and test it natively on Windows, macOS or Linux through `start.ps1`, `start.sh`/`ctl.sh` and `bootstrap.py`. The two worlds differ: on native Windows the test suite has about 277 failures that never happen on the server (symlink privilege, bash/WSL, the deploy kit), a developer must install Python 3.14 and Hermes Agent by hand, and a change can pass locally and still break in the image. The repo also carries and tests a large amount of launcher and platform-detection code that no Deployment ever runs. Separately, the deploy kit fixes a Deployment's data to its own folder, a UID taken from whoever ran `new-deployment`, and Compose-generated container names, which the developer wants to change.

## Solution

Every way of running GFIT-CoWork is a Linux container. A developer creates a **Development Deployment** with the same deploy kit that creates a Team's Deployment, plus one flag, and runs it with the same `deploy/cowork` command. The Development Deployment mounts the developer's checkout, uses the mock Directory instead of the company AD, serves plain HTTP on `localhost`, and exposes a debugger port for VS Code. The test suite runs in a throwaway container built from the same image, started by the same `./scripts/test.sh` command developers already use. The native launchers, the bootstrap, and the native-Windows/WSL code paths are deleted.

The deploy kit also gains configurable home and workspace paths, a fixed UID/GID of 10000, readable container names and persistent `/opt/data` volumes. A production Deployment's network exposure does not change.

## User Stories

1. As a developer, I want to create a Development Deployment with one `new-deployment --dev` command, so that I don't have to install Python or Hermes Agent on my machine.
2. As a developer, I want `new-deployment --dev` to generate the mock Directory's throwaway CA and fill in the Directory values, so that login works without editing `.env` by hand.
3. As a developer, I want to start, stop, restart and view logs of my Development Deployment with `deploy/cowork dev ...`, so that I use the same commands an Operator uses on the server.
4. As a developer, I want `deploy/cowork` to add the development overlay automatically when the Deployment is marked as a Development Deployment, so that I never have to pass extra `-f` files.
5. As a developer, I want my checkout bind-mounted into the GFIT-CoWork container, so that a change to JS or CSS shows up on browser refresh without rebuilding the image.
6. As a developer, I want a Python change to take effect after `deploy/cowork dev restart gfit-cowork`, so that the edit loop is seconds, not a full image build.
7. As a developer, I want to attach the VS Code debugger to the running server through a checked-in "Attach" launch configuration, so that I can set breakpoints in server code.
8. As a developer, I want the debugger to exist only in the development image, so that production images never carry it.
9. As a developer, I want the mock Directory to start as part of my Development Deployment, so that one `up` gives me a working login.
10. As a developer, I want to log in at `http://localhost:<port>` without the reverse proxy and without HTTPS-only cookies, so that I don't need certificates for the app on my machine.
11. As a developer, I want to run Setup once with `deploy/cowork dev exec hermes-agent hermes setup` and have it persist across `down`/`up`, so that I don't re-enter my provider key every day.
12. As a developer, I want my Development Deployment to run the same Hermes Agent image version as production, so that agent behaviour matches what Users get.
13. As a developer, I want every published port of my Development Deployment bound to `127.0.0.1` by default, so that nobody on my network can log in with the mock Directory's published passwords and spend my provider key.
14. As a developer, I want to set `DEV_BIND=0.0.0.0` when I need to, so that I can test calls between two machines (for example, A2A later).
15. As a developer, I want the Development Deployment to also publish the gateway, A2A and the Dashboard on its bind address, so that I can inspect and exercise them directly.
16. As an Operator, I want `deploy/cowork` to refuse the development overlay when the Deployment's hostname is not `localhost`, so that a Team's server can never run in development mode by mistake.
17. As a developer, I want `./scripts/test.sh` to keep its current interface (`--changed`, sharding, extra pytest args), so that AGENTS.md instructions and habits don't change.
18. As a developer, I want `./scripts/test.sh` to run the suite in a fresh container that mounts only the source, so that tests can never read or overwrite my Development Deployment's state or my real Hermes home.
19. As a developer, I want the test container to include Hermes Agent's source at the pinned version, so that tests that need the agent run instead of being skipped.
20. As a developer, I want the test container to include the Docker CLI and Compose plugin, so that tests that render the Compose file run instead of being skipped silently.
21. As a developer, I want `./scripts/test.sh --browser` to run the headless browser smoke in a container that has Playwright and Chromium, so that the browser check needs nothing installed on my machine.
22. As a developer on Windows, I want every shell script the kit and the runner use to keep LF line endings on checkout, so that mounting them into Linux containers does not break their shebangs.
23. As a developer on Windows, I want Git Bash to be the only host shell I need, so that there is one set of launcher commands for every platform.
24. As a maintainer, I want `start.ps1`, `start.sh`, `ctl.sh`, `bootstrap.py`, native-Windows/WSL code paths and their tests removed, so that the repo no longer carries code no Deployment runs.
25. As a maintainer, I want an ADR that records that native running is unsupported and must not be re-added, so that a future reviewer does not restore it.
26. As an Operator, I want to set `HERMES_HOME_DIR` and `HERMES_WORKSPACE_DIR` in a Deployment's `.env`, so that I can keep a Deployment's data on another disk.
27. As an Operator, I want those paths to default to the Deployment folder's `home/` and `workspace/`, so that a Deployment made the usual way still backs up as one folder.
28. As an Operator, I want `deploy/cowork` to apply the same safety checks (no symlink, writable key file) to a custom home path as to the default one, so that moving the data does not weaken the gateway-key guard.
29. As an Operator, I want every container of a Deployment to run as UID/GID 10000, so that ownership is the same on every server regardless of who ran `new-deployment`.
30. As an Operator, I want `new-deployment` to give `home/` and `workspace/` to UID/GID 10000 (run with `sudo`), so that the containers can write to them from the first `up`.
31. As an Operator, I want containers named `gfit-<team>-agent`, `gfit-<team>-cowork` and `gfit-<team>-dashboard`, so that `docker logs` and `docker exec` are easy to type and read.
32. As an Operator, I want the agent's and the Dashboard's `/opt/data` kept in named volumes, so that what the agent installs for itself survives a container recreate.
33. As an Operator, I want a production Deployment's exposure unchanged (app on loopback behind the reverse proxy, Dashboard on loopback over an SSH tunnel, gateway and A2A not published), so that this change does not open anything on the company network.
34. As a contributor, I want the testing, setup and agent-checklist docs to describe only the container workflow, so that new contributors do not try the native path.

## Implementation Decisions

- **New ADR** supersedes ADR 0005 (supported ways to run) and amends ADR 0008 (Deployment storage). It records: the deploy kit is the one way to run GFIT-CoWork, for Team Deployments and Development Deployments alike; native launchers are removed and must not be re-added; the home/workspace path override, UID/GID 10000, container names and `/opt/data` volumes; and that production exposure is unchanged. ADR 0003 (A2A only via the Reception) and ADR 0006 are unchanged.
- **Glossary**: "Development Deployment" is now a term in the domain glossary. Docs and code use it instead of "local", "native" or "dev stack".
- **Line endings**: the repo attributes force LF for the extensionless scripts in the deploy kit and the scripts folder, not only `*.sh`/`*.bash`.
- **Image**: the root Dockerfile becomes multi-stage. The production image stays the default/final target and is built as today. A `dev` target adds the dev requirements, `debugpy`, the Docker CLI with the Compose plugin (no daemon), and Hermes Agent's source copied from the same pinned agent image. A `browser` target extends `dev` with Playwright and Chromium.
- **Development Deployment creation**: `new-deployment --dev <team> <N>` creates a normal Deployment folder and additionally writes `GFIT_DEV=1`, `GFIT_HOSTNAME=localhost`, the mock Directory values, and the mock Directory's throwaway CA as the Deployment's AD CA certificate.
- **Overlay selection**: `deploy/cowork` adds the development overlay (kept inside the deploy kit, not at the repo root) when `.env` has `GFIT_DEV=1`, and refuses to start if `GFIT_DEV=1` and `GFIT_HOSTNAME` is not `localhost`.
- **Development overlay**: builds the `dev` target; bind-mounts the checkout over the app; runs the server under `debugpy` listening on a published debug port; adds the mock Directory as a service; turns off HTTPS-only cookies and the forwarded-for trust; publishes the app, the Dashboard, the gateway and A2A on `${DEV_BIND:-127.0.0.1}`. The standalone mock Directory Compose file is retired in favour of the overlay service.
- **Edit loop**: no file watcher. Static assets are live; Python changes need a container restart.
- **Debugging**: a checked-in VS Code "Attach" configuration maps the container's app path to the workspace root.
- **Setup**: unchanged mechanism. `hermes setup` runs through `deploy/cowork <team> exec hermes-agent`, once per home folder; the kit adds no automatic Setup step.
- **Home and workspace paths**: Compose reads `HERMES_HOME_DIR` and `HERMES_WORKSPACE_DIR` from `.env`, defaulting to the Deployment folder's `home` and `workspace`. Every guard `deploy/cowork` applies to `home/` and `home/.env` today resolves through `HERMES_HOME_DIR`.
- **UID/GID**: all services use 10000:10000 by default. `new-deployment` writes 10000 and chowns `home/` and `workspace/` to it, so it needs root; it fails with a clear message if it cannot chown.
- **Container names**: set explicitly as `gfit-<team>-agent`, `gfit-<team>-cowork`, `gfit-<team>-dashboard` (and `gfit-<team>-ldap` in development).
- **`/opt/data`**: the agent and the Dashboard each get a named volume for `/opt/data`, treated like the agent-source volume (disposable, not part of backup).
- **Production exposure**: unchanged. App and Dashboard on `127.0.0.1`, gateway and A2A not published.
- **Test runner**: `./scripts/test.sh` on the host builds the `dev` target (or `browser` for `--browser`) and runs the existing in-container runner in a throwaway container. Only the checkout is mounted; `HERMES_HOME` and the WebUI state dir point at container-local temp paths; no Deployment folder or host Hermes home is mounted. Existing flags (`--changed`, shard options, pass-through pytest args) are forwarded unchanged. Docker is required on the host; the runner fails with a clear message if it is missing.
- **Removal**: `start.ps1`, `start.sh`, `ctl.sh`, `bootstrap.py`, the runtime WSL/native-Windows detection and Windows-only branches in the server code, and the tests that only guard them are deleted in one change. Tests that guard behaviour the container still has are kept or moved onto the deploy kit.
- **Docs**: AGENTS.md, the testing guide, the setup guide, the setup-agent checklist, the README and the deploy guide describe only the container workflow.

## Testing Decisions

- A good test drives the kit or the runner from the outside and asserts on what it produces: files written, their permissions, the exit status and message, and the `docker` commands it issues or the Compose config it renders. Tests do not inspect script internals or the overlay's YAML structure beyond what `docker compose config` resolves.
- **Seam 1 — deploy kit scripts with a fake `docker` on `PATH`** (prior art: the `kit` and `ready` fixtures and `FAKE_DOCKER` in the existing deployment test module). Covers `new-deployment --dev` output, overlay selection, the non-localhost refusal, `DEV_BIND`, the home/workspace override and its symlink/write guards, UID/GID 10000 and the chown failure path.
- **Seam 2 — rendered Compose config via `docker compose config`** (prior art: the existing template-acceptance test). Covers port bindings for production vs. development (including `DEV_BIND`), container names, `/opt/data` volumes, home/workspace mounts, and the source bind-mount and debug port in development only. These tests now run inside the test container because the `dev` target ships the Docker CLI.
- **Seam 3 — `./scripts/test.sh` with a fake `docker` on `PATH`**. Covers: the right target is built, only the checkout is mounted, no state directory is mounted, flags are forwarded, `--browser` selects the browser target, and a missing Docker fails clearly.
- Existing deployment tests that pin the gateway-key behaviour must keep passing against the new paths and UID.
- Manual, reported in the PR (ADR 0007): build both targets; `up` a Development Deployment from scratch on Windows (Docker Desktop + Git Bash); run Setup; log in through the mock Directory; hit a breakpoint from VS Code; run the full suite and `--browser` through `./scripts/test.sh`; `up` a production-mode Deployment and confirm nothing is published beyond loopback.

## Out of Scope

- Upgrading Hermes Agent from v0.21.6 to v0.21.7 in production and development, and re-verifying the gateway-key behaviour and the agent dependency audit on it (separate ticket; the image pin is a single value this spec does not change).
- Investigating `HERMES_WRITE_SAFE_ROOT`, which the agent image sets to `/opt/data` while the kit moves `HERMES_HOME` elsewhere (separate ticket).
- Inbound A2A from other Teams and the Reception Profile (ADR 0003, phase two). The Development Deployment only publishes the A2A port for future testing.
- A file watcher or automatic reload.
- A PowerShell version of the kit or the runner.
- Changing the reverse proxy or a production Deployment's exposure.

## Further Notes

- On Docker Desktop (Windows/macOS) bind mounts cross a VM boundary: Hermes Agent turns off SQLite WAL for databases in `home/` (ADR 0008) and file access is slower. Do not measure capacity on a Development Deployment.
- The mock Directory's sample passwords are published in the repo; that is why the default bind is loopback.
- Suggested order of work: line endings; `dev` target and the containerised test runner; `new-deployment --dev`, the development overlay and the debugger; deploy-kit changes (paths, UID/GID, names, `/opt/data`); `browser` target; removal of native launchers and code; docs.
