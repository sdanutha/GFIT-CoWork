# Login with the company AD over LDAP, using the Profile name as the username

GFIT-CoWork does not store Users' passwords itself. It checks the username and password against the company's Active Directory over LDAP instead. A login succeeds only when AD confirms the password is correct **and** a Profile with a name matching that username already exists, because the Admin creates Profiles in advance. AD answers "who is this person", and the existence of a Profile answers "may this person use this Deployment". We chose this over storing passwords ourselves or using SSO/OIDC because the company already has AD, Users keep their existing password, and when IT disables the AD account of someone who has left, that person is locked out at once without the Admin doing anything.

## Consequences

- None of the Upstream login methods (one password for the whole system, passkeys, OIDC, trusted header) are used any more.
- AD must be reached over LDAPS or StartTLS only. Passwords must never be sent over unencrypted LDAP.
- AD usernames at GFIT are numeric employee IDs (e.g. `521740`), which are valid Profile names under Hermes's rules (`a-z 0-9 _ -`), so no name-mapping rule is needed. Before comparing names, strip a leading `DOMAIN\` and a trailing `@domain`, then lowercase.
- Profile names are numbers, so the web app shows the real name next to the ID, e.g. "สมชาย ใจดี (521740)". The real name comes from `displayName` in AD at login. If the person has never logged in, the name the Admin typed when creating the Profile is used; if there is none, only the ID is shown.
- Admins do not come from an AD group. They are named by AD user in each Deployment's config (several are allowed, e.g. `ADMIN_USERS=521740,671278`). An Admin logs in straight to the `default` Profile and has no personal Profile.
- AD does not know when someone moves Team, so a Profile must be able to be disabled without deleting its data. The Admin can then stop that person logging in while keeping their work to hand over.
- The AD server address and LDAPS settings are not known yet. They must be configurable; during development, use a mock LDAP.
