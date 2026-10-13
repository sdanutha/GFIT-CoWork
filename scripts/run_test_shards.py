"""Run the pytest suite as N concurrent pytest-shard processes and combine the results.

Called by ``./scripts/test.sh --parallel N [pytest args]`` with the test venv's
interpreter. Each shard is a separate pytest process; ``tests/conftest.py``
gives every process its own free port and state dir when
``HERMES_WEBUI_TEST_PORT`` / ``HERMES_WEBUI_TEST_STATE_DIR`` are not pinned,
so pinning either one is refused here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARGS = ["tests/", "-q", "--timeout=60"]
PINNED_ENV = ("HERMES_WEBUI_TEST_PORT", "HERMES_WEBUI_TEST_STATE_DIR")
# pytest exit codes: 0 ok, 1 tests failed, 5 nothing collected (a shard can be empty).
OK_EXIT_CODES = {0, 5}
_COUNT = re.compile(r"(\d+) ([a-z][a-z ]*?)(?=,| in |$)")
_SUMMARY = re.compile(r"^=*\s*(\d+ [a-z].* in [\d.]+s.*?)\s*=*$")


def parse_summary(output: str) -> tuple[str, dict[str, int]]:
    """Return pytest's final summary line and its counts ("passed" -> 12, ...)."""
    for line in reversed(output.splitlines()):
        match = _SUMMARY.match(line.strip())
        if match:
            text = match.group(1)
            counts: dict[str, int] = {}
            for number, label in _COUNT.findall(text.split(" in ")[0]):
                label = {"error": "errors", "warning": "warnings"}.get(label, label)
                counts[label] = counts.get(label, 0) + int(number)
            return text, counts
    return "", {}


def failed_ids(output: str) -> list[str]:
    """Return the FAILED/ERROR lines from pytest's short test summary."""
    return [line for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))]


def combine(counts: list[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for one in counts:
        for label, number in one.items():
            total[label] = total.get(label, 0) + number
    return total


def format_counts(total: dict[str, int]) -> str:
    order = ["failed", "errors", "passed", "skipped", "deselected", "xfailed", "xpassed", "warnings"]
    labels = [label for label in order if label in total] + sorted(set(total) - set(order))
    return ", ".join(f"{total[label]} {label}" for label in labels) or "no tests ran"


def shard_count(text: str) -> int | None:
    """``auto`` -> min(cores, 8): on a 14-core Windows box 12 shards were no faster than 8."""
    if text == "auto":
        return min(os.cpu_count() or 1, 8)
    if text.isdigit() and int(text) >= 1:
        return int(text)
    return None


def main(argv: list[str]) -> int:
    shards = shard_count(argv[0]) if argv else None
    if shards is None:
        print("usage: ./scripts/test.sh --parallel N [pytest args]", file=sys.stderr)
        return 2
    pytest_args = argv[1:] or DEFAULT_ARGS
    pinned = [name for name in PINNED_ENV if os.environ.get(name)]
    if shards > 1 and pinned:
        print(f"--parallel needs a free port and state dir per shard; unset {', '.join(pinned)}.",
              file=sys.stderr)
        return 2

    log_dir = Path(tempfile.mkdtemp(prefix="gfit-test-shards-"))
    started = time.monotonic()
    running = []
    for shard in range(shards):
        log_path = log_dir / f"shard-{shard}.log"
        log = log_path.open("w", encoding="utf-8")
        command = [sys.executable, "-m", "pytest", *pytest_args, "-rfE",
                   f"--shard-id={shard}", f"--num-shards={shards}"]
        proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        running.append((shard, proc, log, log_path))
    print(f"Running {shards} shards; logs in {log_dir}", flush=True)

    failed = False
    all_counts: list[dict[str, int]] = []
    all_failed: list[str] = []
    try:
        for shard, proc, log, log_path in running:
            code = proc.wait()
            log.close()
            output = log_path.read_text(encoding="utf-8", errors="replace")
            summary, counts = parse_summary(output)
            all_counts.append(counts)
            all_failed.extend(failed_ids(output))
            elapsed = time.monotonic() - started
            print(f"shard {shard + 1}/{shards} exit {code} at {elapsed:.0f}s: "
                  f"{summary or 'no summary line'}", flush=True)
            if code not in OK_EXIT_CODES:
                failed = True
                if not summary:
                    print("\n".join(output.splitlines()[-20:]), file=sys.stderr)
    except KeyboardInterrupt:
        for _shard, proc, _log, _path in running:
            proc.terminate()
        raise

    elapsed = time.monotonic() - started
    print()
    for line in all_failed:
        print(line)
    print(f"{format_counts(combine(all_counts))} in {elapsed:.1f}s ({shards} shards)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
