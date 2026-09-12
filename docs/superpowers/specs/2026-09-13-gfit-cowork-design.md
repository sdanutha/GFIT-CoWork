# GFIT CoWork Design

## Purpose

GFIT CoWork is an English-first local web application for one developer to
work with Hermes Agent inside one selected Workspace at a time. Its purpose is
to make the daily thread workflow—selecting a Workspace, finding a Thread,
prompting Hermes, following tool activity, and deciding Approval requests—more
focused than a general Hermes surface.

## Product Scope

The first release is local-only. It has no accounts, remote access, shared
machines, native wrapper, credential management, file browser, voice,
automation, or Claude compatibility. It selects an existing absolute
Workspace path and shows only Threads belonging to that Workspace.

The core user journey is: open GFIT CoWork; resolve Hermes readiness; choose a
Workspace; choose or create a Thread; read Hermes history; submit a prompt;
watch streamed text and compact tool activity; decide any Approval request;
and stop the turn when required.

## Architecture

The browser communicates only with a local CoWork host. The host starts or
attaches to the local Hermes gateway, validates Workspace paths, and exposes a
small UI-oriented interface. It never independently operates on Workspace
files or shell commands.

`HermesWorkspaceGateway` is the one external seam. It owns translation between
the Hermes protocol and the product operations: readiness, Workspace-scoped
Thread discovery, Thread creation and history, prompt lifecycle, streamed text
and tool activity, interruption, and Approval resolution. The UI does not know
the Hermes protocol; the host does not retain a second transcript model.

Hermes remains the authority for Thread identity, history, profiles, provider
and model selection, credentials, tool activity, approvals, and execution.
GFIT CoWork persists only navigation and presentation preferences: Recent
Workspaces, current Workspace, selected Thread, unsent drafts, and appearance.

## Interaction Model

The Workspace selector accepts an absolute local folder and remembers recent
choices. The Thread list is scoped to that Workspace and presents title,
recency, and state. Thread history is read from Hermes and rendered in message
order.

An idle Thread accepts a prompt and shows live assistant text and compact tool
activity. A Live Thread owned by another Hermes surface remains observable but
the composer is disabled until its active turn ends. Approval requests name the
requested action and require explicit Allow or Deny choices. Health, gateway,
Workspace, Thread, and approval failures present safe recovery guidance without
secrets or raw implementation detail.

## Interface and Appearance

UI controls are English-first; prompts and Hermes content remain
language-neutral. The responsive interface follows the system appearance by
default and offers a persistent dark-mode choice. The current Workspace and
Live Thread state remain visually unambiguous.

## Testing

Tests target the public CoWork host to `HermesWorkspaceGateway` seam and
user-observable browser behavior. A fake gateway covers host and UI cases
without a real model, personal Hermes session, credential, or Workspace
mutation. Contract tests check that the production gateway translation matches
the fake at the same product interface.

Primary coverage includes readiness, Workspace validation and scoping, Recent
Workspaces, Thread history, new Thread creation, prompt streaming, tool events,
interruption, Live Thread prompt blocking, approval decisions, preferences, and
safe error presentation. Tests must prove that CoWork preference storage never
contains transcript content, tool output, credentials, or approval secrets.

## Delivery Plan

Work proceeds through seven blocker-aware vertical slices: health/startup;
Workspace browsing; Thread reading; prompt/stream/stop; approvals; Live Thread
ownership; and product hardening. The approved local tickets under
`.scratch/gfit-cowork/issues/` are the execution source for that sequence.
