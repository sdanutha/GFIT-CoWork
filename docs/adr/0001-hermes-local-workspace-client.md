# Hermes local workspace client

GFIT CoWork is an English-first, local-only web application for one selected
Workspace at a time. A TypeScript CoWork host starts or attaches to the local
Hermes gateway and presents a purpose-built interface to the React UI; Hermes
remains the sole authority for Thread identity, history, tools, profiles,
credentials, and approvals. The host does not operate on Workspace files or
shell commands itself, which avoids recreating a second agent runtime or a
second transcript store.

## Considered options

- Direct browser-to-Hermes integration: rejected because protocol, lifecycle,
  and local security concerns would spread into the UI.
- A host that also manages workspace files and commands: rejected because
  Hermes is the workspace actor and should remain the single authority.
- Remote or shared-machine access: rejected for v1 because it would require a
  distinct authentication and authorization model.
