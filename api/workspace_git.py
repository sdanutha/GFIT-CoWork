"""Git helpers for the workspace panel.

The browser only sends session ids and workspace-relative paths.  This module
resolves the active workspace server-side, scopes paths before they become Git
pathspecs, and keeps all Git subprocess calls shell-free and bounded.
"""

from __future__ import annotations

import difflib
import logging
import os
import shutil
import subprocess
import tempfile
import re
from dataclasses import dataclass
from pathlib import Path

from api.subprocess_utils import windows_hide_flags
from api.workspace import resolve_in_workspace

logger = logging.getLogger(__name__)


GIT_TIMEOUT = 5
STATUS_FILE_LIMIT = 500
DIFF_SIZE_LIMIT = 512 * 1024
WORKSPACE_GIT_DESTRUCTIVE_ENV = "HERMES_WEBUI_WORKSPACE_GIT_DESTRUCTIVE"
_GIT_ENV_SCRUB_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
)
_GIT_ENV_SCRUB_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_GIT_HARDENED_CONFIG = (
    # Workspace Git operations can run against repositories provided by agents,
    # restored sessions, or mounted workspaces. Keep repo-local configuration
    # from turning read/status/fetch calls into host command execution.
    ("core.fsmonitor", "false"),
    # Force the unmodified system ssh binary rather than clearing it — an empty
    # value would break legitimate ssh fetches, while "ssh" overrides any
    # repo-local core.sshCommand that points at an attacker helper.
    ("core.sshCommand", "ssh"),
    ("core.askPass", ""),
    ("credential.helper", ""),
    ("protocol.ext.allow", "never"),
    # Neutralize repo-local core.gitProxy, which specifies an external proxy
    # command reachable on `git fetch` against a git:// remote.
    ("core.gitProxy", ""),
    # Prevent submodule operations from recursing into nested repos, which
    # could trigger hooks or fetch from attacker-controlled submodule URLs.
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
)
_GIT_DESTRUCTIVE_HARDENED_CONFIG = (
    # Disable signing helper command resolution while performing destructive
    # Git operations. Hooks are redirected to a temporary empty directory in
    # _run_git() so Git never falls back to .git/hooks.
    ("commit.gpgSign", "false"),
    ("push.gpgSign", "false"),
    ("gpg.program", ""),
    ("gpg.ssh.program", ""),
    ("gpg.x509.program", ""),
    ("core.alternateRefsCommand", ""),
)


def _hardened_git_argv(
    args: list[str],
    *,
    destructive: bool = False,
    attributes_file: str | None = None,
    hooks_path: str | None = None,
) -> list[str]:
    argv = ["git"]
    for key, value in _GIT_HARDENED_CONFIG:
        argv.extend(["-c", f"{key}={value}"])
    if destructive:
        for key, value in _GIT_DESTRUCTIVE_HARDENED_CONFIG:
            argv.extend(["-c", f"{key}={value}"])
        if hooks_path:
            argv.extend(["-c", f"core.hooksPath={hooks_path}"])
    if attributes_file:
        argv.extend(["-c", f"core.attributesFile={attributes_file}"])
    argv.extend(args)
    return argv


