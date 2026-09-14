## Agent workflow

Use these sources in order when resuming or transferring a task:

1. Read `AGENTS.md` for repository-wide instructions.
2. Read `CONTEXT.md` for durable product language and boundaries.
3. Read `.agents/handoffs/current.md` for the current continuation state.

Instructions have this authority order, from highest to lowest:

1. The latest user instruction.
2. Safety rules.
3. Approved specifications and plans.
4. `AGENTS.md`.
5. The current handoff.

Follow `.agents/workflows/development.md` for the portable development loop.
Before transferring a task or taking a meaningful pause, refresh the current
handoff using `.agents/handoffs/README.md`.

## Agent skills

### Issue tracker

Issues and specs use local Markdown under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five canonical labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.
