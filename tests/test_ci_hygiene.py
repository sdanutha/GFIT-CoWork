"""Small hygiene regression checks for the test runner, packaging, and console noise."""

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
import pytest

# Talks to the shared test server (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("test_server")

ROOT = Path(__file__).resolve().parents[1]

def _make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _bash():
    """Git Bash on Windows; a bare ``bash`` there can resolve to the WSL launcher."""
    if os.name == "nt":
        git_bash = Path(os.environ.get("HERMES_GIT_BASH_PATH") or r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            return str(git_bash)
    return "bash"


def test_pytest_integration_marker_is_registered():
    config = ROOT / "pyproject.toml"
    text = config.read_text(encoding="utf-8")

    assert "[tool.pytest.ini_options]" in text

    assert "markers" in text
    assert "integration:" in text


def test_packaging_targets_python_314_only():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.14"' in pyproject
    assert 'target-version = "py314"' in pyproject
    assert "Programming Language :: Python :: 3.11" not in pyproject


def test_live_model_success_log_is_debug_not_default_console_log():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "console.debug('[hermes] Live models loaded" in ui
    assert "console.log('[hermes] Live models loaded" not in ui


