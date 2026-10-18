#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
  printf '%s\n' 'This service installer is for the Jetson Linux host.' >&2
  exit 1
fi
task_repo="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ ! -x "$task_repo/.venv/bin/python" ]]; then
  printf '%s\n' 'Prepare the virtual environment on the Jetson first.' >&2
  exit 1
fi
export WASHDISHES_DATA_DIR="${WASHDISHES_DATA_DIR:-$HOME/.local/share/washdishes}"
mkdir -p "$HOME/.config/systemd/user"
task_unit="$HOME/.config/systemd/user/dish-watch.service"
# systemd specifiers are escaped; this installer never runs a remote shell.
task_repo_escaped="${task_repo//%/%%}"
task_data_escaped="${WASHDISHES_DATA_DIR//%/%%}"
cat > "$task_unit" <<EOF
[Unit]
Description=Dish watch local kitchen dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$task_repo_escaped
Environment="WASHDISHES_DATA_DIR=$task_data_escaped"
Environment=PYTHONUNBUFFERED=1
Environment="PATH=$task_repo_escaped/.tools/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart="$task_repo_escaped/.venv/bin/python" -m wash_dishes serve
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
EOF
systemd-analyze --user verify "$task_unit"
systemctl --user daemon-reload
systemctl --user enable --now dish-watch.service
systemctl --user is-active --quiet dish-watch.service
task_ready=false
for task_attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
    task_ready=true
    break
  fi
  sleep 1
done
if [[ "$task_ready" != true ]]; then
  printf '%s\n' 'Dish watch did not become healthy. Check: systemctl --user status dish-watch' >&2
  exit 1
fi
printf '%s\n' 'Service installed. Check: systemctl --user status dish-watch'
printf '%s\n' 'For startup without a login, enable user lingering using loginctl on this host.'
