# No Admin in the web app; the Operator works on the server

GFIT-CoWork has one role, the User, bound to their own Profile. There is no Admin role, no `HERMES_WEBUI_ADMIN_USERS` list, and no web login to the `default` Profile. Everything the Admin used to do from the web app is done on the Deployment's server by the **Operator**, with Hermes Agent's own tools and GFIT-CoWork's command line. We chose this over keeping a slimmed-down Admin because every Admin-only route was a way out of a Profile (terminal, git, arbitrary files, shutdown) or a Deployment-wide switch, and the people who need those already have shell access to the server. Removing the role removes the code, the all-Profiles views and the second branch in every Admission and reach check.

Supersedes in part ADR 0002 (server-level features open to the Admin) and ADR 0004 (the Admin list, the Admin's login to `default`, and Profile management from the web app).

## Considered Options

- **Keep an Admin with fewer powers** (Profile management and read-only views only). Rejected: the role still needs its own Admission branch, reach rules and tests, for work the Operator can do from the shell.
- **Manage Profiles with Hermes CLI only, no disable.** Rejected: AD does not know when someone moves Team, and ADR 0004 requires disabling a Profile without deleting its data.

## Consequences

- Admission always gives a User and their own Profile; every admitted request is bound and its Profile reach is that Profile only. Login with AD is always required; development and tests use a mock LDAP. There is no mode with login turned off.
- The Profile lifecycle (create, disable, enable, delete) is a GFIT-CoWork command-line tool on the server that runs the same steps as before: a new Profile is shut until it exists, and a Profile is disabled before it is deleted. A Profile made directly with `hermes profile create` also works, because a Profile with no roster record is active.
- Disabling still stops the Profile's work (ADR 0004). The command line changes only durable state: the roster, and the Profile's scheduled jobs, which it pauses. The running server notices the roster change and ends that Profile's logins and running turns itself, so no Operator endpoint is opened on the server.
- Server-level features (terminal, git, files outside the Profile, commands, shutdown, restart, gateway control, logs, extensions, YOLO mode), the all-Profiles views, kanban, share links, the dashboard, provider and MCP setup, and web onboarding are deleted, not hidden. Session cleanup and recovery repair become Operator tasks on the server.
- A User may choose their own Profile's default model, auxiliary models and reasoning settings, which live in that Profile's Hermes config. Providers and API keys stay with the Operator.
- Web app settings (theme, voice, composer buttons and so on) belong to each Profile and its User may change them; they are no longer one file shared by the whole Deployment.
- A Deployment that still sets `HERMES_WEBUI_ADMIN_USERS` starts as usual and logs a warning that the setting is ignored. It grants nothing: a former Admin with no Profile of their own is refused at login, and an old Admin cookie fails Admission on its next request.
- The route table has only User routes and public ones (the login page and what it needs); a route with no row is refused. A test keeps it that way.
- There is no mode with login turned off: a server with no Directory refuses to start, on any address. Local development and the test suite use the in-memory Directory; the shared test server logs in one test User bound to their own Profile, so the tests exercise the production Admission (TESTING.md, "How the automated tests log in").
- A request's Profile is its Admission's and nothing else: the Profile cookie, Profile switching and every all-Profiles view (the session list, search, projects and cron "other Profiles" toggles, the all-Profiles CLI import) are gone.
- Handing over a departed User's work is done on the server, not by reading their Profile in the web app.
