# 06: Respect Live Thread ownership

**What to build:** A developer can open a Live Thread that Hermes is currently running through another surface, observe its progress in GFIT CoWork, and is protected from submitting a concurrent prompt.

**Blocked by:** 04: Create, prompt, stream, and stop a Thread.

**Status:** done

- [x] A Live Thread is visibly distinct from an idle Thread.
- [x] GFIT CoWork shows available streamed text, tool activity, and Approval state for the Live Thread.
- [x] The composer remains disabled for an externally active turn and explains when it will become available.
- [x] When the external turn completes, the Thread becomes promptable without stale live state.
- [x] Tests cover observation, blocked submission, lifecycle transitions, and gateway errors.

**Notes:** ThreadView tracks a turn as `idle`/`own`/`external`: a Thread listed live (`session.active_list`) or a `turn-start` this view did not submit is `external`, which disables the composer and explains why, shows Stop only for our own turn, and still renders streamed text, tool activity, and approvals. `turn-end`/`turn-error` return it to `idle` (promptable, no stale live state).
