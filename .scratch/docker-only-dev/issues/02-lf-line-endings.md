# 02: Kit and runner scripts keep LF on checkout

**What to build:** On a Windows checkout with core.autocrlf=true, the extensionless scripts in the deploy kit and scripts folder still check out with LF, so they run inside Linux containers.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Git attributes force eol=lf for the deploy kit's and scripts folder's extensionless scripts
- [ ] A test checks git resolves eol=lf for them
- [ ] Existing files are renormalized
