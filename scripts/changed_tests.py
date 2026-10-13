"""Pick the test files a change touches, for ``./scripts/test.sh --changed[=REF]``.

Changed files are everything that differs from ``git merge-base HEAD REF``
(default ``dev``), including uncommitted and untracked files. A test file is
selected when it changed itself, or when its text names a changed file: its
repo path, its file name, or (for Python) its dotted module. A change to the
shared test setup selects the whole suite.

Prints one test path per line, or ``tests/`` for the whole suite, or nothing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REF = "dev"
# Shared by every test: a change here can break any of them.
WHOLE_SUITE = {
    "tests/conftest.py",
    "tests/_pytest_port.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
}


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_files(ref: str) -> list[str]:
    base = _git("merge-base", "HEAD", ref)[0]
    return sorted(set(_git("diff", "--name-only", base))
                  | set(_git("ls-files", "--others", "--exclude-standard")))


def needles(path: str) -> list[str]:
    """Strings whose presence in a test file means it refers to ``path``."""
    p = PurePosixPath(path)
    found = [path, p.name]
    if p.suffix == ".py":
        found.append(".".join(p.with_suffix("").parts))
    return found


def select(changed: list[str], test_texts: dict[str, str]) -> list[str]:
    """Test paths to run for ``changed``; ``["tests/"]`` for the whole suite."""
    if any(path in WHOLE_SUITE for path in changed):
        return ["tests/"]
    selected = {path for path in changed if path in test_texts}
    wanted = [n for path in changed if path not in test_texts for n in needles(path)]
    for test_path, text in test_texts.items():
        if any(n in text for n in wanted):
            selected.add(test_path)
    return sorted(selected)


def main(argv: list[str]) -> int:
    ref = argv[0] if argv else DEFAULT_REF
    try:
        changed = changed_files(ref)
    except subprocess.CalledProcessError as exc:
        print(f"--changed: cannot diff against {ref!r}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    test_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((ROOT / "tests").glob("test_*.py"))
    }
    for path in select(changed, test_texts):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
