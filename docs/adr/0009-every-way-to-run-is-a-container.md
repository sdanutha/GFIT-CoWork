# Every way to run GFIT-CoWork is a container, for Team and Development Deployments alike

GFIT-CoWork now runs only in Linux containers. A Team's Deployment and a developer's **Development Deployment** are both made by the `deploy/` kit: `deploy/new-deployment` creates the folder (with `--dev` for a Development Deployment) and `deploy/cowork` runs it. When the Deployment's `.env` has `GFIT_DEV=1`, `deploy/cowork` adds the development overlay, which builds the image's `dev` target, mounts the developer's checkout over the app, runs the server under `debugpy`, adds the mock Directory as a service, and serves plain HTTP on `localhost`. The test suite runs the same way: `./scripts/test.sh` on the host builds the `dev` target and runs itself in a throwaway container that mounts only the checkout. `start.ps1`, `start.sh`, `ctl.sh`, `bootstrap.py` and the code that told native Windows, WSL and macOS apart are deleted.

We chose this because the native ways to run differed from the server in ways that cost real time. On native Windows the suite had about 277 failures the server never has (symlink privilege, bash and WSL, the deploy kit), a developer had to install Python 3.14 and Hermes Agent by hand, and a change could pass locally and still break in the image. The repo also carried and tested launchers and platform detection that no Deployment ever ran. With one kit for every Deployment, a developer runs what a Team runs.

Supersedes ADR 0005. Amends ADR 0008 (see below). ADR 0003, 0006 and 0007 are unchanged.

## Considered Options

- **Keep native development, use Docker only on the server (ADR 0005).** Rejected for the reasons above.
- **Run the app in Docker, keep the test suite native.** Rejected: it keeps a native Python, the native-only code and the Windows-only failures, which is most of the cost.
- **A separate Compose file for development, apart from `deploy/`.** Rejected: the two would drift, and ADR 0005's rule against a root Compose file still holds. The overlay lives inside the kit and is chosen by the Deployment's own `.env`.
- **Run the tests inside the running Development Deployment (`exec`).** Rejected: its `HERMES_HOME` is the developer's Development Deployment state, which tests could overwrite.

## Consequences

- **Not supported, and removed:** `start.ps1`, `start.sh`, `ctl.sh`, `bootstrap.py`, runtime WSL/native-Windows detection, and the tests that only guarded them. Git history keeps them. Do not re-add a native launcher or a PowerShell copy of the kit.
- **Host requirements:** Docker (Docker Desktop on Windows and macOS) and a bash. On Windows that bash is Git Bash. The kit's and the runner's scripts are checked out with LF on every platform (`.gitattributes`), because they run inside Linux containers.
- **A Development Deployment never runs on a server.** `deploy/cowork` refuses the overlay unless `GFIT_HOSTNAME` is `localhost`. By default it publishes everything (the app, the Dashboard, the gateway, A2A and the debugger) on `127.0.0.1` only. `DEV_BIND=0.0.0.0` in its `.env` opens them to the network, for example to test A2A between two machines. Loopback is the default because the mock Directory's sample passwords are in the repo. Publishing A2A there, without a Reception, does not go against ADR 0003: a Development Deployment holds no real User's memory or files, which is what ADR 0003 protects.
- **Setup in a Development Deployment is the Operator's Setup:** `deploy/cowork <team> exec hermes-agent hermes setup`, once per home folder. The kit adds no automatic step.
- **The edit loop:** static files are live; a Python change needs `deploy/cowork <team> restart gfit-cowork`. There is no file watcher.
- **The test runner:** `./scripts/test.sh` keeps its arguments (`--changed`, `--parallel`, `--serial`, pytest arguments) and adds `--browser`, which uses the image's `browser` target (Playwright and Chromium). Docker is required to run the suite. The `dev` target carries the Docker CLI and the Compose plugin (no daemon), so the tests that render the Compose file run in the container.
- **Amends ADR 0008:**
  - `HERMES_HOME_DIR` and `HERMES_WORKSPACE_DIR` in `.env` may move `home/` and `workspace/` out of the Deployment's folder. They default to the folder, which keeps ADR 0008's "one folder" backup; a Deployment whose data lives elsewhere must back that path up too. `deploy/cowork` applies the same checks to the configured home as to `home/` (not a symlink, a regular `.env`, written atomically).
  - Every container runs as UID/GID 10000, not the Operator's `id -u`/`id -g`. `deploy/new-deployment` gives `home/` and `workspace/` to 10000:10000, so on a Linux server it runs as root (`sudo`), and the Operator needs `sudo` to read those folders. `GFIT_UID`/`GFIT_GID` pick another service user. A Development Deployment on a Linux machine runs as the developer instead, because it mounts their checkout and must write to it; Docker Desktop does not map owners, so there it keeps 10000. A Deployment made before this keeps the UID/GID its `.env` already names.
  - Containers are named `gfit-<team>-agent`, `gfit-<team>-cowork` and `gfit-<team>-dashboard`.
  - The agent's and the Dashboard's `/opt/data` (the agent image's own home, where it installs tools for itself) are kept in named volumes. Like the agent source volume, they are disposable and not part of a backup.
- **Production exposure is unchanged:** GFIT-CoWork and the Dashboard on `127.0.0.1` only, the gateway and A2A not published. Inbound A2A waits for the Reception (ADR 0003).
- **Docker Desktop:** bind mounts cross a VM boundary, so Hermes Agent turns off SQLite WAL in `home/` (ADR 0008) and file access is slower. Do not measure capacity on a Development Deployment.
