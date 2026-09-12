# 05: Handle Hermes Approval requests

**What to build:** A developer receives clear Approval requests while a Hermes Thread is active and can explicitly Allow or Deny the requested action from GFIT CoWork.

**Blocked by:** 04: Create, prompt, stream, and stop a Thread.

**Status:** ready-for-agent

- [ ] An Approval request visibly identifies the requested action and its current decision state.
- [ ] Allow and Deny are explicit user actions; GFIT CoWork does not default to full access.
- [ ] Approved, denied, expired, and failed Approval outcomes remain understandable in the Thread.
- [ ] Duplicate, stale, or unavailable Approval actions fail safely without changing unrelated Thread state.
- [ ] Tests cover the full request-to-decision lifecycle through the public host interface and fake gateway.
