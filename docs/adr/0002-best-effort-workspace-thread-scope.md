# Best-effort Workspace Thread scope

GFIT CoWork scopes its Thread list to the selected Workspace on a **best-effort**
basis rather than guaranteeing an exact per-folder list. The Workspace is the
working directory for Threads created or resumed in GFIT CoWork and the lens for
the list; the list itself is the recent Hermes sessions the gateway reports,
narrowed to the Workspace where Hermes exposes a session's folder. Precise
per-folder listing is a documented limitation.

## Context

`CONTEXT.md` originally promised a Thread list containing only sessions belonging
to the current folder. Verifying the Hermes gateway (v0.21.2) against its source
showed that surface cannot deliver it (see `docs/agents/hermes-api.md`):

- `session.list` returns rows without a cwd and accepts no folder filter.
- Each session's cwd lives in Hermes's SQLite store; only the CLI reads it there
  directly, not through the gateway.
- `projects.*` manage a folder registry, not folder-scoped session listing.

The gateway is the surface `CONTEXT.md` and ADR 0001 commit to (a pure gateway
client, no credentials, Hermes as sole authority). The promised scoping needs a
capability that surface does not offer.

## Considered options

- **Best-effort scope over the gateway (chosen):** keep the gateway-only,
  no-credential boundary; surface recent Hermes sessions and narrow to the
  Workspace where cwd is known (e.g. Threads GFIT CoWork created or activated).
  Honest about the limitation; unblocks the remaining Thread features.
- **Read the Hermes session SQLite store read-only from the host:** delivers
  exact folder scoping, but couples GFIT CoWork to Hermes's internal schema and
  breaks the "gateway client only" boundary this project is built on. Held as an
  escape hatch if exact scoping later becomes a hard requirement — it would need
  its own ADR amending the host-authority boundary.
- **Use the OpenAI-compatible REST API server:** rejected — it needs an
  `API_SERVER_KEY`, conflicting with "GFIT CoWork does not ask for or store
  Hermes credentials," and abandons the gateway surface.

## Consequences

- On first opening a Workspace, pre-existing Threads that GFIT CoWork cannot
  attribute to the folder may not appear; the list favors recent sessions and
  the ones GFIT CoWork tracks.
- The boundary from ADR 0001 (gateway client, no direct store access, no
  credentials) is preserved.
- If exact scoping is required later, revisit the read-only-store option under a
  new ADR, or adopt a future Hermes gateway method that filters sessions by cwd.
