# 03: Open and read a Hermes Thread

**What to build:** A developer can choose a Thread in the current Workspace and read its durable Hermes history through GFIT CoWork, with navigation that restores the selected Thread without creating a local transcript copy.

**Blocked by:** 02: Open a Workspace and browse its Threads.

**Status:** done

- [x] Selecting a Thread loads and renders its Hermes history in message order.
- [x] Thread identity and history come from Hermes rather than CoWork preference storage.
- [x] Returning to the app restores the current Workspace and selected Thread when they remain available.
- [x] Unavailable, deleted, or unreadable Threads present safe recovery guidance.
- [x] Tests exercise selection, history rendering, restoration, and error behavior through the host interface and a fake gateway.

**Notes:** History comes from the real gateway `session.history` (see `docs/agents/hermes-api.md`); only navigation (last Workspace + selected Thread) is persisted client-side, never the transcript. The gateway does not distinguish a deleted session from other read failures, so the real gateway folds both to the `unreadable` recovery state; the `not-found` reason is carried through the contract for when Hermes exposes a distinguishable error.
