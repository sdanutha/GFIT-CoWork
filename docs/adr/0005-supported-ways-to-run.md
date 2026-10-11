# Supported ways to run: the deploy/ kit on a server, start.sh/ctl.sh and start.ps1 for development

GFIT-CoWork runs in exactly three ways. On a server, every Team's Deployment runs from the `deploy/` kit: Hermes Agent and GFIT-CoWork in Docker Compose, one Deployment per Team (ADR 0002), behind the Caddy reverse proxy. For development, `start.sh`/`ctl.sh` run it on Linux or macOS and `start.ps1` runs it on native Windows, with `dev/mock-ldap` as the development Directory. We chose this because every Deployment must run GFIT-CoWork's own image with the Directory configured (ADR 0004), and only `deploy/` does that. Upstream's other ways to run did not: two of its root Compose setups pulled Upstream's published image, so an Admin following them got Upstream's code, and its NixOS module could not configure the Directory at all.

## Consequences

- Not supported, and removed from the repo: Upstream's three root Compose setups (`docker-compose.yml`, `docker-compose.two-container.yml`, `docker-compose.three-container.yml`) with their example env file, the Nix flake and NixOS module, and the WSL autostart kit. Git history keeps them (ADR 0001). Do not re-add them.
- `start.ps1` and its native-Windows CI check stay, and so does bootstrap's runtime WSL detection that tells native Windows apart from WSL. A review that finds them unused on the server should not delete them.
- The Docker smoke workflow brings up `deploy/`, built from the change's own Dockerfile, so CI protects the Deployment every Team actually runs.
- The README and `docs/docker.md` describe only `deploy/`. What they say about the image itself (volumes, UID/GID, the read-only agent source) applies to the image as `deploy/` runs it.
- Tests that guarded the root Compose files either guard `deploy/` instead, when `deploy/` does the same thing, or are gone, when only the Upstream setups did it.
