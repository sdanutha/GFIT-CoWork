import sys
from unittest.mock import MagicMock, patch

import api.version as version_mod


def test_run_git_uses_which_result_when_available(tmp_path):
    with patch.object(version_mod.shutil, 'which', return_value='C:/Tools/git.exe'), \
         patch.object(version_mod.subprocess, 'run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='v0.51.999\n', stderr='')

        out, ok = version_mod._run_git(['describe', '--tags'], tmp_path)

    assert ok is True
    assert out == 'v0.51.999'
    assert mock_run.call_args.args[0][0] == 'C:/Tools/git.exe'


def test_run_git_falls_back_to_usr_bin_git_on_darwin(tmp_path):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == '/usr/bin/git'
        return MagicMock(returncode=0, stdout='v0.51.999\n', stderr='')

    with patch.object(version_mod.shutil, 'which', return_value=None), \
         patch.object(sys, 'platform', 'darwin'), \
         patch.object(version_mod.os.path, 'exists', return_value=True), \
         patch.object(version_mod.subprocess, 'run', side_effect=fake_run):
        out, ok = version_mod._run_git(['describe', '--tags'], tmp_path)

    assert ok is True
    assert out == 'v0.51.999'


def test_run_git_returns_not_found_when_no_executable(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == '/usr/bin/git':
            raise AssertionError('non-macOS path miss must not use /usr/bin/git')
        raise FileNotFoundError

    with patch.object(version_mod.shutil, 'which', return_value=None), \
         patch.object(sys, 'platform', 'linux'), \
         patch.object(version_mod.os.path, 'exists', return_value=True), \
         patch.object(version_mod.subprocess, 'run', side_effect=fake_run):
        out, ok = version_mod._run_git(['status'], tmp_path)

    assert ok is False
    assert out == 'git executable not found'
    assert '/usr/bin/git' not in calls


def test_run_git_returns_not_found_when_usr_bin_git_absent_on_darwin(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        raise FileNotFoundError

    with patch.object(version_mod.shutil, 'which', return_value=None), \
         patch.object(sys, 'platform', 'darwin'), \
         patch.object(version_mod.os.path, 'exists', return_value=False), \
         patch.object(version_mod.subprocess, 'run', side_effect=fake_run):
        out, ok = version_mod._run_git(['status'], tmp_path)

    assert ok is False
    assert out == 'git executable not found'
    assert '/usr/bin/git' not in calls


def test_detect_webui_version_recovers_via_launchd_fallback(tmp_path):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == '/usr/bin/git'
        if cmd[1:] == ['describe', '--tags', '--always']:
            return MagicMock(returncode=0, stdout='v0.51.999\n', stderr='')
        if cmd[1:] == ['diff-index', '--quiet', 'HEAD', '--']:
            return MagicMock(returncode=0, stdout='', stderr='')
        raise AssertionError(f'unexpected git args: {cmd[1:]!r}')

    with patch.object(version_mod.shutil, 'which', return_value=None), \
         patch.object(sys, 'platform', 'darwin'), \
         patch.object(version_mod.os.path, 'exists', return_value=True), \
         patch.object(version_mod, 'REPO_ROOT', tmp_path), \
         patch.object(version_mod.subprocess, 'run', side_effect=fake_run):
        version = version_mod._detect_webui_version()

    assert version == 'v0.51.999'
