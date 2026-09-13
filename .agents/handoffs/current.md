# Current Agent Handoff

## Goal

Ship GFIT CoWork v1 (issues 01–07) working against a real local Hermes gateway,
and live-verify each feature. Implementation is complete; the remaining work is
live-verifying the approval flow (05) and Live Thread ownership (06), which could
not be triggered in the last session.

## State

All 7 issues are implemented and marked done under `.scratch/gfit-cowork/issues/`.

- Architecture: browser (React/Vite) → CoWork host (`src/host`, port 4318) → one
  persistent authenticated WebSocket to the Hermes gateway (`127.0.0.1:9119`).
  `HermesWorkspaceGateway` is the seam; `createGatewayConnection` holds the shared
  socket (request/response by id + `method:"event"` notifications fanned out).
- Verified LIVE in the browser against Hermes 0.21.2: health (01), workspace
  Thread list (02), open/read a Thread via `session.resume` (03),
  create→prompt→stream→turn-end (04), and tool activity — a `terminal: <cmd>` row
  with output behind a details disclosure (04).
- Key facts confirmed by probing (see `docs/agents/hermes-api.md`):
  `session.list` returns a stored key; `session.resume` binds it to a runtime
  `session_id` used for all later calls; streaming events are wrapped as
  `method:"event"` with text in `payload.text`; the turn boundary is
  `session.info.running`; tool command is in `context`/`args.command`, output in
  `result.output`.
- Approvals (05) and Live ownership (06): unit-tested and mapped from verified
  event shapes, but NOT live-triggered — the local Hermes runs
  `approval_mode: "smart"`, which auto-allowed every safe command tried (so no
  approval prompt fired), and no concurrent external turn was orchestrated.

## Git

Branch `main`, in sync with `origin/main` at `b469cd0`. Working tree clean
(only `.claude/launch.json` is untracked — a dev-only browser-preview config,
intentionally not committed).

## Changed Files

None uncommitted. Recent commits cover the whole build, the persistent-connection
transport rework, the `session.resume` fix, and tool/approval event mapping.

## Verification

`npm test` (81/81), `npm run build`, and `npm run check:agents` all pass.
Run the app with `PORT=4318 npm run dev` (host) + `npm run dev:client` (Vite),
then open the printed localhost URL. Kill stale hosts first if 4318 is in use
(EADDRINUSE silently serves old code).

## Next Action

Live-verify 05 and 06:
1. Approvals (05): set the Hermes profile to an approval mode stricter than
   `smart` (so a benign command is gated), send a prompt that triggers a tool,
   and confirm the Allow/Deny UI + `approval.respond`. Adjust the mapping if the
   live `approval.request` payload field names differ from the source.
2. Live ownership (06): run a turn on the same session from another Hermes
   surface (desktop/TUI) and confirm GFIT shows it as external (composer
   disabled) via `session.info.running`.

## Risks or Decisions Needed

- 05 live needs a change to the user's Hermes approval config — a user decision;
  do not modify Hermes config without asking.
- 06 live needs orchestrating a concurrent turn from another surface.
- Several throwaway probe sessions remain in the user's Hermes store (e.g.
  "Run uname -a…", "Respond with pong"); harmless, deletable by the user via
  Hermes (the agent must not hard-delete).
