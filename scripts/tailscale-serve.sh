#!/usr/bin/env bash
set -euo pipefail
# Run on the Jetson after it is enrolled in your tailnet and live mode is running.
if ! command -v tailscale >/dev/null 2>&1; then
  printf '%s\n' 'Install Tailscale using its official Linux instructions, then sign in.' >&2
  exit 1
fi
if ! curl -fsS http://127.0.0.1:8787/api/health >/dev/null; then
  printf '%s\n' 'Start Dish watch on 127.0.0.1:8787 first.' >&2
  exit 1
fi
task_mode="$(curl -fsS http://127.0.0.1:8787/api/health)"
if [[ "$task_mode" == *'"demo"'* ]]; then
  printf '%s\n' 'Start live mode before sharing the Jetson dashboard.' >&2
  exit 1
fi
tailscale serve --bg http://127.0.0.1:8787
tailscale serve status
