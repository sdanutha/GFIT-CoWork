# Cross-Agent Handoffs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Codex, Claude, Copilot, Hermes, and future agents one portable workflow for resuming and transferring GFIT CoWork tasks.

**Architecture:** `AGENTS.md` is the one agent-neutral entry point, while `.agents/handoffs/current.md` captures exactly one active task. Short tool adapters point back to those canonical documents. A dependency-free Node checker verifies the handoff schema and every pointer so the documentation protocol stays executable.

**Tech Stack:** Markdown, Node.js built-in modules, existing npm scripts.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-handoff-design.md`

## Global Constraints

- Keep `AGENTS.md` as the central, tool-neutral instruction entry point.
- Store only one active task in `.agents/handoffs/current.md`.
- Handoffs contain goal, state, branch/commit, changed files, verification, next action, and risks/decisions; never place credentials, secrets, private prompts, transcripts, or raw tool output in them.
- Adapter files point to canonical documents and do not define a competing workflow.
- Do not add dependencies; validation uses Node.js built-in modules.
- Preserve existing product, issue-tracker, and domain guidance in `AGENTS.md`.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `AGENTS.md` | Central orientation order, authority order, and handoff trigger. |
| `.agents/handoffs/README.md` | Defines the current-handoff lifecycle and exact required headings. |
| `.agents/handoffs/current.md` | Safe starter record for one active task. |
| `.agents/workflows/development.md` | Portable orientation → change → verify → commit → handoff workflow. |
| `CLAUDE.md` | Claude adapter pointing to central guidance. |
| `.github/copilot-instructions.md` | Copilot adapter pointing to central guidance. |
| `docs/agents/hermes.md` | Hermes adapter and local-runtime boundary. |
| `scripts/check-agent-handoff.mjs` | Validates canonical files, adapter pointers, and required handoff headings. |
| `package.json` | Exposes validator as `npm run check:agents`. |
| `tests/agent-handoff-check.test.ts` | Runs the public validation command and asserts a zero exit status. |

### Task 1: Establish the canonical handoff protocol

**Files:**
- Modify: `AGENTS.md`
- Create: `.agents/handoffs/README.md`
- Create: `.agents/handoffs/current.md`
- Create: `.agents/workflows/development.md`

**Interfaces:**
- Produces: the canonical paths and required headings consumed by every adapter and by `scripts/check-agent-handoff.mjs`.

- [ ] **Step 1: Add a protocol test fixture that expects the required headings**

Create `tests/agent-handoff-check.test.ts` with this public-command assertion:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('validates the cross-agent handoff protocol', () => {
  assert.doesNotThrow(() => {
    execFileSync('npm', ['run', 'check:agents'], { stdio: 'pipe' })
  })
})
```

- [ ] **Step 2: Run the test to verify it fails before the validator exists**

Run: `npm test -- tests/agent-handoff-check.test.ts`

Expected: FAIL because `check:agents` is not yet defined.

- [ ] **Step 3: Write the canonical documents**

Add an `Agent workflow` section to `AGENTS.md` before the existing skills section. It must direct agents to read, in order: `AGENTS.md`, `CONTEXT.md`, and `.agents/handoffs/current.md` when resuming/transferring a task. It must state this authority order: latest user instruction; safety rules; approved specs/plans; `AGENTS.md`; current handoff.

Create `.agents/handoffs/README.md` with these exact template headings:

```markdown
# Current Agent Handoff

## Goal
## State
## Git
## Changed Files
## Verification
## Next Action
## Risks or Decisions Needed
```

Document that `current.md` has one active task, is refreshed before transfer or a meaningful pause, and contains no sensitive material. Create `current.md` using the same headings with the safe starter state `No active handoff.` and next action `Start a handoff when a task needs continuation.`

Create `.agents/workflows/development.md` with the numbered workflow: orient using canonical docs; confirm repository state; make a small scoped change; run relevant verification; commit a coherent change; refresh or clear the handoff.

- [ ] **Step 4: Implement the smallest validator for canonical documents**

Create `scripts/check-agent-handoff.mjs` that uses `node:fs`, `node:path`, and `process.cwd()`. Read `.agents/handoffs/current.md`, require all eight exact headings above, and exit non-zero with `Missing handoff heading: <heading>` for the first missing heading. Also require `AGENTS.md`, `CONTEXT.md`, `.agents/handoffs/README.md`, and `.agents/workflows/development.md`; report `Missing required agent document: <path>` when absent. Print `Agent handoff protocol is valid.` when successful.

- [ ] **Step 5: Expose the validator and prove the test is green**

Add this script to `package.json`:

```json
"check:agents": "node scripts/check-agent-handoff.mjs"
```

