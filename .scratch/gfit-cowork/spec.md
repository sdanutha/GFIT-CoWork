# GFIT CoWork v1

Status: ready-for-agent

## Problem Statement

Developers who use Hermes Agent locally need a focused workspace surface for
one project folder at a time. Existing Hermes surfaces expose many capabilities
and session sources, while GFIT CoWork needs to make the everyday workflow of
opening a Workspace, finding its Threads, continuing a conversation, watching
tool activity, and deciding Approval requests clear and safe. The product must
not duplicate Hermes histories, manage Hermes credentials, or present Hermes
work as Claude Code behavior.

## Solution

Build GFIT CoWork as an English-first, responsive local web application. A
local CoWork host starts or attaches to a local Hermes gateway and exposes a
small UI-oriented interface to the browser. The app lets one user choose a
Workspace by absolute path, browse that Workspace's Threads, create or resume
Threads, submit prompts, watch streaming replies and tool activity, stop work,
and explicitly allow or deny Approval requests. Hermes remains the source of
truth for Thread history, identity, profiles, provider/model choice, tools, and
credentials.

## User Stories

1. As a developer, I want to open GFIT CoWork in a browser on my machine, so that I can work with Hermes without exposing my agent to other machines.
2. As a developer, I want GFIT CoWork to attach to an existing local Hermes gateway or start one when appropriate, so that I do not have to coordinate separate processes manually.
3. As a developer, I want a clear health screen when Hermes is unavailable or unconfigured, so that I know the next safe action without entering credentials into GFIT CoWork.
4. As a developer, I want to enter an absolute Workspace path, so that I can select the project folder in which I am working.
5. As a developer, I want invalid or inaccessible Workspace paths to be rejected with an actionable explanation, so that I do not accidentally work in the wrong folder.
6. As a developer, I want recently opened Workspaces listed for reuse, so that I can switch back to familiar projects quickly.
7. As a developer, I want the current Workspace to be visually unambiguous, so that I always know which project my next prompt concerns.
8. As a developer, I want to see only Threads belonging to the current Workspace, so that unrelated local and messaging sessions do not distract me.
9. As a developer, I want Thread titles, statuses, and recency to appear in the Workspace list, so that I can identify the conversation to continue.
10. As a developer, I want to create a new Thread in the current Workspace, so that a fresh request begins with the correct Hermes workspace context.
11. As a developer, I want to open an existing Thread, so that I can read and continue its Hermes history.
12. As a developer, I want Hermes-generated Thread history displayed faithfully, so that the screen represents the durable conversation rather than a local copy.
13. As a developer, I want to submit a prompt to an idle Thread, so that Hermes can act in that Workspace.
14. As a developer, I want streamed assistant text to appear while Hermes is working, so that I can follow progress without refreshing.
15. As a developer, I want tool activity to be visible in a compact, readable form, so that I can understand what Hermes is doing without losing the conversation flow.
16. As a developer, I want completed tool details to be expandable, so that I can inspect work when needed without overwhelming ordinary reading.
17. As a developer, I want a visible running state and a Stop action, so that I can interrupt a mistaken or no-longer-needed turn.
18. As a developer, I want an Approval request to explain the action being requested and offer explicit Allow and Deny choices, so that potentially dangerous work remains under my control.
19. As a developer, I want a denied or expired Approval request recorded clearly in the Thread, so that I understand why Hermes did not continue.
20. As a developer, I want a Thread that is live in another Hermes surface to remain observable, so that I can follow ongoing work from GFIT CoWork.
21. As a developer, I want the composer disabled while that Live Thread has an active turn elsewhere, so that GFIT CoWork does not create ambiguous concurrent prompts.
22. As a developer, I want errors from Hermes or the CoWork host presented without exposing credentials or raw implementation detail, so that I can recover safely.
23. As a developer, I want provider and model behavior to follow my Hermes profile/config, so that GFIT CoWork does not silently override my agent setup.
24. As a developer, I want drafts and recent Workspace choices to survive a browser refresh, so that short interruptions do not lose my navigation or unfinished writing.
25. As a developer, I want GFIT CoWork to identify itself as a Hermes workspace product, so that its name and behavior never imply Claude Code or Anthropic ownership.
26. As a developer, I want a responsive light interface that follows the system theme with dark mode available, so that long conversations remain comfortable to read.
27. As a developer, I want the UI to use English labels while accepting prompts in any language, so that product controls remain consistent without constraining my work.

