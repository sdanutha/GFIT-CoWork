"""``./scripts/test.sh --parallel N`` runs N pytest-shard processes and combines them."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest

# Talks to the shared test server (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("test_server")

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "run_test_shards.py"

_spec = importlib.util.spec_from_file_location("run_test_shards", HELPER)
shards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shards)


def _clean_env():
    env = os.environ.copy()
    for name in shards.PINNED_ENV:
        env.pop(name, None)
    return env


def test_parse_summary_reads_quiet_and_verbose_lines():
    quiet = "....\n11 failed, 19611 passed, 279 skipped, 4 xfailed, 5 warnings, 45 subtests passed in 927.50s (0:15:27)\n"
    verbose = "...\n========== 1 failed, 3 passed, 1 error in 0.12s ==========\n"

    _text, counts = shards.parse_summary(quiet)
    assert counts == {"failed": 11, "passed": 19611, "skipped": 279, "xfailed": 4,
                      "warnings": 5, "subtests passed": 45}
    _text, counts = shards.parse_summary(verbose)
    assert counts == {"failed": 1, "passed": 3, "errors": 1}
    assert shards.parse_summary("no summary here\n") == ("", {})


def test_combine_sums_counts_across_shards():
    total = shards.combine([{"passed": 3, "failed": 1}, {"passed": 2, "skipped": 1}, {}])
    assert total == {"passed": 5, "failed": 1, "skipped": 1}
    assert shards.format_counts(total) == "1 failed, 5 passed, 1 skipped"


def test_parallel_run_combines_shards_and_fails_on_any_failure(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(textwrap.dedent("""\
        def test_a(): pass
        def test_b(): pass
        def test_c(): pass
        def test_broken(): assert False
        """), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(HELPER), "2", str(tmp_path), "-c", str(tmp_path / "pytest.ini"),
         "-q", "-p", "no:cacheprovider"],
        env=_clean_env(), capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Running 2 shards" in result.stdout
    assert "shard 1/2" in result.stdout and "shard 2/2" in result.stdout
    assert "FAILED " in result.stdout and "test_broken" in result.stdout
    assert "1 failed, 3 passed" in result.stdout.splitlines()[-1]


def test_parallel_run_passes_when_every_shard_passes_or_is_empty(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_one.py").write_text("def test_only(): pass\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(HELPER), "3", str(tmp_path), "-c", str(tmp_path / "pytest.ini"),
         "-q", "-p", "no:cacheprovider"],
        env=_clean_env(), capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1].startswith("1 passed in ")


def test_pinned_port_or_state_dir_is_refused():
    env = _clean_env()
    env["HERMES_WEBUI_TEST_PORT"] = "8789"

    result = subprocess.run([sys.executable, str(HELPER), "2"], env=env,
                            capture_output=True, text=True, timeout=30)

    assert result.returncode == 2
    assert "HERMES_WEBUI_TEST_PORT" in result.stderr


def test_runner_hands_parallel_runs_to_the_helper():
    runner = (ROOT / "scripts" / "test.sh").read_text(encoding="utf-8")
    assert '"$ROOT/scripts/run_test_shards.py"' in runner
    assert '"${1:-}" == "--parallel"' in runner


def test_auto_shard_count_is_cores_capped_at_eight(monkeypatch):
    monkeypatch.setattr(shards.os, "cpu_count", lambda: 14)
    assert shards.shard_count("auto") == 8
    monkeypatch.setattr(shards.os, "cpu_count", lambda: 4)
    assert shards.shard_count("auto") == 4
    monkeypatch.setattr(shards.os, "cpu_count", lambda: None)
    assert shards.shard_count("auto") == 1
    assert shards.shard_count("3") == 3
    assert shards.shard_count("0") is None
    assert shards.shard_count("x") is None
