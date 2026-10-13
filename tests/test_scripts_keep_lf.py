"""Every shell script checks out with LF line endings, on every platform.

The deploy kit and the test runner run inside Linux containers (ADR 0009). A
script checked out with CRLF on Windows (core.autocrlf=true) has the shebang
`#!/usr/bin/env bash\r`, which Linux cannot run.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _tracked_scripts() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    scripts = []
    for name in files:
        path = REPO / name
        try:
            with path.open("rb") as fh:
                head = fh.readline()
        except OSError:
            continue
        if head.startswith(b"#!") and (b"bash" in head or b"/sh" in head):
            scripts.append(name)
    return scripts


def test_the_kit_scripts_are_found():
    scripts = _tracked_scripts()
    assert "deploy/cowork" in scripts and "deploy/new-deployment" in scripts


@pytest.mark.parametrize("name", _tracked_scripts())
def test_shell_script_checks_out_with_lf(name):
    attr = subprocess.run(
        ["git", "check-attr", "eol", "--", name],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert attr.endswith("eol: lf"), attr