## Implementation Decisions

- GFIT CoWork is a Hermes-only product. It has no Claude runtime, Claude session migration, or multi-provider adapter.
- The product is a local-only web application. The CoWork host accepts browser connections from the same machine and does not implement remote access, accounts, or shared-machine authorization.
- The browser communicates only with the CoWork host. It does not speak the Hermes gateway protocol directly.
- `HermesWorkspaceGateway` is the sole integration seam. It presents UI-oriented operations for gateway availability, Workspace-scoped Thread discovery, Thread creation and opening, history retrieval, prompt submission, streamed text and tool events, interruption, and Approval resolution.
- The CoWork host starts or attaches to a local Hermes gateway, translates gateway events through its UI interface, and owns connection lifecycle and error classification.
- Hermes is the source of truth for Thread identity, transcript history, tool activity, approval state, profiles, providers, models, credentials, and agent execution. GFIT CoWork must not persist a competing transcript store.
- GFIT CoWork persists only non-conversation UI preferences such as Recent Workspaces, the current Workspace, appearance choice, and unsent drafts.
- A Workspace is an existing absolute local folder. The host validates it before making it current and uses it to scope Thread discovery and new work.
- Workspace Thread lists exclude unrelated Hermes sessions, including sessions from messaging platforms or other local Workspaces.
- A Live Thread is readable in GFIT CoWork but rejects new prompt submission until its active turn completes. The user can continue to see stream and Approval state.
- Approval is the default policy. The UI must offer explicit Allow and Deny actions; it does not enable full access by default.
- The host never runs shell commands or reads or writes Workspace files as an independent actor. Hermes alone performs agent actions.
- The UI receives provider/model information from Hermes for display when available, but does not offer a separate provider/model configuration surface in v1.
- A health/setup state provides non-secret remediation guidance when Hermes or its profile is unavailable. It does not collect or store provider credentials.
- The interface is English-first, responsive, follows the system theme by default, and supports a user-selected dark mode.

## Testing Decisions

- Tests validate user-observable behavior and the public interface between the CoWork host and `HermesWorkspaceGateway`; they must not assert protocol parsing internals or component implementation details.
- A fake `HermesWorkspaceGateway` drives host tests for healthy startup, unavailable gateway, Workspace validation, scoped Thread listing, new Thread creation, history loading, streamed messages, tool events, interruption, Live Thread prompt blocking, and Approval allow/deny outcomes.
- Browser-facing tests cover the Workspace chooser, Recent Workspace behavior, Thread selection, composer enablement, streaming display, tool disclosure, health/setup state, error presentation, approval controls, responsive navigation, and theme persistence.
- Contract tests verify that the production `HermesWorkspaceGateway` translates supported Hermes gateway operations and events into the same behavior as the fake.
- Tests use a temporary Hermes configuration and fixture Workspace where integration coverage is needed. They must not invoke a real model, mutate a developer Workspace, or depend on personal Hermes sessions or credentials.
- Tests verify that no messages, tool outputs, credentials, or approval secrets are written to CoWork preference storage or exposed in ordinary error messages.

## Out of Scope

- Remote access, multi-user accounts, shared-machine collaboration, and public deployment.
- Hermes provider/model/profile configuration, credential entry, and toolset management.
- A native desktop wrapper, native folder picker, voice, file browser, preview panes, automation, cron jobs, and dashboard settings.
- Multi-agent trees, session branching, steering, session editing, and session deletion.
- Claude Code compatibility, Claude session import, transcript migration, or any multi-provider mode.
- Direct Workspace filesystem, terminal, or shell operations by GFIT CoWork.

## Further Notes

- The project follows `docs/adr/0001-hermes-local-workspace-client.md` and the vocabulary in `CONTEXT.md`.
- The feature is tracked locally under `.scratch/gfit-cowork/`; implementation work should be split into blocker-aware tickets before coding begins.
- Hermes gateway behavior and supported event contracts should be verified against the installed Hermes version as part of the gateway adapter ticket.
