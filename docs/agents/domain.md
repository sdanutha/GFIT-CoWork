# Domain Docs

## Before exploring, read these

- `CONTEXT.md` at the repo root, or `CONTEXT-MAP.md` when it exists
- Relevant ADRs in `docs/adr/`

If they do not exist, proceed silently. `/domain-modeling` creates domain documentation lazily when terms or decisions are resolved.

## File structure

This is a single-context repository:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md` in issues, specifications, test names, and design discussions. Surface any conflict with an existing ADR explicitly rather than silently overriding it.
