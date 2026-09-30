# One User per Profile in one shared Hermes Agent

Each Team has its own Deployment (1 Hermes Agent + 1 GFIT-CoWork + 1 API key), which serves 30–50 Users. Each User logs in with their own Profile name as their username and is locked to that Profile only (building on the existing `bound_profile` mechanism). We chose this over a separate container per User because it is much easier to run and it uses the Profile structure Hermes already has.

## Consequences

- **This is not a security boundary.** Every Profile runs as the same OS user, so a User can tell the agent to read another Profile's files, including another Profile's `.env`. We **deliberately accept** this, because Users in the same Team trust one another and every Profile already uses the same API key. If people outside the Team are ever to use a Deployment, this decision must be revisited.
- Server-level features (terminal, git push/pull, extensions, shutdown, logs, YOLO mode, choosing a Workspace outside the Profile) are open to the Admin only, because they make it easy to break out of a Profile.
- It has not yet been tested whether one GFIT-CoWork process (`ThreadingHTTPServer`) can serve 30–50 Users at once. Start with 5–10 Users and measure before growing.
