# GFIT-CoWork

A multi-user web workspace, run on a shared server, for working with Hermes Agent. It is a hard fork of Hermes WebUI.

## Language

**GFIT-CoWork**:
This product: the web app that people log in to and work with the agent through.
_Avoid_: Hermes WebUI, WebUI, the UI

**Hermes Agent**:
The external autonomous agent (by Nous Research) that GFIT-CoWork drives. GFIT-CoWork does not own it; it stays named "Hermes".
_Avoid_: the backend, the bot

**User**:
A person who logs in to GFIT-CoWork with their company AD account. Each User owns exactly one Profile, which the Admin creates in advance and names after their AD username.
_Avoid_: account, member

**Admin**:
An AD user named in the Deployment's config; a Deployment may have several. The Admin logs in directly to the `default` Profile, stays there, and has no personal Profile; the Admin sees other Profiles only through the all-Profiles views and never switches into one (ADR 0004). Only the Admin can create, disable and delete Profiles, and use server-level features (terminal, extensions, logs).
_Avoid_: root, superuser, owner

**Profile**:
A Hermes Agent profile: one agent identity with its own config, memory, skills, sessions and Workspaces. A logged-in User can reach only their own Profile.
_Avoid_: bot, persona

**Profile roster**:
GFIT-CoWork's own record of each Profile: the User's display name, whether the Profile is active or disabled, and the last login. It is kept apart from the Hermes Profile config. A disabled Profile keeps its data, but its User cannot log in and it does no work: its running turns stop and its scheduled jobs pause until it is enabled again.
_Avoid_: user list, member table

**Admission**:
The decision, from an AD username the company AD has confirmed, of whether that person may use the Deployment and with which role and Profile: an Admin goes to `default`; anyone else needs their own Profile, and it must be active. It is made at login and again on every request, so a change to the Admin list or the Profile roster takes effect at once.
_Avoid_: authorization, access check

**Bound**:
Said of a User's request: it runs in that User's Profile and may name no other, because the request's Admission says so. The Admin's requests are not bound.
_Avoid_: pinned, locked

**Profile reach**:
The Profiles a request may read. A User's reach is their own Profile only; the Admin's is every Profile. A view (the session list, projects, the Profile list) can narrow it further, for example to the active Profile.
_Avoid_: scope, visibility

**Workspace**:
A folder the agent works in for a session. Every Workspace lives inside its owner's Profile.
_Avoid_: project folder, directory

**Team**:
A group of Users (about 30–50) who share one Deployment. Users in the same Team are trusted by one another.
_Avoid_: group, department, tenant

**Deployment**:
One Hermes Agent plus one GFIT-CoWork, serving exactly one Team, with its own URL, Admin and API key. Deployments share nothing with one another.
_Avoid_: instance, server, stack

**Reception**:
The one Profile in each Deployment that answers A2A requests from other Deployments. It is not owned by a User and can only answer questions, not take actions.
_Avoid_: front desk, gateway, team bot

**A2A**:
The Agent-to-Agent protocol that Deployments use to talk to one another. Any Profile can call out, but only the Reception can be called.
_Avoid_: federation, inter-team API

**Upstream**:
The original Hermes WebUI project (nesquena/hermes-webui) that GFIT-CoWork was forked from. Changes are no longer merged from it.
_Avoid_: origin, parent repo
