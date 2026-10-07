import json
import pathlib
import subprocess
import types
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

import pytest

from tests._pytest_port import BASE


ROOT = pathlib.Path(__file__).parent.parent


def _git(cwd, *args):
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        shell=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=str(path),
        shell=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if init.returncode != 0:
        _git(path, "init")
        _git(path, "checkout", "-B", "master")
    _git(path, "config", "user.email", "hermes-tests@example.invalid")
    _git(path, "config", "user.name", "Hermes Tests")
    return path


def _init_bare_repo(path):
    init = subprocess.run(
        ["git", "init", "--bare", "-b", "master", str(path)],
        shell=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if init.returncode != 0:
        _git(path.parent, "init", "--bare", str(path))
        _git(path, "symbolic-ref", "HEAD", "refs/heads/master")
    return path


def _commit_all(path, message="initial"):
    _git(path, "add", ".")
    _git(path, "commit", "-m", message)


def _get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _make_session(created_list, ws=None):
    body = {}
    if ws:
        body["workspace"] = str(ws)
    data, status = _post("/api/session/new", body)
    assert status == 200
    sid = data["session"]["session_id"]
    created_list.append(sid)
    return sid, pathlib.Path(data["session"]["workspace"])


class _CaptureHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.response_headers = []
        self.wfile = BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass

    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def test_git_status_non_git_workspace(tmp_path):
    from api.workspace_git import git_status

    ws = tmp_path / "plain"
    ws.mkdir()
    assert git_status(ws) == {"is_git": False}


def test_git_status_handles_staged_unstaged_untracked_deleted_and_renamed(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    (repo / "delete-me.txt").write_text("bye\n", encoding="utf-8")
    (repo / "old name.txt").write_text("move\n", encoding="utf-8")
    _commit_all(repo)

    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "delete-me.txt").unlink()
    _git(repo, "mv", "old name.txt", "new name.txt")
    (repo / "untracked space.txt").write_text("new\nfile\n", encoding="utf-8")

    status = git_status(repo)
    by_path = {item["path"]: item for item in status["files"]}

    assert status["is_git"] is True
    assert by_path["tracked.txt"]["unstaged"] is True
    assert by_path["staged.txt"]["staged"] is True
    assert by_path["delete-me.txt"]["status"] == "D"
    assert by_path["new name.txt"]["old_path"] == "old name.txt"
    assert by_path["untracked space.txt"]["untracked"] is True
    assert by_path["untracked space.txt"]["additions"] == 2
    assert status["totals"]["changed"] >= 5


def test_git_status_reports_ignored_files_without_counting_them_as_changes(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)

    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "debug.log").write_text("ignored log\n", encoding="utf-8")
    build = repo / "build"
    build.mkdir()
    (build / "artifact.txt").write_text("ignored artifact\n", encoding="utf-8")

    status = git_status(repo)
    by_path = {item["path"]: item for item in status["files"]}

    assert by_path["tracked.txt"]["unstaged"] is True
    assert by_path["debug.log"]["ignored"] is True
    assert by_path["debug.log"]["status"] == "Ignored"
    assert by_path["build/"]["ignored"] is True
    assert by_path["build/"]["staged"] is False
    assert by_path["build/"]["untracked"] is False
    assert status["totals"]["changed"] == 1
    assert status["totals"]["untracked"] == 0


