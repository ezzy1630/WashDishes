"""Small camera occupancy heartbeat; no images or model objects cross this boundary."""
import json
import os
from datetime import timedelta

from .store import parse_stamp, stamp, utcnow


def note(root, frame_at, people, *, loaded):
    path = root / 'learning-camera.json'
    now = utcnow()
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {}
    try:
        if not isinstance(old, dict) or not 0 <= (now - parse_stamp(old['at'])).total_seconds() <= 30:
            old = {}
    except (KeyError, ValueError, TypeError):
        old = {}
    quiet_since = old.get('quiet_since') if not people and old.get('people') == 0 else None
    payload = {'at': stamp(now), 'frame_at': frame_at, 'people': people,
               'quiet_since': quiet_since or (stamp(now) if not people else None), 'models_loaded': loaded}
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(payload))
        os.replace(temporary, path)
    except OSError:
        # Losing the learning heartbeat must not stop recording/analysis. Remove
        # stale permission to train; an unreadable/missing heartbeat fails closed.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def camera_busy(root, *, active):
    if not active:
        return False
    try:
        data = json.loads((root / 'learning-camera.json').read_text())
        now = utcnow()
        return (now - parse_stamp(data['at']) > timedelta(seconds=30)
                or now - parse_stamp(data['frame_at']) > timedelta(seconds=30)
                or data['people'] != 0 or not data['quiet_since']
                or now - parse_stamp(data['quiet_since']) < timedelta(seconds=60))
    except (OSError, ValueError, KeyError, TypeError):
        return True
