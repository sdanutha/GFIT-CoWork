# Workspace Git view

The Workspace Git view lets a User see the Git state of their session's Workspace. It is read-only:

- the Git badge on the Workspace (`/api/git-info`)
- repository status (`/api/git/status`)
- the branch list (`/api/git/branches`)
- file diffs (`/api/git/diff`)

Nothing in the web app changes a repository: staging, discarding, committing, fetching, pulling,
pushing and branch switching went with the server-level tools (ADR 0006). A User changes their
repository by asking the agent, which works inside their Workspace like any other tool.

Diffs for untracked files are size checked before WebUI reads file contents. Large or binary files
return metadata instead of inline diff text.

## Workspace and path scope

The browser does not send an arbitrary repository path. Git requests carry a session id and, when
needed, workspace-relative file paths. The server resolves the session workspace, checks each path
against that workspace, and then builds Git pathspecs from the checked paths.

Git commands run through `subprocess.run` with `shell=False`, with a 5 second timeout.

Before any Git subprocess starts, WebUI removes inherited `GIT_DIR`, `GIT_WORK_TREE`,
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, and injected
`GIT_CONFIG_KEY_*` / `GIT_CONFIG_VALUE_*` values from the environment. It also removes inherited
`GIT_ASKPASS`, `SSH_ASKPASS`, `GIT_SSH`, and `GIT_SSH_COMMAND` values, then sets
`GIT_TERMINAL_PROMPT=0` so remote authentication failures fail fast instead of blocking on an
interactive prompt. Those variables can redirect Git to a different repository, inject config, or run
helper commands, so WebUI does not trust them from the parent process.

Repository-local credential helpers, askpass commands and hooks never run: none of the read-only
commands above invokes them.
