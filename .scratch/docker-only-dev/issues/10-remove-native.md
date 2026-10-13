# 10: Remove native running

**What to build:** Delete start.ps1, start.sh, ctl.sh, bootstrap.py, the native-Windows/WSL code paths and the tests that only guard them; docs describe only containers.

**Blocked by:** 01, 04, 09

**Status:** done

- [x] Launchers and native-only code gone
- [x] Remaining tests green in the container
- [x] README, setup, troubleshooting, setup-agent checklist updated

## Comments

Left in place on purpose (UNSURE in the inventory): the dir-fd fallbacks in
`api/workspace.py` (`_DIR_FD_OK`, `_O_BINARY`) and the path-based branch of the
routes move handler. They are dead on Linux but sit in security-relevant
workspace containment code; removing them deserves its own reviewed change.
The macOS roots in the workspace block-list also stay (cheap defence).
