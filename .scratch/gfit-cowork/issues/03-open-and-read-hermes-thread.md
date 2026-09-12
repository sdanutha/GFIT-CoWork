# 03: Open and read a Hermes Thread

**What to build:** A developer can choose a Thread in the current Workspace and read its durable Hermes history through GFIT CoWork, with navigation that restores the selected Thread without creating a local transcript copy.

**Blocked by:** 02: Open a Workspace and browse its Threads.

**Status:** ready-for-agent

- [ ] Selecting a Thread loads and renders its Hermes history in message order.
- [ ] Thread identity and history come from Hermes rather than CoWork preference storage.
- [ ] Returning to the app restores the current Workspace and selected Thread when they remain available.
- [ ] Unavailable, deleted, or unreadable Threads present safe recovery guidance.
- [ ] Tests exercise selection, history rendering, restoration, and error behavior through the host interface and a fake gateway.
