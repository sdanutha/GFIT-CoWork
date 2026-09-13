# Agent Handoff Protocol

`.agents/handoffs/current.md` is the single continuation record shared by all
agents. When active, it describes exactly one task. Read it before resuming a
task. Update it after every meaningful state change so an unexpected stop still
leaves recent continuation state. Refresh it again before transferring the task
or taking a meaningful pause. Replace or clear it when its continuation state is
no longer current.

Keep the record concise and actionable. Include no credentials, secrets,
private prompts, conversation transcripts, or raw tool output.

Use these exact headings:

# Current Agent Handoff

## Goal

## State

## Git

## Changed Files

## Verification

## Next Action

## Risks or Decisions Needed
