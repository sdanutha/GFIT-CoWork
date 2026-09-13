# 02: Open a Workspace and browse its Threads

**What to build:** A developer can enter an absolute local folder, make it the current Workspace, reopen a Recent Workspace, and browse only the Hermes Threads that belong to that Workspace.

**Blocked by:** 01: Launch GFIT CoWork with Hermes health.

**Status:** done

- [x] An existing accessible absolute folder becomes the current Workspace; invalid or inaccessible paths explain why they cannot be opened.
- [x] Recent Workspaces persist as navigation preferences without copying Workspace data or Thread history.
- [x] The Thread list is scoped to the current Workspace and excludes unrelated local or messaging sessions.
- [x] Each listed Thread provides an understandable title, recency, and activity status.
- [x] Fake-gateway tests cover path validation, scope filtering, recent-list behavior, and host-to-browser error states.

**Notes:** Recent Workspaces are a client-side navigation preference (localStorage), covered by client tests rather than fake-gateway tests. The real gateway's Hermes Thread listing (`listThreads`) is provisional and must be verified against a running Hermes; a failure yields an empty list so the Workspace still opens.
