"""Worker optimizations retain filesystem boundaries, recovery, and cleanup audit facts."""
import json
import os
from datetime import timedelta

import pytest

from wash_dishes.dataset import Dataset
from wash_dishes.recovery import EvidenceRecovery
from wash_dishes.store import stamp, utcnow


def test_evidence_paths_require_a_single_non_special_basename(media, tmp_path):
    outside = tmp_path / 'outside.jpg'
    outside.write_bytes(b'private')
    (media.media / 'link.jpg').symlink_to(outside)
    (media.media / 'broken.jpg').symlink_to(tmp_path / 'missing')
    (media.media / 'inside.jpg').symlink_to(media.media / 'original.jpg')
    for name in ('', '.', '..', '../outside.jpg', '/tmp/outside.jpg', 'nested/photo.jpg',
                 'link.jpg', 'broken.jpg', 'inside.jpg'):
        with pytest.raises(ValueError):
            media.safe_path(name)
    assert media.safe_path('original.jpg') == media.media / 'original.jpg'
    assert outside.read_bytes() == b'private'


@pytest.mark.parametrize('remove_photo', [False, True])
def test_recovery_rechecks_changed_labels_and_missing_originals(store, media, monkeypatch, remove_photo):
    from test_sequences import jpeg

    at = utcnow() - timedelta(minutes=5)
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], at=at,
                                   box=[.3, .5, .1, .1], source='vision', candidate=True)
    media.save_snapshot(item['id'], jpeg('red'), at=at, capture={'stage': 'leaving'})
    dataset = Dataset(store.root, store)
    frame_id = dataset.save(jpeg('blue'), {'detections': []}, 'test', observed_at=at-timedelta(seconds=10))
    recovery = EvidenceRecovery(store, media, dataset)
    assert recovery.refresh() == 0
    path = dataset.labels / (frame_id + '.json')
    info = path.stat()
    label = json.loads(path.read_text())
    label['predicted'] = [{'kind': 'pan', 'box': item['box'], 'score': .9},
                          {'kind': 'person', 'box': [.2, .2, .5, .7], 'score': .9}]
    path.write_text(json.dumps(label))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    if remove_photo:
        dataset.image(frame_id).unlink()
    # New capture changes the dataset version, and this record's retry interval has elapsed.
    dataset.save(jpeg('green'), {'detections': []}, 'new capture', observed_at=at+timedelta(seconds=10))
    version, _ = recovery.attempts[item['id']]
    recovery.attempts[item['id']] = (version, float('-inf'))
    assert recovery.refresh() == (0 if remove_photo else 1)
    result = store.obligation(item['id'])
    using = [e for e in result['evidence'] if e['capture'].get('stage') == 'using']
    assert len(using) == (0 if remove_photo else 1)
    if using:
        assert using[0]['capture']['dataset_frame_id'] == frame_id
    assert result['followup_at'] == item['followup_at']
    assert result['resident_id'] is None


def test_cleanup_coalesces_audit_rows_without_losing_times_or_counts(store, media, monkeypatch):
    at = utcnow().replace(minute=10, second=0, microsecond=0)
    store.set_settings({'buffer_hours': 1})
    runs = []
    for minutes in (0, 1, 2, 61):
        now = at + timedelta(minutes=minutes)
        monkeypatch.setattr('wash_dishes.media.utcnow', lambda: now)
        segment = media.buffer / f'expired-{minutes}.ts'
        segment.write_bytes(b'segment')
        old = (now-timedelta(hours=2)).timestamp()
        os.utime(segment, (old, old))
        assert media.cleanup()['removed_files'] == 1
        runs.append({'at': stamp(now), 'removed_files': 1})
    assert media.cleanup()['removed_files'] == 0
    rows = store.conn.execute("SELECT * FROM events WHERE event='storage_cleanup' ORDER BY id").fetchall()
    assert len(rows) == 2
    first, second = [json.loads(r['data']) for r in rows]
    assert first == {'removed_files': 3, 'runs': runs[:3]}
    assert second == {'removed_files': 1, 'runs': runs[3:]}
