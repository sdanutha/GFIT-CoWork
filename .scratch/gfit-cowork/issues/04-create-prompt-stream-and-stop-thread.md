# 04: Create, prompt, stream, and stop a Thread

**What to build:** A developer can create a Thread in the current Workspace or use an idle existing Thread, submit a prompt, watch Hermes text and tool activity stream into the conversation, and stop the active turn.

**Blocked by:** 03: Open and read a Hermes Thread.

**Status:** ready-for-agent

- [ ] New Threads are created in the current Workspace and appear in its Thread list.
- [ ] An idle Thread accepts a prompt and exposes its active state immediately.
- [ ] Streamed assistant text and compact tool activity update the selected Thread without polling or page reloads.
- [ ] Completed tool details are available on demand without overwhelming ordinary conversation reading.
- [ ] Stop interrupts the active Hermes turn and leaves the resulting Thread state understandable.
- [ ] Fake-gateway tests cover creation, submission, streaming, tool events, completion, errors, and interruption without a real model.
