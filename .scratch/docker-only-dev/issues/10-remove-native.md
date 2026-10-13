# 10: Remove native running

**What to build:** Delete start.ps1, start.sh, ctl.sh, bootstrap.py, the native-Windows/WSL code paths and the tests that only guard them; docs describe only containers.

**Blocked by:** 01, 04, 09

**Status:** ready-for-agent

- [ ] Launchers and native-only code gone
- [ ] Remaining tests green in the container
- [ ] README, setup, troubleshooting, setup-agent checklist updated
