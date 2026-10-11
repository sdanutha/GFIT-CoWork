# Inbound A2A from other Teams only through the Reception, and answer-only

Deployments talk to each other over A2A using the A2A plugin that ships with Hermes Agent; no code in GFIT-CoWork is needed. Every Profile can call out to other Teams, but only the **Reception** Profile of each Deployment accepts calls from other Teams. An incoming A2A message is injected into the session that Profile is using, which sees all of its owner's memory and files. If other Teams could call a User's Profile directly, that would open a way to pull out private data. So the Reception is limited to answering questions only (through `A2A_ADVERTISED_TOOLSETS`), with no tools that edit files, run commands or send data out.

## Consequences

- This is done in phase two, after multi-User login works.
- The Operator sets up peers and tokens in the Hermes config file themselves; there is no management page in GFIT-CoWork yet.
