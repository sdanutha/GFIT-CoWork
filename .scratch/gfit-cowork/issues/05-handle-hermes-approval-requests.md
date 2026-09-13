# 05: Handle Hermes Approval requests

**What to build:** A developer receives clear Approval requests while a Hermes Thread is active and can explicitly Allow or Deny the requested action from GFIT CoWork.

**Blocked by:** 04: Create, prompt, stream, and stop a Thread.

**Status:** done

- [x] An Approval request visibly identifies the requested action and its current decision state.
- [x] Allow and Deny are explicit user actions; GFIT CoWork does not default to full access.
- [x] Approved, denied, expired, and failed Approval outcomes remain understandable in the Thread.
- [x] Duplicate, stale, or unavailable Approval actions fail safely without changing unrelated Thread state.
- [x] Tests cover the full request-to-decision lifecycle through the public host interface and fake gateway.

**Notes:** Approvals ride the existing SSE stream (`approval.request`/`approval.resolved` → `approval-request`/`approval-resolved` events) with a `POST /api/thread/approval` control route (`approval.respond`, `docs/agents/hermes-api.md`). The host defaults any non-`allow` choice to `deny` (never full access); duplicate requests are de-duplicated by request id and a failed response leaves the approval pending without touching other state. **Verified live end-to-end.** A guard rule (`world/other-writable permissions`) gates `chmod 777 …` regardless of approval mode, so prompting `chmod 777 /tmp/gfit-approval-test` fired a real `approval.request`. In the browser GFIT showed the "APPROVAL REQUESTED" card with the exact command and explicit **Deny**/**Allow** buttons (no default), the turn blocked awaiting the decision, clicking **Deny** resolved the card to "Denied", Hermes reported "The command was blocked because approval was denied. No permissions were changed.", and the composer became promptable again. `approval.request` payload carries `request_id` + `command` (redacted by Hermes); a timeout emits `approval.expire {request_id}` → `expired` (mapped, not exercised live). Note: `approval_mode: "smart"` auto-allows most commands via a command allowlist, so approvals are guard-rule-driven; the test temporarily set `manual` then restored `smart` (the guard fires in either mode).
