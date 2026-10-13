"""./scripts/test.sh runs the suite in a throwaway container (ADR 0009).

On the host it builds the dev image and runs itself in a fresh container; inside
the container (GFIT_TEST_IN_CONTAINER=1) it picks how to run pytest. Both halves
are driven from the outside, with a fake ``docker`` or a fake Python that
records how it was called.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Records each invocation, one argument per line, then a line "--".
RECORDING = '#!/usr/bin/env bash\nfor a in "$@"; do printf "%s\\n" "$a"; done >> "$PROOF_FILE"\necho -- >> "$PROOF_FILE"\n'


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _repo_with_runner(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "test.sh", repo / "scripts" / "test.sh")
    return repo


def _calls(proof: Path) -> list[list[str]]:
    calls, current = [], []
    for line in (proof.read_text(encoding="utf-8").splitlines() if proof.exists() else []):
        if line == "--":
            calls.append(current)
            current = []
        else:
            current.append(line)
    return calls


def _option_values(argv, option):
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == option]


# ── on the host ─────────────────────────────────────────────────────────────

def _run_on_host(tmp_path, *args):
    """test.sh as a developer runs it, with a fake docker that records its calls."""
    repo = _repo_with_runner(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "docker", RECORDING)
    proof = tmp_path / "docker-calls.txt"
    env = os.environ.copy()
    env.pop("GFIT_TEST_IN_CONTAINER", None)
    env.update(PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", PROOF_FILE=str(proof))
    result = subprocess.run(["bash", "scripts/test.sh", *args], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return repo, _calls(proof)


def test_on_the_host_the_runner_builds_the_dev_image_and_runs_itself_in_it(tmp_path):
    _repo, (build, run) = _run_on_host(tmp_path, "--changed", "-x")
    assert build[:4] == ["build", "--target", "dev", "-t"]
    image = build[4]
    assert run[:2] == ["run", "--rm"]
    # The image, then the runner itself, with the developer's arguments unchanged.
    assert run[run.index(image):] == [image, "scripts/test.sh", "--changed", "-x"]
    assert "GFIT_TEST_IN_CONTAINER=1" in _option_values(run, "-e")


def test_the_test_container_mounts_only_the_checkout(tmp_path):
    repo, (_build, run) = _run_on_host(tmp_path)
    assert _option_values(run, "-v") == [f"{repo}:/src"]
    assert _option_values(run, "-w") == ["/src"]
    env = dict(e.split("=", 1) for e in _option_values(run, "-e"))
    # Hermes state lives inside the throwaway container, never on the host.
    for name in ("HOME", "HERMES_HOME", "HERMES_WEBUI_STATE_DIR"):
        assert env[name].startswith("/tmp/"), name


def test_browser_flag_uses_the_browser_image(tmp_path):
    _repo, (build, run) = _run_on_host(tmp_path, "--browser")
    assert build[:3] == ["build", "--target", "browser"]
    assert run[-1] == "--browser"


def test_the_runner_says_docker_is_needed_when_it_is_missing(tmp_path):
    repo = _repo_with_runner(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
    result = subprocess.run([shutil.which("bash"), "scripts/test.sh"], cwd=repo,
                            env={"PATH": str(bin_dir)},
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 2
    assert "Docker is required" in result.stderr


# ── inside the container ────────────────────────────────────────────────────

def _run_in_container(tmp_path, *args, **extra_env):
    repo = _repo_with_runner(tmp_path)
    _executable(repo / "fake-python", RECORDING)
    proof = repo / "proof.txt"
    env = os.environ.copy()
    for name in ("HERMES_WEBUI_TEST_PORT", "HERMES_WEBUI_TEST_STATE_DIR"):
        env.pop(name, None)
    env.update(GFIT_TEST_IN_CONTAINER="1", GFIT_TEST_PYTHON="./fake-python",
               PROOF_FILE=str(proof), **extra_env)
    result = subprocess.run(["bash", "scripts/test.sh", *args], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    calls = _calls(proof)
    return (calls[0] if calls else []), result.stderr


def test_without_arguments_the_runner_shards_the_whole_suite(tmp_path):
    argv, _stderr = _run_in_container(tmp_path)
    assert argv[0].endswith("/scripts/run_test_shards.py")
    assert argv[1:] == ["auto"]


def test_serial_flag_runs_one_pytest_process(tmp_path):
    argv, _stderr = _run_in_container(tmp_path, "--serial")
    assert argv == ["-m", "pytest", "tests/", "-v", "--timeout=60"]


def test_paths_run_one_pytest_process(tmp_path):
    argv, _stderr = _run_in_container(tmp_path, "tests/test_x.py", "-q")
    assert argv == ["-m", "pytest", "tests/test_x.py", "-q"]


def test_without_arguments_the_runner_runs_serially_when_state_is_pinned(tmp_path):
    argv, stderr = _run_in_container(tmp_path, HERMES_WEBUI_TEST_PORT="8789")
    assert argv == ["-m", "pytest", "tests/", "-v", "--timeout=60"]
    assert "running the suite serially" in stderr


def test_changed_flag_asks_the_selector_and_runs_nothing_when_empty(tmp_path):
    argv, stderr = _run_in_container(tmp_path, "--changed=main")
    assert argv[0].endswith("/scripts/changed_tests.py")
    assert argv[1:] == ["main"]
    assert "no test files touch the changes since main" in stderr


def test_browser_flag_in_the_container_runs_the_browser_smoke(tmp_path):
    argv, _stderr = _run_in_container(tmp_path, "--browser", "--headed")
    assert argv[0].endswith("/tests/browser_smoke.py")
    assert argv[1:] == ["--headed"]
