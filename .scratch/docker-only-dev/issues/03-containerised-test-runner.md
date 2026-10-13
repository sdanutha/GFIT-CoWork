# 03: The test suite runs in a throwaway container

**What to build:** A dev image target and a host ./scripts/test.sh that runs the existing runner inside a fresh container that mounts only the checkout.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Dockerfile dev target: dev requirements, Hermes Agent source at the pinned version, Docker CLI + Compose plugin
- [ ] ./scripts/test.sh on the host builds the dev target and docker-runs itself with the same arguments; no state is mounted
- [ ] Missing docker fails with a clear message
- [ ] Seam 3 tests with a fake docker
- [ ] AGENTS.md and TESTING.md describe the container runner
