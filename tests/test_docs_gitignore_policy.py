"""Regression tests for docs/ ignore policy."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git_check_ignore(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_new_top_level_markdown_docs_are_trackable():
    """New docs/*.md files should be visible to Git, not silently ignored."""
    assert _git_check_ignore("docs/example-new-guide.md").returncode == 1


def test_root_agents_entrypoint_is_trackable():
    """AGENTS.md is the shared repo entrypoint."""
    assert _git_check_ignore("AGENTS.md").returncode == 1


def test_claude_folder_is_tracked_except_personal_settings_and_locks():
    """.claude/ holds shared settings and rules; personal settings and runtime locks stay out."""
    assert _git_check_ignore(".claude/settings.json").returncode == 1
    assert _git_check_ignore(".claude/rules/issue-tracker.md").returncode == 1
    assert _git_check_ignore(".claude/settings.local.json").returncode == 0
    assert _git_check_ignore(".claude/scheduled_tasks.lock").returncode == 0


def test_docs_scratch_files_remain_ignored():
    """The broad docs/* ignore rule should still keep arbitrary scratch files out."""
    assert _git_check_ignore("docs/local-scratch.tmp").returncode == 0
