#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    task_python="$(command -v python3.12)"
  else
    task_python="$(command -v python3)"
  fi
  "$task_python" -m venv .venv
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade
fi
.venv/bin/python -m pip install -e '.[test]' -c requirements-core.lock
printf '%s\n' 'Ready. Run scripts/run-local.sh --demo to try the isolated demo.'
