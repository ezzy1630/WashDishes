#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  scripts/setup-local.sh
fi
if curl -fsS http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
  open 'http://127.0.0.1:8787'
  exit 0
fi
scripts/run-local.sh --demo &
task_server_pid=$!
trap 'kill "$task_server_pid" 2>/dev/null || true' EXIT INT TERM
for task_attempt in {1..40}; do
  if ! kill -0 "$task_server_pid" 2>/dev/null; then
    wait "$task_server_pid"
    exit 1
  fi
  if curl -fsS http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
    open 'http://127.0.0.1:8787'
    wait "$task_server_pid"
    exit 0
  fi
  sleep 0.25
done
printf '%s\n' 'The dashboard did not start. Check the terminal output above.' >&2
exit 1
