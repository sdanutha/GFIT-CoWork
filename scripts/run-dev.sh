#!/bin/sh
# Run GFIT-CoWork in the foreground against a Hermes Agent install that uses the
# managed runtime (hermes_bootstrap + a Hermes-owned Python). bootstrap.py and
# start.sh do not detect that layout yet: its legacy venv/bin/python re-execs
# into the managed interpreter with -I, which drops the WebUI's PYTHONPATH.
#
# Usage: scripts/run-dev.sh            (port 8787, real ~/.hermes)
#        HERMES_HOME=/tmp/x HERMES_WEBUI_STATE_DIR=/tmp/y HERMES_WEBUI_PORT=8789 scripts/run-dev.sh
# Stop with Ctrl-C.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)

if ! command -v hermes >/dev/null 2>&1; then
  echo "run-dev: 'hermes' is not on PATH; install Hermes Agent first" >&2
  exit 1
fi

# hermes prints its runtime as a JSON argv: [python, "-I", "-c", code].
RUNTIME=$(hermes --print-runtime-command)
PY=$(printf '%s' "$RUNTIME" | /usr/bin/env python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
AGENT=$(printf '%s' "$RUNTIME" | /usr/bin/env python3 -c '
import json, re, sys
code = json.load(sys.stdin)[3]
print(re.search(r"sys\.path\.insert\(0, (\x27[^\x27]+\x27)\)", code).group(1)[1:-1])')

export HERMES_WEBUI_AGENT_DIR="$AGENT"
export HERMES_WEBUI_PYTHON="$PY"
cd "$ROOT"

exec "$PY" -I -c "
import os, sys, runpy
sys.path.insert(0, '$AGENT')
os.environ['HERMES_HOME'] = os.environ.get('HERMES_HOME') or str(__import__('hermes_constants').get_default_hermes_root())
import hermes_bootstrap
sys.path.insert(0, '$ROOT')
sys.argv = ['server.py']
runpy.run_path('$ROOT/server.py', run_name='__main__')
"
