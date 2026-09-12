# 06: Respect Live Thread ownership

**What to build:** A developer can open a Live Thread that Hermes is currently running through another surface, observe its progress in GFIT CoWork, and is protected from submitting a concurrent prompt.

**Blocked by:** 04: Create, prompt, stream, and stop a Thread.

**Status:** ready-for-agent

- [ ] A Live Thread is visibly distinct from an idle Thread.
- [ ] GFIT CoWork shows available streamed text, tool activity, and Approval state for the Live Thread.
- [ ] The composer remains disabled for an externally active turn and explains when it will become available.
- [ ] When the external turn completes, the Thread becomes promptable without stale live state.
- [ ] Tests cover observation, blocked submission, lifecycle transitions, and gateway errors.
