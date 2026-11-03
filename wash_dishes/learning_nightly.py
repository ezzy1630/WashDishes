"""Nightly CLI entry point. Never stops/restarts camera services to fit a model."""
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .learning_jobs import run


def camera_active():
    # Fail closed on unknown service state. Training cannot fit alongside loaded live models.
    if sys.platform != 'linux':
        return False
    result = subprocess.run(['systemctl', '--user', 'is-active', 'dish-watch.service'], capture_output=True, text=True)
    return result.stdout.strip() not in ('inactive', 'failed')


def nightly(store):
    now = datetime.now(ZoneInfo(os.getenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles')))
    if now.hour != 4 or now.minute < 45:
        return {'status': 'outside_quiet_hour'}
    os.nice(10)
    from .learning_state import camera_busy
    return run(store, busy=lambda: camera_busy(store.root, active=camera_active()), day=now.date().isoformat())