def test_git_status_ignores_crlf_only_worktree_noise(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    _commit_all(repo)

    (repo / "tracked.txt").write_text("one\r\ntwo\r\n", encoding="utf-8", newline="")

    raw = _git(repo, "status", "--porcelain", "--", "tracked.txt")
    assert raw.startswith(" M")

    status = git_status(repo)
    assert status["totals"]["changed"] == 0
    assert status["files"] == []
    assert status["noise_filtering"]["active"] is True
    assert status["noise_filtering"]["crlf_only"] == 1


def test_git_status_keeps_real_edit_with_crlf_endings(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    _commit_all(repo)

    (repo / "tracked.txt").write_text("one\r\ntwo\r\nthree\r\n", encoding="utf-8", newline="")

    status = git_status(repo)
    by_path = {item["path"]: item for item in status["files"]}
    assert status["totals"]["changed"] == 1
    assert by_path["tracked.txt"]["unstaged"] is True
    assert by_path["tracked.txt"]["additions"] == 1
    assert by_path["tracked.txt"]["deletions"] == 0


def test_git_status_ignores_filemode_only_noise(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    script = repo / "script.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _commit_all(repo)

    _git(repo, "update-index", "--chmod=+x", "script.sh")

    raw = _git(repo, "status", "--porcelain", "--", "script.sh")
    assert "script.sh" in raw

    status = git_status(repo)
    assert status["totals"]["changed"] == 0
    assert status["files"] == []
    assert status["noise_filtering"]["active"] is True


def test_git_status_scopes_nested_workspace_to_that_directory(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    nested = repo / "app"
    nested.mkdir()
    (nested / "inside.txt").write_text("inside\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    _commit_all(repo)

    (nested / "inside.txt").write_text("inside\nchanged\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\nchanged\n", encoding="utf-8")

    status = git_status(nested)
    paths = {item["path"] for item in status["files"]}
    assert paths == {"inside.txt"}


def test_git_diff_generates_untracked_text_diff_and_blocks_escape(tmp_path):
    from api.workspace_git import GitWorkspaceError, git_diff

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    (repo / "new file.txt").write_text("hello\nworld\n", encoding="utf-8")

    diff = git_diff(repo, "new file.txt", "unstaged")
    assert diff["binary"] is False
    assert "+++ b/new file.txt" in diff["diff"]
    assert "+hello" in diff["diff"]

    with pytest.raises(GitWorkspaceError):
        git_diff(repo, "../outside.txt", "unstaged")


def test_git_diff_skips_repo_local_textconv(tmp_path):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("scripted textconv helper setup is POSIX-only")

    from api.workspace_git import git_diff

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text("*.txt diff=demo\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    marker = tmp_path / "git-diff-textconv-ran"
    helper = tmp_path / "git_diff_textconv_helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('textconv ran', encoding='utf-8')\n"
        "print('converted')\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    _git(repo, "config", "diff.demo.textconv", f'"{sys.executable}" "{helper}" "{marker}"')

    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")

    diff = git_diff(repo, "tracked.txt", "unstaged")

    assert "+two" in diff["diff"]
    assert "converted" not in diff["diff"]
    assert not marker.exists()


def test_git_diff_skips_repo_local_clean_filter_without_destructive_mode(tmp_path, monkeypatch):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("scripted clean filter setup is POSIX-only")

    from api.workspace_git import WORKSPACE_GIT_DESTRUCTIVE_ENV, git_diff

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text("*.txt filter=demo\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    marker = tmp_path / "git-diff-clean-filter-ran"
    helper = tmp_path / "git_diff_clean_filter_helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('clean filter ran', encoding='utf-8')\n"
        "print(sys.stdin.read(), end='')\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    _git(repo, "config", "filter.demo.clean", f'"{sys.executable}" "{helper}" "{marker}"')
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")

    monkeypatch.delenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, raising=False)
    diff = git_diff(repo, "tracked.txt", "unstaged")

    assert "+two" in diff["diff"]
    assert not marker.exists()


def test_git_status_reports_ignored_files_without_counting_them_as_changed(tmp_path):
    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)

    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "debug.log").write_text("ignored log\n", encoding="utf-8")
    build = repo / "build"
    build.mkdir()
    (build / "artifact.txt").write_text("ignored artifact\n", encoding="utf-8")

    status = git_status(repo)
    by_path = {item["path"]: item for item in status["files"]}

    assert by_path["tracked.txt"]["unstaged"] is True
    assert by_path["debug.log"]["ignored"] is True
    assert by_path["debug.log"]["status"] == "Ignored"
    assert by_path["debug.log"]["staged"] is False
    assert by_path["debug.log"]["unstaged"] is False
    assert by_path["debug.log"]["untracked"] is False
    assert any(item["ignored"] and item["path"].startswith("build") for item in status["files"])
    assert status["totals"]["changed"] == 1
    assert status["totals"]["untracked"] == 0


def test_git_diff_large_untracked_file_is_bounded(tmp_path):
    from api.workspace_git import DIFF_SIZE_LIMIT, git_diff, git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    large = repo / "large.txt"
    large.write_text("x" * (DIFF_SIZE_LIMIT + 1), encoding="utf-8")

    status = git_status(repo)
    by_path = {item["path"]: item for item in status["files"]}
    assert by_path["large.txt"]["untracked"] is True
    assert by_path["large.txt"]["additions"] == 0

    diff = git_diff(repo, "large.txt", "unstaged")
    assert diff["too_large"] is True
    assert diff["diff"] == ""


def test_git_branches_lists_local_remote_and_upstream(tmp_path):
    from api.workspace_git import git_branches

    remote = _init_bare_repo(tmp_path / "remote.git")
    origin = _init_repo(tmp_path / "origin")
    (origin / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(origin)
    _git(origin, "branch", "-M", "main")
    _git(origin, "remote", "add", "origin", str(remote))
    _git(origin, "push", "-u", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(remote), str(clone))
    branches = git_branches(clone)
    assert branches["current"] == "main"
    assert branches["detached"] is False
    assert any(item["name"] == "main" and item["upstream"] == "origin/main" for item in branches["local"])
    main = next(item for item in branches["local"] if item["name"] == "main")
    assert "updated_relative" in main and "author" in main and "subject" in main
    assert any(item["name"] == "origin/main" for item in branches["remote"])
    assert not any(item["name"] == "origin" for item in branches["remote"])


def test_git_status_ignores_repo_local_fsmonitor_command(tmp_path):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("executable fsmonitor helper setup is POSIX-only")

    from api.workspace_git import git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    marker = tmp_path / "fsmonitor-ran"
    helper = tmp_path / "fsmonitor_helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('fsmonitor executed', encoding='utf-8')\n"
        "print('')\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", f"{sys.executable} {helper} {marker}")

    status = git_status(repo)

    assert status["is_git"] is True
    assert not marker.exists()


def test_git_status_skips_repo_local_clean_filter_without_destructive_mode(tmp_path, monkeypatch):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("scripted clean filter setup is POSIX-only")

    from api.workspace_git import WORKSPACE_GIT_DESTRUCTIVE_ENV, git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text("*.txt filter=demo\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    marker = tmp_path / "git-status-clean-filter-ran"
    helper = tmp_path / "git_status_clean_filter_helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('clean filter ran', encoding='utf-8')\n"
        "print(sys.stdin.read(), end='')\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    _git(repo, "config", "filter.demo.clean", f'"{sys.executable}" "{helper}" "{marker}"')
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")

    monkeypatch.delenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, raising=False)
    status = git_status(repo)

    assert status["is_git"] is True
    assert status["totals"]["unstaged"] == 1
    assert not marker.exists()


def test_destructive_filter_overrides_include_worktree_scope(tmp_path):
    import os

    from api.workspace_git import _destructive_filter_overrides

    repo = _init_repo(tmp_path / "repo")
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", "filter.demo.clean", "cat")
    _git(repo, "config", "--worktree", "filter.demo.required", "true")

    overrides = dict(_destructive_filter_overrides(repo, os.environ.copy()))

    assert overrides["filter.demo.clean"] == "cat"
    assert overrides["filter.demo.smudge"] == "cat"
    assert overrides["filter.demo.process"] == ""
    assert overrides["filter.demo.required"] == "false"


def test_destructive_filter_overrides_include_included_scope(tmp_path):
    import os

    from api.workspace_git import _destructive_filter_overrides

    repo = _init_repo(tmp_path / "repo")
    included = tmp_path / "included-filter.cfg"
    included.write_text(
        "[filter \"demo\"]\n"
        "\tclean = cat\n"
        "\trequired = true\n",
        encoding="utf-8",
    )
    _git(repo, "config", "include.path", str(included))

    overrides = dict(_destructive_filter_overrides(repo, os.environ.copy()))

    assert overrides["filter.demo.clean"] == "cat"
    assert overrides["filter.demo.smudge"] == "cat"
    assert overrides["filter.demo.process"] == ""
    assert overrides["filter.demo.required"] == "false"


def test_destructive_merge_driver_overrides_include_local_worktree_and_included_scope(tmp_path):
    import os

    from api.workspace_git import _destructive_merge_driver_overrides

    repo = _init_repo(tmp_path / "repo")
    included = tmp_path / "included-merge.cfg"
    included.write_text(
        "[merge \"included\"]\n"
        "\tdriver = cat\n",
        encoding="utf-8",
    )
    _git(repo, "config", "include.path", str(included))
    _git(repo, "config", "merge.local.driver", "cat")
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", "merge.worktree.driver", "cat")

    overrides = dict(_destructive_merge_driver_overrides(repo, os.environ.copy()))

    trusted_driver = 'git merge-file "%A" "%O" "%B"'
    assert overrides["merge.included.driver"] == trusted_driver
    assert overrides["merge.local.driver"] == trusted_driver
    assert overrides["merge.worktree.driver"] == trusted_driver


def test_run_git_force_destructive_hardening_applies_hook_redirect_without_flag(monkeypatch, tmp_path):
    from api import workspace_git

    captured = {}
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    def fake_subprocess_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(workspace_git, "workspace_git_destructive_enabled", lambda: False)
    monkeypatch.setattr(workspace_git.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(workspace_git.tempfile, "mkdtemp", lambda prefix: str(hooks_dir))

    workspace_git._run_git(tmp_path, ["fetch"], force_destructive_hardening=True)

    assert any(str(arg).startswith("core.hooksPath=") for arg in captured["argv"])
    assert "core.alternateRefsCommand=" in captured["argv"]


def test_selected_temp_index_env_blocks_repo_local_filters_when_destructive_mode_enabled(monkeypatch, tmp_path):
    from api import workspace_git

    ctx = workspace_git.GitContext(tmp_path, tmp_path, "")

    monkeypatch.setattr(workspace_git, "workspace_git_destructive_enabled", lambda: True)
    monkeypatch.setattr(workspace_git, "_has_repo_local_filters", lambda cwd, env: True)

    with pytest.raises(workspace_git.GitWorkspaceError) as exc:
        workspace_git._selected_temp_index_env(ctx, ["tracked.txt"])

    assert exc.value.code == "filtered_path"


def test_git_env_scrub_removes_redirecting_vars_and_preserves_temp_index(monkeypatch):
    from api.workspace_git import _clean_git_env

    monkeypatch.setenv("GIT_DIR", "/tmp/evil-git-dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/evil-work-tree")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/evil-config")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/tmp/evil-system-config")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "ssh -i /tmp/evil-key")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.sshCommand=ssh -i /tmp/evil-key'")
    monkeypatch.setenv("GIT_ASKPASS", "/tmp/evil-askpass")
    monkeypatch.setenv("SSH_ASKPASS", "/tmp/evil-ssh-askpass")
    monkeypatch.setenv("GIT_SSH", "/tmp/evil-ssh")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /tmp/evil-key")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")

    env = _clean_git_env({"GIT_INDEX_FILE": "/tmp/hermes-index"})

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_CONFIG_GLOBAL" not in env
    assert "GIT_CONFIG_SYSTEM" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert "GIT_CONFIG_KEY_0" not in env
    assert "GIT_CONFIG_VALUE_0" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert "GIT_ASKPASS" not in env
    assert "SSH_ASKPASS" not in env
    assert "GIT_SSH" not in env
    assert "GIT_SSH_COMMAND" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_INDEX_FILE"] == "/tmp/hermes-index"


def test_git_error_classifier_identifies_non_fast_forward_push():
    from api.workspace_git import _classify_git_error

    assert _classify_git_error("Updates were rejected", ["push"]) == "non_fast_forward"
    assert _classify_git_error("non-fast-forward", ["push"]) == "non_fast_forward"
    assert _classify_git_error("fetch first", ["push"]) == "non_fast_forward"


def test_destructive_workspace_git_flag_defaults_off_and_accepts_truthy(monkeypatch):
    from api.workspace_git import WORKSPACE_GIT_DESTRUCTIVE_ENV, workspace_git_destructive_enabled

    monkeypatch.delenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, raising=False)
    assert workspace_git_destructive_enabled() is False

    monkeypatch.setenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, "1")
    assert workspace_git_destructive_enabled() is True

    monkeypatch.setenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, "true")
    assert workspace_git_destructive_enabled() is True


