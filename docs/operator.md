# The Operator

There is no Admin in the web app: the team's **Operator** runs the server, the
API key and the list of Profiles from the server's shell
([ADR 0006](adr/0006-no-admin-in-the-web-app.md)). This page covers what a User
can and cannot do, and the Operator's command line. For setting up a
Deployment see [setup.md](setup.md) and [deploy/README.md](../deploy/README.md).

## Users and the Operator

A **User** works only in their own Profile. Every request runs in the Profile
bound to their session, whatever Profile the client names; naming another one
is refused, and there is no Profile switching and no view of other Profiles. A
User's Workspaces live in `<Profile>/workspace`, created at first login.
Registering a Workspace outside it, or any file operation that resolves outside
it (through `..` or a symlink), is refused. A User changes their own web
settings and their own Profile's models and reasoning; settings that belong to
the whole Deployment (the assistant's name, API redaction, ...) are the
Operator's.

The **Operator** is whoever has shell access to the Deployment's server. They
set up providers and API keys, manage Profiles with the command line below, and
use Hermes Agent's own tools for anything server-level (a terminal, logs,
gateway control, restarts). The web app has none of these: the route table
(`api/route_table.py`) lists every route a User may call, and anything else is
refused (fail closed).

## Managing Profiles

The Operator manages Profiles on the server, inside the Deployment's
GFIT-CoWork container, through the `gfit` shortcut in
[deploy/README.md](../deploy/README.md#add-a-user) (`api.operator_cli` with the
web server's Python and environment):

```bash
gfit list                                   # name, status, last login, display name
gfit create 521740 --display-name "Somchai Jaidee" --clone-from default
gfit disable 521740
gfit enable 521740
gfit delete 521740 --confirm 521740        # only a disabled Profile
gfit sessions-audit                         # read-only
gfit sessions-repair                        # quiet time; restart the server after
gfit sessions-cleanup [--empty]             # quiet time; restart the server after
```

A Profile is named after the person's employee ID. `--clone-from default`
copies the `default` Profile's config, skills and, from its `.env`, only the
provider keys, so the new Profile can use the Team's model right away. The
`default` Profile's `.env` is readable by every User's agent: keep secrets
Users must not see out of it (see [setup.md](setup.md)). **Disable** keeps the
data: the command line pauses the Profile's scheduled jobs, and the running
server notices within seconds, signs the person out and stops their running
turns; later logins are told their access is suspended. **Enable** lets them
back in and resumes the jobs the disable paused. **Delete** removes a disabled
Profile and its record for good.

Display name, status and last login live in the **Profile roster**
(`gfit_roster.json` in the state directory), not in the Hermes Profile config.

## Name and Sign Out

After login the Profile chip shows who is signed in as "name (ID)", e.g.
"สมชาย ใจดี (521740)". Every login takes the name from the Directory and saves
it in the Profile roster. Until someone's first login, their chip shows the
name the Operator gave at create, or just the ID. `/api/auth/status`
sends `user`, `display_name` and `label`. Clicking the chip opens a menu with
the name and **Sign Out**, which ends that browser's session only.
