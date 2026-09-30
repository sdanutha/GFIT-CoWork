# GFIT-CoWork -- Changelog

GFIT-CoWork is a hard fork of Hermes WebUI (Upstream, ADR 0001). This file
records GFIT-CoWork's own changes. Upstream's release history up to the fork
is kept in git: `git show 00f9dc0f:CHANGELOG.md`.

The owner writes this file. Ordinary pull requests do not edit it; they put
release-note wording in the PR body instead.

## [Unreleased]

### Added

- **Login with a company AD employee ID.** Every person logs in through the
  company AD (the Directory), and each User works only in their own Profile.
- **The Admin role.** Admins named in the Deployment's config land in the
  `default` Profile, manage Profiles through the Profile roster, and alone use
  server-level features.
- **The `deploy/` kit.** One Deployment per Team, built from this repository's
  Dockerfile, behind the Caddy reverse proxy (ADR 0005).

### Removed

- Upstream's other ways to log in, the in-app update check, the extension
  Gallery, `/pet`, and the release workflow that published an image.