def test_git_active_stream_lock_detection(monkeypatch):
    from api import routes
    from api.config import STREAMS, STREAMS_LOCK

    session = types.SimpleNamespace(active_stream_id="stream-git-lock-test")
    with STREAMS_LOCK:
        STREAMS[session.active_stream_id] = object()
    try:
        assert routes._git_locked_by_active_stream(session) is True
    finally:
        with STREAMS_LOCK:
            STREAMS.pop(session.active_stream_id, None)

    assert routes._git_locked_by_active_stream(session) is False


def test_git_hardened_config_blocks_submodule_recursion():
    from api.workspace_git import _GIT_HARDENED_CONFIG

    config = dict(_GIT_HARDENED_CONFIG)
    assert config.get("submodule.recurse") == "false"
    assert config.get("fetch.recurseSubmodules") == "false"


def test_git_status_blocks_injection_via_filter_name_with_equals(tmp_path, monkeypatch):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("scripted filter setup is POSIX-only")

    from api.workspace_git import WORKSPACE_GIT_DESTRUCTIVE_ENV, git_status

    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text("*.txt filter=evil=core.sshCommand\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _commit_all(repo)
    marker = tmp_path / "injected-command-ran"
    helper = tmp_path / "injected_helper.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"pathlib.Path('{marker}').write_text('injected', encoding='utf-8')\n"
        "print(sys.stdin.read(), end='')\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    _git(repo, "config", "filter.evil=core.sshCommand.clean", f'"{sys.executable}" "{helper}"')
    _git(repo, "config", "core.sshCommand", f'"{sys.executable}" "{helper}"')
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")

    monkeypatch.delenv(WORKSPACE_GIT_DESTRUCTIVE_ENV, raising=False)
    status = git_status(repo)

    assert status["is_git"] is True
    assert not marker.exists()


def test_run_git_passes_windows_hide_flags(monkeypatch, tmp_path):
    """_run_git must pass creationflags=windows_hide_flags() so git child
    processes don't accumulate visible console windows on Windows (#5692).
    windows_hide_flags() is 0 on non-Windows, so this is a safe no-op there;
    the test asserts the kwarg is wired regardless of platform."""
    import api.workspace_git as wg
    from api.subprocess_utils import windows_hide_flags

    repo = _init_repo(tmp_path / "repo")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _commit_all(repo)

    captured = {}
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        captured["creationflags"] = kwargs.get("creationflags", "MISSING")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(wg.subprocess, "run", fake_run)
    wg._run_git(repo, ["rev-parse", "HEAD"])

    assert captured.get("creationflags") == windows_hide_flags(), (
        "_run_git must pass creationflags=windows_hide_flags() to subprocess.run"
    )


def test_config_names_for_scope_passes_windows_hide_flags(monkeypatch, tmp_path):
    """_config_names_for_scope must also pass creationflags=windows_hide_flags()
    (#5692) — the git-config probe spawns a console window on Windows too."""
    import re as _re
    import api.workspace_git as wg
    from api.subprocess_utils import windows_hide_flags

    repo = _init_repo(tmp_path / "repo")

    captured = {}
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        captured["creationflags"] = kwargs.get("creationflags", "MISSING")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(wg.subprocess, "run", fake_run)
    wg._config_names_for_scope(
        "--local",
        repo,
        {},
        "filter\\..*",
        _re.compile(r"^filter\."),
        ignore_unsupported=True,
    )

    assert captured.get("creationflags") == windows_hide_flags(), (
        "_config_names_for_scope must pass creationflags=windows_hide_flags()"
    )