def workspace_git_destructive_enabled() -> bool:
    return os.getenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clean_git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    for key in _GIT_ENV_SCRUB_KEYS:
        env.pop(key, None)
    for key in list(env):
        if key.startswith(_GIT_ENV_SCRUB_PREFIXES):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class GitWorkspaceError(RuntimeError):
    """User-facing Git operation error."""

    def __init__(self, message: str, code: str = "git_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GitContext:
    workspace: Path
    repo_root: Path
    workspace_prefix: str


def _classify_git_error(message: str, args: list[str] | None = None) -> str:
    text = (message or "").lower()
    joined = " ".join(args or []).lower()
    if "timed out" in text:
        return "timeout"
    if "not installed" in text or "no such file or directory: 'git'" in text:
        return "missing_git"
    if "not a git repository" in text:
        return "not_a_repo"
    if "outside the workspace" in text or "outside the git repository" in text:
        return "path_outside_workspace"
    if "authentication failed" in text or "permission denied" in text or "could not read username" in text:
        return "auth_failed"
    if "no upstream" in text or "no configured push destination" in text or "has no upstream branch" in text:
        return "no_upstream"
    if (
        "non-fast-forward" in text
        or "fetch first" in text
        or ("rejected" in text and "push" in joined)
    ):
        return "non_fast_forward"
    if "conflict" in text or "unmerged" in text or ("merge" in text and "needs" in text):
        return "conflict"
    if "working tree" in text and ("clean" in text or "dirty" in text):
        return "dirty_worktree"
    if "local changes" in text or "would be overwritten by checkout" in text:
        return "dirty_worktree"
    if "invalid reference" in text or "not a valid" in text or "unknown revision" in text:
        return "invalid_ref"
    if "hook" in text:
        return "hook_failed"
    return "git_failed"


def _run_git(
    ctx_or_cwd: GitContext | Path,
    args: list[str],
    *,
    timeout: int = GIT_TIMEOUT,
    check: bool = False,
    env: dict[str, str] | None = None,
    destructive: bool = False,
    force_destructive_hardening: bool = False,
    disable_filter_attributes: bool = False,
    neutralize_filter_programs: bool = False,
    neutralize_remote_helpers: bool = False,
) -> subprocess.CompletedProcess[str]:
    cwd = ctx_or_cwd.repo_root if isinstance(ctx_or_cwd, GitContext) else ctx_or_cwd
    run_env = _clean_git_env(env)
    effective_destructive = destructive and workspace_git_destructive_enabled()
    hardened_destructive_path = effective_destructive or force_destructive_hardening
    attributes_file = None
    hooks_path = None
    extra_configs: list[tuple[str, str]] = []
    temporary_attributes: list[str] = []
    temporary_dirs: list[str] = []
    try:
        if disable_filter_attributes:
            fd, attributes_path = tempfile.mkstemp(prefix="hermes-webui-git-attrs-")
            os.close(fd)
            attributes_file = attributes_path
            temporary_attributes = [attributes_path]
        if disable_filter_attributes or neutralize_filter_programs:
            # Read/status/fetch paths treat repo-local filter programs as
            # untrusted code. Prefer raw-byte visibility over executing them.
            extra_configs.extend(_destructive_filter_overrides(cwd, run_env))
        if effective_destructive:
            extra_configs.extend(_destructive_merge_driver_overrides(cwd, run_env))
        if effective_destructive or neutralize_remote_helpers:
            extra_configs.extend(_destructive_remote_helper_overrides(cwd, run_env))
            args = _destructive_remote_command_args(args, cwd, run_env)
        if hardened_destructive_path:
            hooks_path = tempfile.mkdtemp(prefix="hermes-webui-git-hooks-")
            temporary_dirs = [hooks_path]
        if extra_configs:
            run_env["GIT_CONFIG_COUNT"] = str(len(extra_configs))
            for i, (key, value) in enumerate(extra_configs):
                run_env[f"GIT_CONFIG_KEY_{i}"] = key
                run_env[f"GIT_CONFIG_VALUE_{i}"] = value
        result = subprocess.run(
            _hardened_git_argv(
                args,
                destructive=hardened_destructive_path,
                attributes_file=attributes_file,
                hooks_path=hooks_path,
            ),
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            creationflags=windows_hide_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GitWorkspaceError("Git command timed out", "timeout") from exc
    except FileNotFoundError as exc:
        raise GitWorkspaceError("Git is not installed or not available on PATH", "missing_git") from exc
    except OSError as exc:
        raise GitWorkspaceError(str(exc), _classify_git_error(str(exc), args)) from exc
    finally:
        for path in temporary_attributes:
            Path(path).unlink(missing_ok=True)
        for path in temporary_dirs:
            shutil.rmtree(path, ignore_errors=True)
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "Git command failed").strip()
        raise GitWorkspaceError(message, _classify_git_error(message, args))
    return result


_FILTER_CONFIG_RE = re.compile(r"^filter\.(.+)\.(clean|smudge|process|required)$")


_MERGE_DRIVER_CONFIG_RE = re.compile(r"^merge\.(.+)\.driver$")
_REMOTE_HELPER_CONFIG_RE = re.compile(r"^remote\.(.+)\.(uploadpack|receivepack)$")


def _config_names_for_scope(
    scope: str,
    cwd: Path,
    env: dict[str, str],
    config_pattern: str,
    name_re: re.Pattern[str],
    *,
    ignore_unsupported: bool = False,
) -> set[str]:
    result = subprocess.run(
        ["git", "config", "--includes", scope, "--name-only", "--get-regexp", config_pattern],
        cwd=str(cwd),
        shell=False,
        text=True,
        capture_output=True,
        timeout=GIT_TIMEOUT,
        env=env,
        creationflags=windows_hide_flags(),
    )
    if result.returncode not in {0, 1}:
        if ignore_unsupported:
            return set()
        message = (result.stderr or result.stdout or "Git command failed").strip()
        raise GitWorkspaceError(message, _classify_git_error(message, ["config"]))
    names: set[str] = set()
    for line in (result.stdout or "").splitlines():
        match = name_re.match(line.strip())
        if match:
            names.add(match.group(1))
    return names


def _filter_names_for_scope(
    scope: str,
    cwd: Path,
    env: dict[str, str],
    *,
    ignore_unsupported: bool = False,
) -> set[str]:
    return _config_names_for_scope(
        scope,
        cwd,
        env,
        r"^filter\..*\.(clean|smudge|process|required)$",
        _FILTER_CONFIG_RE,
        ignore_unsupported=ignore_unsupported,
    )


def _merge_driver_names_for_scope(
    scope: str,
    cwd: Path,
    env: dict[str, str],
    *,
    ignore_unsupported: bool = False,
) -> set[str]:
    return _config_names_for_scope(
        scope,
        cwd,
        env,
        r"^merge\..*\.driver$",
        _MERGE_DRIVER_CONFIG_RE,
        ignore_unsupported=ignore_unsupported,
    )


def _remote_helper_names_for_scope(
    scope: str,
    cwd: Path,
    env: dict[str, str],
    *,
    ignore_unsupported: bool = False,
) -> set[str]:
    return _config_names_for_scope(
        scope,
        cwd,
        env,
        r"^remote\..*\.(uploadpack|receivepack)$",
        _REMOTE_HELPER_CONFIG_RE,
        ignore_unsupported=ignore_unsupported,
    )


def _destructive_filter_overrides(cwd: Path, env: dict[str, str]) -> list[tuple[str, str]]:
    names = _filter_names_for_scope("--local", cwd, env)
    names |= _filter_names_for_scope(
        "--worktree",
        cwd,
        env,
        ignore_unsupported=True,
    )
    overrides: list[tuple[str, str]] = []
    for name in sorted(names):
        if "\n" in name or "\0" in name:
            logger.warning("Skipping filter name with illegal characters: %r", name)
            continue
        overrides.extend(
            [
                (f"filter.{name}.clean", "cat"),
                (f"filter.{name}.smudge", "cat"),
                (f"filter.{name}.process", ""),
                (f"filter.{name}.required", "false"),
            ]
        )
    return overrides


def _destructive_merge_driver_overrides(cwd: Path, env: dict[str, str]) -> list[tuple[str, str]]:
    names = _merge_driver_names_for_scope("--local", cwd, env)
    names |= _merge_driver_names_for_scope(
        "--worktree",
        cwd,
        env,
        ignore_unsupported=True,
    )
    # Replace repo-defined merge drivers with Git's trusted three-way merge
    # binary so stash restores cannot invoke workspace-controlled helpers.
    overrides: list[tuple[str, str]] = []
    for name in sorted(names):
        if "\n" in name or "\0" in name:
            logger.warning("Skipping merge driver name with illegal characters: %r", name)
            continue
        overrides.append((f"merge.{name}.driver", 'git merge-file "%A" "%O" "%B"'))
    return overrides


def _destructive_remote_helper_overrides(cwd: Path, env: dict[str, str]) -> list[tuple[str, str]]:
    names = _remote_helper_names_for_scope("--local", cwd, env)
    names |= _remote_helper_names_for_scope(
        "--worktree",
        cwd,
        env,
        ignore_unsupported=True,
    )
    overrides: list[tuple[str, str]] = []
    for name in sorted(names):
        if "\n" in name or "\0" in name:
            logger.warning("Skipping remote helper name with illegal characters: %r", name)
            continue
        overrides.extend(
            [
                (f"remote.{name}.uploadpack", "git-upload-pack"),
                (f"remote.{name}.receivepack", "git-receive-pack"),
            ]
        )
    return overrides


def _destructive_remote_command_args(args: list[str], cwd: Path, env: dict[str, str]) -> list[str]:
    if not args:
        return args
    names = _remote_helper_names_for_scope("--local", cwd, env)
    names |= _remote_helper_names_for_scope(
        "--worktree",
        cwd,
        env,
        ignore_unsupported=True,
    )
    if not names:
        return args
    command = args[0]
    if command in {"fetch", "pull"}:
        return [command, "--upload-pack=git-upload-pack", *args[1:]]
    if command == "push":
        return [command, "--receive-pack=git-receive-pack", *args[1:]]
    return args


def _has_repo_local_filters(cwd: Path, env: dict[str, str]) -> bool:
    names = _filter_names_for_scope("--local", cwd, env)
    names |= _filter_names_for_scope("--worktree", cwd, env, ignore_unsupported=True)
    return bool(names)


def _block_filtered_destructive_write(ctx: GitContext, message: str) -> None:
    if workspace_git_destructive_enabled() and _has_repo_local_filters(ctx.repo_root, _clean_git_env()):
        raise GitWorkspaceError(message, "filtered_path")


def resolve_git_context(workspace: str | Path) -> GitContext | None:
    ws = Path(workspace).expanduser().resolve()
    result = _run_git(ws, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    repo_root = Path(result.stdout.strip()).resolve()
    try:
        prefix = ws.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    return GitContext(workspace=ws, repo_root=repo_root, workspace_prefix="" if prefix == "." else prefix)


def _workspace_pathspec(ctx: GitContext) -> str:
    return ctx.workspace_prefix or "."


def _repo_rel(ctx: GitContext, workspace_rel: str) -> str:
    try:
        target = resolve_in_workspace(ctx.workspace, workspace_rel or ".")
    except ValueError as exc:
        raise GitWorkspaceError(str(exc), "path_outside_workspace") from exc
    try:
        repo_rel = target.relative_to(ctx.repo_root).as_posix()
    except ValueError as exc:
        raise GitWorkspaceError("Path is outside the Git repository", "path_outside_workspace") from exc
    if ctx.workspace_prefix:
        try:
            target.relative_to(ctx.workspace)
        except ValueError as exc:
            raise GitWorkspaceError("Path is outside the workspace", "path_outside_workspace") from exc
    return repo_rel


def _workspace_rel(ctx: GitContext, repo_rel: str) -> str | None:
    repo_rel = repo_rel.replace("\\", "/")
    if not ctx.workspace_prefix:
        return repo_rel
    prefix = ctx.workspace_prefix.rstrip("/") + "/"
    if repo_rel == ctx.workspace_prefix:
        return "."
    if repo_rel.startswith(prefix):
        return repo_rel[len(prefix) :]
    return None


def _empty_status() -> dict:
    return {
        "changed": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "conflicts": 0,
    }


def _status_code(xy: str, *, untracked: bool = False, renamed: bool = False) -> str:
    if untracked:
        return "??"
    if xy in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
        return xy
    if renamed:
        return "R"
    for ch in xy:
        if ch in "MADRCUT":
            return ch
    return xy.strip(".") or "M"


def _parse_numstat(text: str, ctx: GitContext) -> dict[str, tuple[int, int, bool]]:
    stats: dict[str, tuple[int, int, bool]] = {}
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        raw_add, raw_del, raw_path = parts
        binary = raw_add == "-" or raw_del == "-"
        additions = 0 if binary else int(raw_add or "0")
        deletions = 0 if binary else int(raw_del or "0")
        workspace_path = _workspace_rel(ctx, raw_path)
        if workspace_path is None:
            continue
        stats[workspace_path] = (additions, deletions, binary)
    return stats


def _parse_path_list(text: str, ctx: GitContext) -> set[str]:
    paths: set[str] = set()
    for raw_path in text.split("\0"):
        if not raw_path:
            continue
        workspace_path = _workspace_rel(ctx, raw_path)
        if workspace_path is not None:
            paths.add(workspace_path)
    return paths


def _collect_diff_paths(ctx: GitContext, cached: bool, *, ignore_cr_at_eol: bool = True) -> set[str] | None:
    args = ["diff", "--name-only", "-z"]
    args.append("--no-textconv")
    if ignore_cr_at_eol:
        args.append("--ignore-cr-at-eol")
    if cached:
        args.append("--cached")
    args.extend(["--", _workspace_pathspec(ctx)])
    result = _run_git(
        ctx,
        args,
        check=False,
        disable_filter_attributes=workspace_git_destructive_enabled(),
        neutralize_filter_programs=True,
    )
    if result.returncode != 0:
        return None
    return _parse_path_list(result.stdout, ctx)


def _collect_numstat(
    ctx: GitContext,
    cached: bool,
    *,
    ignore_cr_at_eol: bool = True,
) -> dict[str, tuple[int, int, bool]]:
    args = ["diff", "--numstat"]
    args.append("--no-textconv")
    if ignore_cr_at_eol:
        args.append("--ignore-cr-at-eol")
    if cached:
        args.append("--cached")
    args.extend(["--", _workspace_pathspec(ctx)])
    result = _run_git(
        ctx,
        args,
        check=False,
        disable_filter_attributes=workspace_git_destructive_enabled(),
        neutralize_filter_programs=True,
    )
    if result.returncode != 0:
        return {}
    return _parse_numstat(result.stdout, ctx)


def _count_untracked_file(path: Path) -> tuple[int, int, bool]:
    try:
        if not path.is_file() or path.stat().st_size > DIFF_SIZE_LIMIT:
            return 0, 0, False
    except OSError:
        return 0, 0, False
    try:
        data = path.read_bytes()
    except OSError:
        return 0, 0, False
    if b"\0" in data:
        return 0, 0, True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return 0, 0, True
    return len(text.splitlines()) or (1 if text else 0), 0, False


def git_status(workspace: str | Path) -> dict:
    ctx = resolve_git_context(workspace)
    if ctx is None:
        return {"is_git": False}

    result = _run_git(
        ctx,
        [
            "status",
            "--porcelain=v2",
            "-z",
            "--branch",
            "--ignored=matching",
            "--untracked-files=all",
            "--",
            _workspace_pathspec(ctx),
        ],
        check=True,
        disable_filter_attributes=workspace_git_destructive_enabled(),
        neutralize_filter_programs=True,
    )
    staged_stats = _collect_numstat(ctx, cached=True)
    unstaged_stats = _collect_numstat(ctx, cached=False)
    staged_raw_stats = _collect_numstat(ctx, cached=True, ignore_cr_at_eol=False)
    unstaged_raw_stats = _collect_numstat(ctx, cached=False, ignore_cr_at_eol=False)
    staged_diff_paths = _collect_diff_paths(ctx, cached=True)
    unstaged_diff_paths = _collect_diff_paths(ctx, cached=False)

    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    files: dict[str, dict] = {}
    filtered_noise = {"filemode_only": 0, "crlf_only": 0}
    tokens = result.stdout.split("\0")
    i = 0
    truncated = False
    while i < len(tokens):
        rec = tokens[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# "):
            parts = rec.split(" ", 2)
            if len(parts) >= 3 and parts[1] == "branch.head":
                branch = "" if parts[2] == "(detached)" else parts[2]
            elif len(parts) >= 3 and parts[1] == "branch.upstream":
                upstream = parts[2]
            elif len(parts) >= 3 and parts[1] == "branch.ab":
                for bit in parts[2].split():
                    if bit.startswith("+") and bit[1:].isdigit():
                        ahead = int(bit[1:])
                    elif bit.startswith("-") and bit[1:].isdigit():
                        behind = int(bit[1:])
            continue

        old_path = None
        renamed = False
        if rec.startswith("? "):
            xy = "??"
            repo_path = rec[2:]
            untracked = True
            ignored = False
        elif rec.startswith("! "):
            xy = "!!"
            repo_path = rec[2:]
            untracked = False
            ignored = True
        elif rec.startswith("1 "):
            parts = rec.split(" ", 8)
            if len(parts) < 9:
                continue
            xy = parts[1]
            repo_path = parts[8]
            untracked = False
            ignored = False
        elif rec.startswith("2 "):
            parts = rec.split(" ", 9)
            if len(parts) < 10:
                continue
            xy = parts[1]
            repo_path = parts[9]
            if i < len(tokens):
                old_path = tokens[i]
                i += 1
            renamed = True
            untracked = False
            ignored = False
        elif rec.startswith("u "):
            parts = rec.split(" ", 10)
            if len(parts) < 11:
                continue
            xy = parts[1]
            repo_path = parts[10]
            untracked = False
            ignored = False
        else:
            continue

        workspace_path = _workspace_rel(ctx, repo_path)
        if workspace_path is None:
            continue
        old_workspace_path = _workspace_rel(ctx, old_path) if old_path else None
        x = xy[0] if xy else "."
        y = xy[1] if len(xy) > 1 else "."
        conflict = xy in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"} or rec.startswith("u ")
        additions, deletions, binary = 0, 0, False
        for source in (staged_stats, unstaged_stats):
            if workspace_path in source:
                a, d, b = source[workspace_path]
                additions += a
                deletions += d
                binary = binary or b
        if untracked:
            additions, deletions, binary = _count_untracked_file(ctx.workspace / workspace_path)

        staged = (x not in {".", "?"}) and not untracked
        unstaged = (y not in {".", " "}) and not untracked
        if staged and staged_diff_paths is not None and not renamed:
            raw_staged = staged
            staged = workspace_path in staged_diff_paths or (
                old_workspace_path is not None and old_workspace_path in staged_diff_paths
            )
            if raw_staged and not staged:
                if workspace_path in staged_raw_stats or (
                    old_workspace_path is not None and old_workspace_path in staged_raw_stats
                ):
                    filtered_noise["crlf_only"] += 1
                else:
                    filtered_noise["filemode_only"] += 1
        if unstaged and unstaged_diff_paths is not None and not renamed:
            raw_unstaged = unstaged
            unstaged = workspace_path in unstaged_diff_paths or (
                old_workspace_path is not None and old_workspace_path in unstaged_diff_paths
            )
            if raw_unstaged and not unstaged:
                if workspace_path in unstaged_raw_stats or (
                    old_workspace_path is not None and old_workspace_path in unstaged_raw_stats
                ):
                    filtered_noise["crlf_only"] += 1
                else:
                    filtered_noise["filemode_only"] += 1
        if ignored:
            files[workspace_path] = {
                "path": workspace_path,
                "old_path": None,
                "workspace_path": workspace_path,
                "status": "Ignored",
                "staged": False,
                "unstaged": False,
                "untracked": False,
                "ignored": True,
                "conflict": False,
                "additions": 0,
                "deletions": 0,
                "binary": False,
            }
            if len(files) >= STATUS_FILE_LIMIT:
                truncated = True
                break
            continue

        if not (staged or unstaged or untracked or conflict or renamed):
            continue
        if not (untracked or conflict or renamed or binary) and additions == 0 and deletions == 0:
            filtered_noise["crlf_only"] += 1
            continue

        files[workspace_path] = {
            "path": workspace_path,
            "old_path": old_workspace_path,
            "workspace_path": workspace_path,
            "status": _status_code(xy, untracked=untracked, renamed=renamed),
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "ignored": False,
            "conflict": conflict,
            "additions": additions,
            "deletions": deletions,
            "binary": binary,
        }
        if len(files) >= STATUS_FILE_LIMIT:
            truncated = True
            break

    file_list = sorted(files.values(), key=lambda f: (f["path"].lower()))
    totals = _empty_status()
    for item in file_list:
        if item.get("ignored"):
            continue
        if item["staged"]:
            totals["staged"] += 1
        if item["unstaged"]:
            totals["unstaged"] += 1
        if item["untracked"]:
            totals["untracked"] += 1
        if item["conflict"]:
            totals["conflicts"] += 1
    totals["changed"] = sum(1 for item in file_list if not item.get("ignored"))

    if not branch:
        branch = (_run_git(ctx, ["rev-parse", "--short", "HEAD"], check=False).stdout or "").strip()
    return {
        "is_git": True,
        "branch": branch or "HEAD",
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "totals": totals,
        "files": file_list,
        "truncated": truncated,
        "noise_filtering": {
            **filtered_noise,
            "active": any(filtered_noise.values()),
        },
    }


def _branch_ahead_behind(ctx: GitContext, branch: str, upstream: str) -> tuple[int, int]:
    if not upstream:
        return 0, 0
    result = _run_git(ctx, ["rev-list", "--left-right", "--count", f"{branch}...{upstream}"], check=False)
    if result.returncode != 0:
        return 0, 0
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def _for_each_ref(ctx: GitContext, ref_prefix: str) -> list[dict]:
    fmt = (
        "%(refname)%00%(refname:short)%00%(upstream:short)%00%(objectname:short)%00"
        "%(committerdate:unix)%00%(committerdate:relative)%00%(authorname)%00%(subject)"
    )
    result = _run_git(ctx, ["for-each-ref", f"--format={fmt}", ref_prefix], check=True)
    refs = []
    for line in result.stdout.splitlines():
        full_name, name, upstream, sha, updated, updated_relative, author, subject = (
            line.split("\0") + ["", "", "", "", "", "", "", ""]
        )[:8]
        if not name or full_name.endswith("/HEAD") or name.endswith("/HEAD"):
            continue
        if ref_prefix == "refs/remotes" and "/" not in name:
            continue
        item = {
            "name": name,
            "sha": sha,
            "updated": int(updated) if str(updated).isdigit() else 0,
            "updated_relative": updated_relative,
            "author": author,
            "subject": subject,
        }
        if upstream:
            ahead, behind = _branch_ahead_behind(ctx, name, upstream)
            item.update({"upstream": upstream, "ahead": ahead, "behind": behind})
        else:
            item.update({"upstream": "", "ahead": 0, "behind": 0})
        refs.append(item)
    return sorted(refs, key=lambda item: item["name"].lower())


def git_branches(workspace: str | Path) -> dict:
    ctx = resolve_git_context(workspace)
    if ctx is None:
        raise GitWorkspaceError("Workspace is not a Git repository", "not_a_repo")
    head_name = _run_git(ctx, ["branch", "--show-current"], check=True).stdout.strip()
    detached = not bool(head_name)
    head_sha = _run_git(ctx, ["rev-parse", "--short", "HEAD"], check=True).stdout.strip()
    status = git_status(workspace)
    local = _for_each_ref(ctx, "refs/heads")
    remote = _for_each_ref(ctx, "refs/remotes")
    return {
        "is_git": True,
        "current": head_name or head_sha or "HEAD",
        "detached": detached,
        "head": head_sha,
        "local": local,
        "remote": remote,
        "upstream": status.get("upstream", ""),
        "ahead": status.get("ahead", 0),
        "behind": status.get("behind", 0),
    }


def _diff_stats(diff_text: str) -> tuple[int, int]:
    additions = deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _synthetic_untracked_diff(path: Path, label: str) -> dict:
    try:
        if not path.is_file():
            raise GitWorkspaceError("Path is not a file")
        if path.stat().st_size > DIFF_SIZE_LIMIT:
            return {
                "binary": False,
                "too_large": True,
                "diff": "",
                "additions": 0,
                "deletions": 0,
            }
    except OSError as exc:
        raise GitWorkspaceError(str(exc)) from exc
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GitWorkspaceError(str(exc)) from exc
    if b"\0" in data:
        return {"binary": True, "too_large": False, "diff": "", "additions": 0, "deletions": 0}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"binary": True, "too_large": False, "diff": "", "additions": 0, "deletions": 0}
    lines = text.splitlines()
    diff_lines = list(
        difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{label}", lineterm="")
    )
    diff = "\n".join(diff_lines) + ("\n" if diff_lines else "")
    too_large = len(diff.encode("utf-8", errors="replace")) > DIFF_SIZE_LIMIT
    if too_large:
        diff = diff[:DIFF_SIZE_LIMIT]
    additions, deletions = _diff_stats(diff)
    return {
        "binary": False,
        "too_large": too_large,
        "diff": diff,
        "additions": additions,
        "deletions": deletions,
    }


def git_diff(workspace: str | Path, path: str, kind: str = "unstaged") -> dict:
    ctx = resolve_git_context(workspace)
    if ctx is None:
        raise GitWorkspaceError("Workspace is not a Git repository")
    if kind not in {"unstaged", "staged"}:
        raise GitWorkspaceError("kind must be staged or unstaged")
    repo_rel = _repo_rel(ctx, path)
    workspace_rel = _workspace_rel(ctx, repo_rel) or path

    status = git_status(workspace)
    file_state = next((f for f in status.get("files", []) if f.get("path") == workspace_rel), None)
    if kind == "unstaged" and file_state and file_state.get("untracked"):
        payload = _synthetic_untracked_diff(ctx.workspace / workspace_rel, workspace_rel)
        return {"path": workspace_rel, "kind": kind, **payload}

    args = ["diff", "--no-ext-diff", "--no-textconv", "--unified=3"]
    if kind == "staged":
        args.append("--cached")
    args.extend(["--", repo_rel])
    result = _run_git(ctx, args, check=True, neutralize_filter_programs=True)
    diff = result.stdout
    binary = "Binary files " in diff or "GIT binary patch" in diff
    too_large = len(diff.encode("utf-8", errors="replace")) > DIFF_SIZE_LIMIT
    if too_large:
        diff = diff[:DIFF_SIZE_LIMIT]
    additions, deletions = _diff_stats(diff)
    return {
        "path": workspace_rel,
        "kind": kind,
        "binary": binary,
        "too_large": too_large,
        "additions": additions,
        "deletions": deletions,
        "diff": "" if binary else diff,
    }


def _selected_temp_index_env(ctx: GitContext, specs: list[str]) -> tuple[dict[str, str], str]:
    _block_filtered_destructive_write(
        ctx,
        "Repository uses local Git filters; selected commit staging may corrupt index content. "
        "Use the terminal to commit manually.",
    )
    fd, index_path = tempfile.mkstemp(prefix="hermes-webui-git-index-")
    os.close(fd)
    Path(index_path).unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": index_path}
    try:
        head = _run_git(
            ctx,
            ["rev-parse", "--verify", "HEAD"],
            check=False,
            env=env,
            destructive=True,
        )
        if head.returncode == 0:
            _run_git(ctx, ["read-tree", "HEAD"], check=True, env=env, destructive=True)
        else:
            _run_git(ctx, ["read-tree", "--empty"], check=True, env=env, destructive=True)
        _run_git(
            ctx,
            ["add", "-A", "--", *specs],
            check=True,
            env=env,
            destructive=True,
            disable_filter_attributes=True,
        )
        return env, index_path
    except Exception:
        Path(index_path).unlink(missing_ok=True)
        raise

