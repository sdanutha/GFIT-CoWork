"""The version probe reports a missing git cleanly, without starting anything."""
from unittest.mock import patch

import api.version as version_mod


def test_run_git_returns_not_found_when_no_git_is_on_path(tmp_path):
    with patch.object(version_mod.shutil, 'which', return_value=None):
        out, ok = version_mod._run_git(['status'], tmp_path)

    assert ok is False
    assert out == 'git executable not found'


def test_run_git_runs_the_git_on_path(tmp_path):
    out, ok = version_mod._run_git(['--version'], tmp_path)

    assert ok is True
    assert out.startswith('git version')
