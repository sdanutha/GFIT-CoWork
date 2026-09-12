# 02: Open a Workspace and browse its Threads

**What to build:** A developer can enter an absolute local folder, make it the current Workspace, reopen a Recent Workspace, and browse only the Hermes Threads that belong to that Workspace.

**Blocked by:** 01: Launch GFIT CoWork with Hermes health.

**Status:** ready-for-agent

- [ ] An existing accessible absolute folder becomes the current Workspace; invalid or inaccessible paths explain why they cannot be opened.
- [ ] Recent Workspaces persist as navigation preferences without copying Workspace data or Thread history.
- [ ] The Thread list is scoped to the current Workspace and excludes unrelated local or messaging sessions.
- [ ] Each listed Thread provides an understandable title, recency, and activity status.
- [ ] Fake-gateway tests cover path validation, scope filtering, recent-list behavior, and host-to-browser error states.
