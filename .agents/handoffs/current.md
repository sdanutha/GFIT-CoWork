# Current Agent Handoff

## Goal

Ship GFIT CoWork v1 (issues 01–07) against a real local Hermes gateway and
live-verify every feature. This is complete: all 7 issues are implemented and
each is verified live against Hermes 0.21.2.

## State

All issues under `.scratch/gfit-cowork/issues/` are done and live-verified:

- 01 health, 02 workspace Thread list, 03 open/read a Thread (`session.resume`),
  04 create→prompt→stream→stop **plus tool activity** (a `terminal: <cmd>` row
  with output behind a details disclosure) — all verified in the browser.
- 05 approvals — verified live: `chmod 777 …` hits a guard rule
  (`world/other-writable`) that gates in any approval mode, so it fired a real
  `approval.request`; GFIT showed the Allow/Deny card with the exact command,
  Deny resolved to "Denied", Hermes blocked the command, composer re-enabled.
- 06 Live Thread ownership — verified end-to-end: `session.resume` returns
  `running:true` during an external turn and the host `/api/thread` surfaces it
  (`running`), so ThreadView opens the Thread as external (composer disabled,
  "another surface"); unit-tested for the UI.

Architecture unchanged: browser → CoWork host (`src/host`, 4318) → one
persistent authenticated WebSocket to Hermes (9119). See
`docs/agents/hermes-api.md` for the verified event/RPC facts.

## Git

Branch `main`, in sync with `origin/main` at `07d5e2a`. Working tree clean
(only `.claude/launch.json` untracked — dev-only, intentionally uncommitted).

## Changed Files

None uncommitted.

## Verification

`npm test` (82/82), `npm run build`, `npm run check:agents` all pass. Run with
`PORT=4318 npm run dev` + `npm run dev:client`; kill stale hosts if 4318 is in
use (EADDRINUSE silently serves old code). Live approval tests need a guarded
command (`hermes approvals test "<cmd>"`); `chmod 777 /tmp/x` works and is safe
to Deny.

## Next Action

v1 is complete and verified, including the earlier polish: the Thread-list badge
now shows **Live** for a running Thread (`session.active_list` `status==='working'`,
matched by `session_key`) — verified live in the browser. No outstanding work.
Optional future: the badge is a list-load snapshot (not a live subscription);
map tool event types beyond `terminal` if other tools appear.

## Risks or Decisions Needed

- None outstanding. Approval mode was temporarily set to `manual` during 05
  testing and **restored to `smart`** (confirmed).
- Several throwaway probe sessions remain in the user's Hermes store (echo/uname/
  pong/essay tests); harmless, deletable by the user via Hermes.
