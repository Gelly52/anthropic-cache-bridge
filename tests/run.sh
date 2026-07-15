#!/bin/sh
set -eu
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/acb-test-pycache" \
    python3 -m unittest discover -s "$ROOT_DIR/tests" -p 'test_*.py' -v
sh -n "$ROOT_DIR/install.sh"
sh -n "$ROOT_DIR/uninstall.sh"
sh -n "$ROOT_DIR/bin/acbctl"
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/acb-test-pycache" \
    python3 -m py_compile "$ROOT_DIR/bin/anthropic-cache-bridge.py"
echo "All checks passed"
