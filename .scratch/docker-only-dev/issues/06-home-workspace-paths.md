# 06: Configurable home and workspace paths

**What to build:** HERMES_HOME_DIR and HERMES_WORKSPACE_DIR in a Deployment's .env, defaulting to the folder's home/ and workspace/; cowork's gateway-key guards follow the configured home.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Compose mounts the configured paths in every service
- [ ] cowork refuses a symlinked or missing configured home and writes the key into it
- [ ] Seam 1 and 2 tests; deploy guide
