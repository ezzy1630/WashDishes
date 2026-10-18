#!/usr/bin/env bash
# Three user timers on the Jetson:
#   dish-watch-identity: daily 04:30, relearn identity cue weights (separate low-priority process).
#   dish-watch-verifier: daily 04:45, retrain the second look at new dishes from every label, keeping
#                        the hand-checked evaluation windows out of training.
#   dish-watch-improve:  Sunday 02:00, stop live analysis, retrain and evaluate the kitchen detector,
#                        adopt it only if it does better, then start the service again.
# Usage: scripts/install-improvement-timers.sh TRUTH_JSON FOOTAGE_DIR [TRUTH_JSON FOOTAGE_DIR ...]
set -euo pipefail
if [[ "$(uname -s)" != Linux || $# -lt 2 || $(($# % 2)) -ne 0 ]]; then
  printf '%s\n' 'Run on the Jetson: scripts/install-improvement-timers.sh TRUTH_JSON FOOTAGE_DIR [TRUTH_JSON FOOTAGE_DIR ...]' >&2
  exit 1
fi
task_repo="$(cd -- "$(dirname -- "$0")/.." && pwd)"
periods=""
holdouts=""
while (($#)); do
  truth="$(realpath "$1")"; footage="$(realpath "$2")"; shift 2
  [[ -f "$truth" && -d "$footage" ]] || { printf '%s\n' "Truth file or footage folder not found: $truth $footage" >&2; exit 1; }
  periods+=" --truth \"${truth//%/%%}\" --footage \"${footage//%/%%}\""
  holdouts+=" --holdout \"${truth//%/%%}\""
done
export WASHDISHES_DATA_DIR="${WASHDISHES_DATA_DIR:-$HOME/.local/share/washdishes}"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
repo="${task_repo//%/%%}"
data="${WASHDISHES_DATA_DIR//%/%%}"
common="WorkingDirectory=$repo
Environment=\"WASHDISHES_DATA_DIR=$data\"
Environment=PYTHONUNBUFFERED=1
Environment=\"PATH=$repo/.tools/bin:/usr/local/bin:/usr/bin:/bin\"
UMask=0077
NoNewPrivileges=true"

cat > "$units/dish-watch-identity.service" <<EOF
[Unit]
Description=Dish watch: relearn identity cue weights

[Service]
Type=oneshot
$common
Nice=10
ExecStart="$repo/.venv/bin/python" -m wash_dishes fit-identity
EOF

cat > "$units/dish-watch-verifier.service" <<EOF
[Unit]
Description=Dish watch: retrain the second look at new dishes

[Service]
Type=oneshot
$common
Nice=10
ExecStart="$repo/.venv/bin/python" -m wash_dishes train-verifier$holdouts
EOF

cat > "$units/dish-watch-verifier.timer" <<EOF
[Unit]
Description=Dish watch: retrain the second look daily

[Timer]
OnCalendar=*-*-* 04:45
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "$units/dish-watch-identity.timer" <<EOF
[Unit]
Description=Dish watch: relearn identity cue weights daily

[Timer]
OnCalendar=*-*-* 04:30
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Training needs the GPU memory live analysis holds, so the service is stopped for the run and
# always started again afterwards, whether or not training succeeded.
# The Jetson's clock can boot at 1970 and jump forward once synced, which a user timer reads as
# "Sunday 02:00 has passed". ExecCondition skips the run, before anything is stopped, unless it
# really is early Sunday morning.
cat > "$units/dish-watch-improve.service" <<EOF
[Unit]
Description=Dish watch: retrain the kitchen detector and adopt it only if better

[Service]
Type=oneshot
$common
Nice=5
TimeoutStartSec=6h
ExecCondition=/bin/sh -c 'now=\$\$(date +%%Y%%u%%H); [ "\$\${now%%???}" -ge 2026 ] && [ "\$\${now#????}" -ge 701 ] && [ "\$\${now#????}" -le 705 ]'
ExecStartPre=/usr/bin/systemctl --user stop dish-watch.service
ExecStart="$repo/.venv/bin/python" -m wash_dishes improve$periods
ExecStopPost=/usr/bin/systemctl --user start dish-watch.service
EOF

cat > "$units/dish-watch-improve.timer" <<EOF
[Unit]
Description=Dish watch: weekly detector retraining

[Timer]
OnCalendar=Sun *-*-* 02:00
Persistent=false

[Install]
WantedBy=timers.target
EOF

for unit in dish-watch-identity.service dish-watch-identity.timer dish-watch-verifier.service dish-watch-verifier.timer dish-watch-improve.service dish-watch-improve.timer; do
  systemd-analyze --user verify "$units/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now dish-watch-identity.timer dish-watch-verifier.timer dish-watch-improve.timer
systemctl --user list-timers 'dish-watch-*'