Run: `npm test -- tests/agent-handoff-check.test.ts && npm run check:agents`

Expected: PASS, and the command prints `Agent handoff protocol is valid.`

- [ ] **Step 6: Commit the canonical protocol**

```bash
git add AGENTS.md .agents scripts/check-agent-handoff.mjs package.json tests/agent-handoff-check.test.ts
git commit -m "docs: add cross-agent handoff protocol"
```

### Task 2: Add tool adapters without duplicating policy

**Files:**
- Create: `CLAUDE.md`
- Create: `.github/copilot-instructions.md`
- Create: `docs/agents/hermes.md`
- Modify: `scripts/check-agent-handoff.mjs`
- Modify: `tests/agent-handoff-check.test.ts`

**Interfaces:**
- Consumes: canonical documents from Task 1.
- Produces: three adapter paths that all explicitly point to `AGENTS.md`, `CONTEXT.md`, and `.agents/handoffs/current.md`.

- [ ] **Step 1: Add failing assertions for adapter paths and pointers**

Extend `tests/agent-handoff-check.test.ts` to temporarily copy each adapter's content into a testable helper contract or invoke the public checker after adapters are absent. The expected failing command is:

```bash
npm run check:agents
```

Expected: FAIL with `Missing required agent document: CLAUDE.md`.

- [ ] **Step 2: Create the short adapters**

Create each adapter with a short title and this exact orientation statement adapted to its tool name:

```markdown
Read `AGENTS.md` first. Then read `CONTEXT.md`. When resuming, pausing, or transferring a task, read and update `.agents/handoffs/current.md` using `.agents/handoffs/README.md`.
```

`docs/agents/hermes.md` must additionally state that Hermes retains its runtime/tool authority and follows the repository handoff protocol for project continuity; it must not claim Hermes can read credentials or bypass the product's approval model.

- [ ] **Step 3: Extend the validator to enforce adapters**

Add `CLAUDE.md`, `.github/copilot-instructions.md`, and `docs/agents/hermes.md` to the required-document list. For each, require the literal strings `AGENTS.md`, `CONTEXT.md`, and `.agents/handoffs/current.md`; exit non-zero with `Adapter is missing canonical pointer: <path>` when one is absent.

- [ ] **Step 4: Run focused validation**

Run: `npm test -- tests/agent-handoff-check.test.ts && npm run check:agents`

Expected: PASS.

- [ ] **Step 5: Commit the adapters**

```bash
git add CLAUDE.md .github/copilot-instructions.md docs/agents/hermes.md scripts/check-agent-handoff.mjs tests/agent-handoff-check.test.ts
git commit -m "docs: add agent-specific handoff adapters"
```

### Task 3: Verify the portable workflow and document how to use it

**Files:**
- Modify: `README.md`
- Modify: `tests/agent-handoff-check.test.ts`
- Modify: `scripts/check-agent-handoff.mjs`

**Interfaces:**
- Consumes: the canonical protocol and adapters from Tasks 1–2.
- Produces: an onboarding pointer and validator coverage for all required protocol locations.

- [ ] **Step 1: Add a failing README-pointer validation case**

Extend `scripts/check-agent-handoff.mjs` so `README.md` must contain the literal `.agents/handoffs/README.md`. Add a test case or command assertion showing the checker fails before the README change:

```bash
npm run check:agents
```

Expected: FAIL with `README is missing agent handoff guidance.`

- [ ] **Step 2: Add concise README onboarding**

Add an `## Agent continuity` section to `README.md` after the setup/run instructions:

```markdown
## Agent continuity

This repository can move between Codex, Claude, Copilot, Hermes, and other agents without copying conversation history. Read [AGENTS.md](AGENTS.md), [CONTEXT.md](CONTEXT.md), and the [current handoff](.agents/handoffs/current.md) before resuming work. The [handoff protocol](.agents/handoffs/README.md) explains how to pause or transfer a task safely.
```

- [ ] **Step 3: Make the README check green and run the full suite**

Run:

```bash
npm run check:agents
npm test
npm run build
git diff --check
```

Expected: every command exits 0; `npm test` includes the handoff validator test.

- [ ] **Step 4: Commit the verified workflow**

```bash
git add README.md scripts/check-agent-handoff.mjs tests/agent-handoff-check.test.ts
git commit -m "docs: explain agent continuity"
```

## Plan Self-Review

- Spec coverage: Task 1 establishes the canonical entry point, current handoff, lifecycle, and portable workflow. Task 2 provides Codex-compatible central guidance plus Claude, Copilot, and Hermes adapters. Task 3 validates the protocol and onboards future users.
- Placeholder scan: no deferred requirements or undefined interfaces remain.
- Type consistency: every task refers to the same canonical paths and uses the exact eight required handoff headings.
