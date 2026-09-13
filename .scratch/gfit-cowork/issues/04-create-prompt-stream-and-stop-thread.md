# 04: Create, prompt, stream, and stop a Thread

**What to build:** A developer can create a Thread in the current Workspace or use an idle existing Thread, submit a prompt, watch Hermes text and tool activity stream into the conversation, and stop the active turn.

**Blocked by:** 03: Open and read a Hermes Thread.

**Status:** done

- [x] New Threads are created in the current Workspace and appear in its Thread list.
- [x] An idle Thread accepts a prompt and exposes its active state immediately.
- [x] Streamed assistant text and compact tool activity update the selected Thread without polling or page reloads.
- [x] Completed tool details are available on demand without overwhelming ordinary conversation reading.
- [x] Stop interrupts the active Hermes turn and leaves the resulting Thread state understandable.
- [x] Fake-gateway tests cover creation, submission, streaming, tool events, completion, errors, and interruption without a real model.

**Notes:** Streaming uses SSE (`GET /api/thread/stream`) forwarding gateway turn events; prompt/stop/create are POST control routes (see ADR — SSE chosen over a host↔browser WebSocket). Real gateway mapping (`session.create`, `prompt.submit`, `session.interrupt`, and the `message.*`/`turn.*` subscription) is wired per `docs/agents/hermes-api.md`; the live subscription and tool-activity events are best-effort/provisional until verified against a running Hermes turn — the fake-gateway tests drive all stream variants meanwhile.
