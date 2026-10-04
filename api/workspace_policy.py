"""GFIT-CoWork -- one Workspace policy answers every Workspace question for a request.

Each request has one Workspace policy, chosen from the request's Admission
(:func:`request_workspace_policy`):

- a **User's policy** (:class:`UserWorkspacePolicy`): everything is inside that
  User's Workspace folder, ``<Profile>/workspace`` (ADR 0002);
- the **unconfined policy** (:data:`UNCONFINED`): the Admin, and requests with
  no Admission (login turned off, worker threads). Today's rules, including
  remote-terminal Workspaces and the saved-list rules;
- the **refusing answer** (:data:`REFUSING`): a Directory session with no
  recorded Admission, or an Admission this module does not understand.
  Unknown is not allowed.

The policy answers: the default Workspace for a new session, the saved
Workspace list cleaned for this caller, whether a path may be used as a
session's or the last-used Workspace, resolving a Workspace to use and to
register (the register target is checked before any folder is created), the
roots file operations may reach, confining a resolved file path, whether the
media viewer may serve a file, and whether a worktree path may become the
session's Workspace.

Every path check runs on fully resolved paths (``..`` and symlinks followed).
Refusals raise ``ValueError`` with the Workspace message.

Attachments are not a Workspace: they belong to a session, and session
ownership guards them (``api.helpers.resolve_inside``, the unconfined primitive).

An explicit *profile* argument is the Admin's: the unconfined policy uses it as
today. A User's policy is bound to that User's Profile, which wins; it
accepts *profile* only so every policy is asked the same way, and ignores it.
"""
from __future__ import annotations

from pathlib import Path

from api.workspace import (
    OUTSIDE_WORKSPACE_MESSAGE,
    USER_WORKSPACE_DIRNAME,
    _clean_unconfined_workspace_list,
    _clean_user_workspace_list,
    _configured_default_workspace,
    _expanduser_path,
    _is_within,
    _refuse_system_folder,
    _remote_cwd_for,
    _resolve_unconfined_workspace,
    _safe_resolve,
    _strip_surrounding_quotes,
    _unconfined_file_roots,
    _unconfined_may_use,
    _unconfined_register_target,
    _validate_unconfined_workspace_to_add,
    _workspace_access_error,
)


def _requested_path(path: str | Path) -> Path:
    """A requested Workspace path, unquoted and home-expanded (not yet resolved)."""
    return _expanduser_path(_strip_surrounding_quotes(str(path)).strip())


class UserWorkspacePolicy:
    """A User's policy: everything inside their Workspace folder, ``<Profile>/workspace``.

    Its methods accept the unconfined policy's *profile* keyword so callers ask
    every policy the same way. The User's own Profile, fixed when the policy is
    chosen, wins: *profile* is never used here.
    """

    def __init__(self, profile_home: str | Path):
        self.profile_home = _safe_resolve(Path(profile_home))
        self.root = _safe_resolve(self.profile_home / USER_WORKSPACE_DIRNAME)

    @classmethod
    def for_profile(cls, profile: str) -> "UserWorkspacePolicy":
        """The policy of User *profile*; raises ValueError for a name that is not a Profile's."""
        from api.profiles import _resolve_named_profile_home

        return cls(_resolve_named_profile_home(profile))

    def default_workspace(self, *, profile=None) -> str:
        """The User's Workspace folder, created if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        return str(self.root)

    def saved_list(self, workspaces: list, *, profile=None) -> list:
        """The default Workspace first ("Home"), then saved folders inside it."""
        return _clean_user_workspace_list(workspaces, self.root)

    def may_use(self, raw: str | None, *, profile=None) -> bool:
        """May the stored value *raw* be a session's or the last-used Workspace?"""
        if not raw:
            return False
        try:
            self.resolve_to_use(raw)
            return True
        except ValueError:
            return False

    def resolve_to_use(self, path: str | Path | None, *, profile=None) -> Path:
        """The default Workspace for an empty path, else an existing folder inside it."""
        if path in (None, ""):
            return Path(self.default_workspace())
        candidate = self.confine(_requested_path(path))
        access_error = _workspace_access_error(candidate)
        if access_error:
            raise ValueError(access_error)
        return candidate

    def resolve_to_register(self, path: str, *, profile=None) -> Path:
        """A folder to add to the saved list: an existing folder inside the Workspace."""
        return self.resolve_to_use(path)

    def register_target(self, path: str, *, profile=None) -> Path:
        """The folder that may be created to register *path*; refused before anything is created.

        A blocked system folder is refused with its own message first, as for the Admin.
        """
        candidate = _requested_path(path)
        _refuse_system_folder(_safe_resolve(candidate))
        return self.confine(candidate)

    def file_roots(self, *, profile=None) -> list[Path]:
        return [self.root] if self.root.is_dir() else []

    def confine(self, path: Path) -> Path:
        """Resolved *path*, or ValueError when it is outside the Workspace."""
        resolved = _safe_resolve(Path(path))
        if not _is_within(resolved, self.root):
            raise ValueError(OUTSIDE_WORKSPACE_MESSAGE)
        return resolved

    def may_serve_media(self, target: Path) -> bool:
        """The User's own Profile; the media route's deny-list still applies."""
        return _is_within(_safe_resolve(Path(target)), self.profile_home)

    def may_become_worktree(self, path: Path) -> bool:
        try:
            self.confine(path)
            return True
        except ValueError:
            return False


