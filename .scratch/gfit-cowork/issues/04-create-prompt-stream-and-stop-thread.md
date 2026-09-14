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

**Notes:** Streaming uses SSE (`GET /api/thread/stream`) forwarding gateway turn events; prompt/stop/create are POST control routes (SSE chosen over a host↔browser WebSocket). **Verified live** against Hermes 0.21.2: the host holds one persistent gateway WebSocket (a session is bound to the socket that created it, so create→prompt→stream→stop share it); streaming events arrive wrapped as `method:"event"` (`params.type`, `params.payload.text`) and `session.info.running` is the turn boundary (Hermes emits no `turn.*`). A created Thread's prompt streamed back and the composer re-enabled on turn end. **Tool activity is also verified live**: `tool.start`/`tool.complete` map to tool-start/tool-end (command from `context`/`args.command`, output from `result.output`), rendering a `terminal: <command>` row with the output behind a details disclosure.
