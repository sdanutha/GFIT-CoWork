#!/usr/bin/env bash
# Run GFIT-CoWork's test suite in a throwaway Linux container (ADR 0009).
#
# On the host, this builds the image's `dev` target (or `browser` for
# --browser) and runs itself inside a fresh container that mounts only the
# checkout: no Deployment, no Hermes home, no state from the host. Inside the
# container it runs pytest with the image's Python, which has the test
# dependencies and Hermes Agent at the pinned version.
#
#   ./scripts/test.sh                  the whole suite, sharded
#   ./scripts/test.sh --changed[=REF]  the test files a change touches since REF (dev)
#   ./scripts/test.sh --parallel[=N]   N pytest-shard processes
#   ./scripts/test.sh --serial         one pytest process
#   ./scripts/test.sh --browser        the headless browser smoke
#   ./scripts/test.sh <pytest args>    e.g. tests/test_x.py -q
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${GFIT_TEST_IN_CONTAINER:-}" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to run the tests: they run in a Linux container (ADR 0009)." >&2
    echo "Install Docker (Docker Desktop on Windows and macOS) and rerun ./scripts/test.sh." >&2
    exit 2
  fi

  target=dev
  run_opts=()
  if [[ "${1:-}" == "--browser" ]]; then
    target=browser
    run_opts+=(--shm-size=1g)
  fi
  image="gfit-cowork-test:$target"

  src="$ROOT"
  if command -v cygpath >/dev/null 2>&1; then
    # Git Bash: give docker a Windows path, and stop MSYS from rewriting
    # container paths like /src into C:/Program Files/Git/src.
    src="$(cygpath -m "$ROOT")"
    export MSYS_NO_PATHCONV=1
  fi

  # Files the tests write in the checkout (.pytest_cache) belong to the
  # developer on Linux. Docker Desktop maps ownership itself.
  if [[ "$(uname -s)" == Linux ]]; then
    run_opts+=(--user "$(id -u):$(id -g)")
  else
    run_opts+=(--user hermeswebui)
  fi
  [[ -t 1 ]] && run_opts+=(-t)
  # The LDAP tests can reach a Development Deployment's mock Directory through
  # the host (dev/mock-ldap/README.md).
  run_opts+=(--add-host host.docker.internal:host-gateway)
  for name in GFIT_MOCK_LDAP_URL GFIT_MOCK_LDAP_PLAIN_URL GFIT_MOCK_LDAP_CA_CERT; do
    [[ -n "${!name:-}" ]] && run_opts+=(-e "$name=${!name}")
  done

  echo "Building the $target test image ($image)..." >&2
  docker build --target "$target" -t "$image" "$src" >&2

  # --no-healthcheck: the image's HEALTHCHECK probes a server this container
  # does not run.
  exec docker run --rm --no-healthcheck "${run_opts[@]}" \
    -v "$src:/src" -w /src \
    -e GFIT_TEST_IN_CONTAINER=1 \
    -e HOME=/tmp/test-home \
    -e HERMES_HOME=/tmp/hermes-home \
    -e HERMES_WEBUI_STATE_DIR=/tmp/webui-state \
    -e HERMES_WEBUI_AGENT_DIR=/opt/gfit-dev/hermes-agent \
    --entrypoint bash \
    "$image" scripts/test.sh "$@"
fi

# Inside the container. The image's Python has every test dependency.
PYTHON_BIN="${GFIT_TEST_PYTHON:-/opt/gfit-dev/test-venv/bin/python}"

# --browser: the headless browser smoke (tests/browser_smoke.py).
if [[ "${1:-}" == "--browser" ]]; then
  shift
  exec "$PYTHON_BIN" "$ROOT/tests/browser_smoke.py" "$@"
fi

# --serial: one pytest process.
if [[ "${1:-}" == "--serial" ]]; then
  shift
  if [[ $# -eq 0 ]]; then
    set -- tests/ -v --timeout=60
  fi
  exec "$PYTHON_BIN" -m pytest "$@"
fi

# --changed[=REF]: only the test files a change touches since merge-base HEAD REF
# (default dev); see scripts/changed_tests.py. More than 30 files run sharded.
if [[ "${1:-}" == "--changed" || "${1:-}" == --changed=* ]]; then
  ref="dev"
  if [[ "$1" == --changed=* ]]; then
    ref="${1#--changed=}"
  fi
  shift
  selected="$("$PYTHON_BIN" "$ROOT/scripts/changed_tests.py" "$ref")" || exit $?
  if [[ -z "$selected" ]]; then
    echo "--changed: no test files touch the changes since $ref." >&2
    exit 0
  fi
  mapfile -t files <<< "$selected"
  echo "--changed: ${#files[@]} test path(s) since $ref." >&2
  if [[ "${files[*]}" == "tests/" || ${#files[@]} -gt 30 ]]; then
    exec "$PYTHON_BIN" "$ROOT/scripts/run_test_shards.py" auto "${files[@]}" -q --timeout=60 "$@"
  fi
  exec "$PYTHON_BIN" -m pytest "${files[@]}" -q --timeout=60 "$@"
fi

# --parallel N: run N pytest-shard processes at once and combine their results.
if [[ "${1:-}" == --parallel=* ]]; then
  shards="${1#--parallel=}"
  shift
  exec "$PYTHON_BIN" "$ROOT/scripts/run_test_shards.py" "$shards" "$@"
fi
if [[ "${1:-}" == "--parallel" ]]; then
  shift
  exec "$PYTHON_BIN" "$ROOT/scripts/run_test_shards.py" "$@"
fi

# No arguments: the whole suite, sharded across min(cores, 8, memory / 3 GB) processes.
# Sharding needs a free port and state dir per shard, so pinned ones run serially.
if [[ $# -eq 0 ]]; then
  if [[ -n "${HERMES_WEBUI_TEST_PORT:-}" || -n "${HERMES_WEBUI_TEST_STATE_DIR:-}" ]]; then
    echo "HERMES_WEBUI_TEST_PORT or HERMES_WEBUI_TEST_STATE_DIR is pinned; running the suite serially." >&2
    set -- tests/ -v --timeout=60
  else
    exec "$PYTHON_BIN" "$ROOT/scripts/run_test_shards.py" auto
  fi
fi

exec "$PYTHON_BIN" -m pytest "$@"
