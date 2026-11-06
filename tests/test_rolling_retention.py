import os
from datetime import timedelta

from wash_dishes.store import Store, utcnow


def test_four_day_retention_preserves_recent_footage_and_expires_older_segments(store, media, monkeypatch):
    now = utcnow()
    monkeypatch.setattr('wash_dishes.media.utcnow', lambda: now)
    for age in (35, 50, 72, 95.99, 96.01):
        path = media.buffer / f'{age}.ts'
        path.write_bytes(b'recording')
        ended = (now - timedelta(hours=age)).timestamp()
        os.utime(path, (ended, ended))

    media.cleanup()

    assert sorted(p.name for p in media.buffer.glob('*.ts')) == ['35.ts', '50.ts', '72.ts', '95.99.ts']


def test_four_day_retention_can_be_saved_through_settings_and_survives_reopen(authenticated):
    result = authenticated.patch('/api/settings', json={'buffer_hours': 96, 'storage_limit_gb': 150})
    assert result.status_code == 200
    root = authenticated.app.state.runtime.store.root
    reopened = Store(root)
    try:
        assert reopened.settings()['buffer_hours'] == 96
        assert reopened.settings()['storage_limit_gb'] == 150
    finally:
        reopened.close()


def test_search_accepts_the_full_four_day_window(authenticated):
    from test_live_tools import AREA, JPEG, PAN

    data = authenticated.app.state.runtime.dataset
    frame_id = data.save(JPEG, {'detections': [PAN]}, 'Anchor')
    frame = data.item(frame_id)
    frame['capture'] = {'areas': [AREA], 'detections': []}
    data._write(frame_id, frame)
    result = authenticated.post('/api/traces', json={'frame_id': frame_id, 'area_id': 'counter', 'hours': 96})
    assert result.status_code == 201