class _UnconfinedWorkspacePolicy:
    """The Admin's, and no caller's: today's rules, not confined."""

    def default_workspace(self, *, profile: str | Path | None = None) -> str:
        return _configured_default_workspace(profile)

    def saved_list(self, workspaces: list, *, profile: str | Path | None = None) -> list:
        return _clean_unconfined_workspace_list(workspaces, profile)

    def may_use(self, raw: str | None, *, profile: str | Path | None = None) -> bool:
        return bool(raw) and _unconfined_may_use(raw, profile, _remote_cwd_for(profile))

    def resolve_to_use(self, path: str | Path | None, *, profile: str | Path | None = None) -> Path:
        return _resolve_unconfined_workspace(path, profile)

    def resolve_to_register(self, path: str, *, profile: str | Path | None = None) -> Path:
        return _validate_unconfined_workspace_to_add(path, profile)

    def register_target(self, path: str, *, profile: str | Path | None = None) -> Path | None:
        """None when *path* is target-side for a remote terminal (nothing to create here)."""
        return _unconfined_register_target(path, profile)

    def file_roots(self, *, profile: str | Path | None = None) -> list[Path]:
        return _unconfined_file_roots(profile)

    def confine(self, path: Path) -> Path:
        return path

    def may_serve_media(self, target: Path) -> bool:
        """No Profile rule; the media route's own allowed roots apply."""
        return True

    def may_become_worktree(self, path: Path) -> bool:
        return True


class _RefusingWorkspacePolicy:
    """The refusing answer: nothing may be used, reached or served."""

    def _refuse(self, *_args, **_kwargs):
        raise ValueError(OUTSIDE_WORKSPACE_MESSAGE)

    default_workspace = resolve_to_use = resolve_to_register = register_target = confine = _refuse

    def saved_list(self, workspaces: list, **_kwargs) -> list:
        return []

    def may_use(self, raw, **_kwargs) -> bool:
        return False

    def file_roots(self, **_kwargs) -> list[Path]:
        return []

    def may_serve_media(self, target: Path) -> bool:
        return False

    def may_become_worktree(self, path: Path) -> bool:
        return False


UNCONFINED = _UnconfinedWorkspacePolicy()
REFUSING = _RefusingWorkspacePolicy()


def policy_for(admission, *, directory_session: bool):
    """The Workspace policy for *admission*: the one mapping from Admission to policy.

    A User's Admission gives that User's policy, the Admin's gives the
    unconfined one. No Admission is unconfined only when there is no Directory
    session (login turned off, a worker thread); a Directory session with none
    is refused, as is a role or Profile this module does not understand.
    """
    from api.access import ROLE_ADMIN, ROLE_USER

    if admission is None:
        return REFUSING if directory_session else UNCONFINED
    if admission.role == ROLE_ADMIN:
        return UNCONFINED
    if admission.role == ROLE_USER and admission.profile:
        try:
            return UserWorkspacePolicy.for_profile(admission.profile)
        except ValueError:
            return REFUSING
    return REFUSING


def request_workspace_policy():
    """This request's Workspace policy, from the request's Admission."""
    from api.access import request_admission, request_has_directory_session

    return policy_for(request_admission(), directory_session=request_has_directory_session())
