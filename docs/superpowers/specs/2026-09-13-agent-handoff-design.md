# Cross-Agent Handoff Design

## Purpose

GFIT CoWork must remain easy to continue when an agent reaches a context or
usage limit. A developer can move the same task between Codex, Claude,
Copilot, Hermes, or a future agent without reconstructing its status from
conversation history.

The repository, rather than an agent platform, is the source of continuity.
Git records accepted changes; project documents record durable product facts;
one current handoff records the next actionable state.

## Architecture

`AGENTS.md` is the agent-neutral entry point. Every agent reads it before
working. It points to `CONTEXT.md` for durable domain language and to the
handoff protocol only when a task is being resumed, paused, or transferred.

`.agents/handoffs/current.md` is the single current handoff. It is a compact,
replaceable work record, not an activity log. It identifies the goal, current
state, branch and commit, changed files, verified commands and results, the
one next action, and risks or required human decisions. It contains no secrets,
credentials, private prompts, transcripts, or raw tool output.

`.agents/handoffs/README.md` defines the mandatory template and lifecycle:
read before resuming; update after a meaningful state change; replace when the
next task supersedes it; remove only when no continuation state remains.

`.agents/workflows/development.md` gives one portable workflow for all agents:
orient, inspect the handoff, make a small scoped change, verify it, commit it,
and refresh the handoff. It defers product-specific decisions to existing
project documents rather than duplicating them.

Tool-specific adapter files are intentionally short pointers:

- `CLAUDE.md` for Claude;
- `.github/copilot-instructions.md` for GitHub Copilot;
- `docs/agents/hermes.md` for Hermes; and
- `AGENTS.md` directly for Codex and other tools that recognize it.

Each adapter directs its agent to the same source documents and must not create
a divergent workflow.

## Handoff Flow

1. A receiving agent reads `AGENTS.md`, `CONTEXT.md`, and the current handoff
   when it exists.
2. It checks the referenced branch, commit, status, and verification commands
   before acting.
3. It makes only work that matches the stated next action or gets a new user
   decision when the handoff says one is needed.
4. Before pausing or transferring, it records the exact next action and fresh
   verification evidence in `current.md`.
5. A completed task clears or replaces the handoff so a stale instruction
   cannot be resumed accidentally.

## Safety and Conflict Rules

The handoff never authorizes destructive Git actions, credential use, network
publishing, or workspace automation by itself. Those actions still require the
user's request and the agent's own safety rules.

The current handoff is a coordination aid, not authority over Git or product
specifications. If it conflicts with `AGENTS.md`, `CONTEXT.md`, an approved
specification, or the user's latest instruction, the higher-authority source
wins and the receiving agent updates the handoff to explain the correction.

Only one active task should be represented in `current.md`. Parallel work uses
separate named handoff files only when a coordinating agent explicitly creates
them and names the owner and merge boundary.

## Testing

This is documentation and workflow infrastructure. Verification checks that
all adapter pointers resolve, the handoff template has every mandatory field,
and project development commands remain documented in one authoritative place
(`package.json` and the README).

## Scope

This design adds portable project context and handoff instructions. It does
not install agent CLIs, change Hermes runtime behavior, create external
accounts, automate task routing, or copy conversation transcripts into Git.
