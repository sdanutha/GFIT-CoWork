# Portable Agent Development Workflow

1. Orient using `AGENTS.md`, `CONTEXT.md`, and, when continuing a task,
   `.agents/handoffs/current.md`. Orientation is complete when the goal and
   applicable repository rules are clear.
2. Confirm the repository state, including the active branch, current commit,
   and existing changes. Confirmation is complete when unrelated work is
   identified and preserved.
3. Make one small, scoped change that advances the approved goal. The change is
   complete when it stays within the current task boundary.
4. Run the verification relevant to the change. Verification is complete when
   its commands and results are known.
5. Commit a coherent change when the task permits commits. The commit is
   complete when its message describes the delivered behavior.
6. Checkpoint after every meaningful state change by refreshing
   `.agents/handoffs/current.md` with the latest state, verification, and one
   exact next action. Refresh it again before a transfer or meaningful pause, or
   clear it when no continuation state remains. The workflow is complete when
   the next agent has one exact next action or the file says no handoff is active.
