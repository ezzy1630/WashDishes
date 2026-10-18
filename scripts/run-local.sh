#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'Run scripts/setup-local.sh first.' >&2
  exit 1
fi
export WASHDISHES_DATA_DIR="${WASHDISHES_DATA_DIR:-$PWD/.data}"
exec .venv/bin/python -m wash_dishes serve "$@"
