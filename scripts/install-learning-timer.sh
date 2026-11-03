#!/usr/bin/env bash
# Explicit opt-in replacement for the probe trainer. Does not stop the camera.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Install on the Jetson only.' >&2; exit 1; }
repo="$(cd -- "$(dirname -- "$0")/.." && pwd)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
cat > "$units/dish-watch-learning.service" <<EOF
[Unit]
Description=Dish Watch: independent human truth learning
[Service]
Type=oneshot
WorkingDirectory=${repo//%/%%}
Environment="WASHDISHES_DATA_DIR=${WASHDISHES_DATA_DIR:-$HOME/.local/share/washdishes/live}"
Nice=10
UMask=0077
NoNewPrivileges=true
ExecStart="$repo/.venv/bin/python" -m wash_dishes learn-nightly
TimeoutStartSec=6h
EOF
cat > "$units/dish-watch-learning.timer" <<'EOF'
[Unit]
Description=Dish Watch: quiet-hour learning
[Timer]
OnCalendar=*-*-* 04:45
Persistent=false
[Install]
WantedBy=timers.target
EOF
systemd-analyze --user verify "$units/dish-watch-learning.service" "$units/dish-watch-learning.timer"
# This scheduler replaces only the probe timer. fit-identity keeps owning identity
# weights and the guess display gate; the weekly detector retrain (dish-watch-improve)
# keeps the camera-off window this nightly job never takes.
systemctl --user disable --now dish-watch-verifier.timer
systemctl --user daemon-reload
systemctl --user enable --now dish-watch-learning.timer
