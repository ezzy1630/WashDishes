from datetime import timedelta

from wash_dishes.dataset import Dataset
from wash_dishes.store import stamp, utcnow

JPEG = b'\xff\xd8frame\xff\xd9'
PAN = {'kind': 'pan', 'score': .8, 'box': [.2, .6, .1, .1]}
AREA = {'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [.1, .5, .4, .4]}


def test_capture_keeps_exact_predictions_and_reserves_neighboring_frames(authenticated):
    runtime = authenticated.app.state.runtime
    at = utcnow()
    neighbor = runtime.dataset.save(JPEG, {'detections': [PAN]}, 'Before correction', observed_at=at - timedelta(seconds=30))
    runtime.publish_live(JPEG, 'g', stamp(at), {'detections': [PAN], 'size': [640, 360]}, [AREA], [])
    result = authenticated.post('/api/dataset/capture', json={'live_frame_id': runtime.live_id, 'purpose': 'evaluation'})
    assert result.status_code == 201
    item = result.json()
    assert item['holdout'] and item['predicted'][0]['box'] == PAN['box']
    assert item['capture']['areas'] == [AREA]
    assert runtime.dataset.item(neighbor)['holdout']
    future = runtime.dataset.save(JPEG, {'detections': []}, 'Future neighbor', observed_at=at + timedelta(seconds=30))
    assert runtime.dataset.item(future)['holdout']
    # The corrected labels never replace the original detector prediction.
    result = authenticated.put('/api/dataset/' + item['id'], json={'status': 'labeled', 'boxes': [{'kind': 'plate', 'box': [.7, .6, .1, .1]}]})
    assert result.status_code == 200
    assert result.json()['predicted'] == item['predicted']
    measured = authenticated.get('/api/dataset').json()['evaluation']
    assert (measured['frames'], measured['matched'], measured['missed'], measured['extra']) == (1, 0, 1, 1)


def test_stale_capture_and_no_footage_are_explicit(authenticated):
    assert authenticated.post('/api/dataset/capture', json={'live_frame_id': 99, 'purpose': 'training'}).status_code == 409
    runtime = authenticated.app.state.runtime
    fid = runtime.dataset.save(JPEG, {'detections': [PAN]}, 'Anchor')
    label = runtime.dataset.item(fid)
    label['capture'] = {'areas': [AREA], 'detections': [], 'live_frame_id': 1}
    runtime.dataset._write(fid, label)
    r = authenticated.post('/api/traces', json={'frame_id': fid, 'area_id': 'counter', 'hours': 6})
    assert r.status_code == 201
    job = r.json()
    runtime.traces.run(job['id'])
    result = authenticated.get('/api/traces/' + job['id']).json()
    assert result['status'] == 'complete' and result['candidates'] == []
    assert result['coverage']['segments'] == 0
    assert 'No retained' in result['message']
    assert authenticated.get('/api/traces/not-a-valid-id').status_code == 400


def test_evaluation_frames_never_enter_training_export(store, tmp_path):
    data = Dataset(store.root, store)
    at = utcnow()
    for n in range(12):
        fid = data.save(JPEG, {'detections': [PAN]}, 'Older training', observed_at=at - timedelta(days=1, minutes=n * 20))
        data.update(fid, [PAN], 'labeled')
    held = data.capture(JPEG, {'detections': [PAN]}, observed_at=at, purpose='evaluation', capture={'areas': [AREA]})
    data.update(held['id'], [PAN], 'labeled')
    exported = data.export(tmp_path / 'export')
    assert not any(p.stem == held['id'] for p in exported.parent.rglob('*.jpg'))


def test_reserved_window_also_excludes_verifier_review_samples(store):
    import numpy as np

    from wash_dishes.verifier import examples
    at = utcnow()
    data = Dataset(store.root, store)
    data.capture(JPEG, {'detections': []}, observed_at=at, purpose='evaluation', capture={'areas': [AREA]})
    for when in (at, at - timedelta(days=1)):
        sid = store.add_dish_sample([1., 0.], .9, at=when)
        store.label_dish_sample(sid, True)
    x, y = examples(store.root, store, lambda _: (None, []), lambda _: np.array([]))
    assert x.shape == (1, 2) and y.tolist() == [1]


def test_a_replay_improvement_cannot_bypass_the_reserved_picture_gate(store, tmp_path):
    from wash_dishes.improve import KITCHEN, improve
    current = store.root / 'models' / KITCHEN
    current.write_bytes(b'current')
    candidate = tmp_path / 'candidate.pt'
    candidate.write_bytes(b'candidate')
    def replay(model):
        return {'left_out': {'truth': 10, 'matched': 9, 'predicted': 10 if model else 12, 'recall': .9, 'precision': .9 if model else .75, 'f1': .9 if model else .82}}
    def stills(model):
        return {'frames': 2, 'snapshot': 'same-frozen-set', 'missed': 1 if model else 0, 'extra': 0}
    result = improve(store.root, store, 30, train=lambda: candidate, evaluate=replay, evaluate_stills=stills)
    assert not result['promoted'] and 'reserved picture' in result['reason']
    assert current.read_bytes() == b'current'


def test_real_recording_search_keeps_candidate_without_assigning_owner(store, media, tmp_path):
    import os
    import shutil
    import subprocess

    import cv2
    import numpy as np

    from wash_dishes.tracing import TraceSearch, change_score, region_signature
    before = np.full((180, 320, 3), 80, np.uint8)
    after = before.copy()
    cv2.rectangle(after, (70, 110), (100, 140), (235, 235, 235), -1)
    for name, image in [('before', before), ('after', after)]:
        cv2.imwrite(str(tmp_path / (name + '.jpg')), image)
        subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-loop', '1', '-i', str(tmp_path / (name + '.jpg')),
                        '-t', '10', '-r', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-f', 'mpegts', str(tmp_path / (name + '.ts'))], check=True, timeout=15)
    at = utcnow()
    for index in range(11):
        path = media.buffer / f'{index:03d}.ts'
        shutil.copyfile(tmp_path / ('before.ts' if index < 4 else 'after.ts'), path)
        ended = (at - timedelta(seconds=(10 - index) * 10)).timestamp()
        os.utime(path, (ended, ended))
    data = Dataset(store.root, store)
    record = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], source='baseline', candidate=True, timing_known=False)
    item = data.capture((tmp_path / 'after.jpg').read_bytes(), {'detections': [PAN]}, observed_at=at, purpose='reference',
                        capture={'areas': [AREA], 'detections': [PAN | {'area_id': 'counter', 'obligation_id': record['id']}]})
    assert not item['correction'] and item['status'] == 'skipped'
    search = TraceSearch(store.root, store, media, data)
    job = search.create(item['id'], 'counter', 1, record['id'])
    search.run(job['id'])
    result = search.get(job['id'])
    assert result['status'] == 'complete' and result['candidates']
    # The media lock also serves inference snapshots. FFmpeg must not hold it.
    import threading
    from unittest.mock import patch
    original_run = subprocess.run
    def export(*args, **kwargs):
        if '-f' in args[0] and 'concat' in args[0]:
            acquired = threading.Event()
            def snapshot_access():
                with media.lock:
                    acquired.set()
            worker = threading.Thread(target=snapshot_access, daemon=True)
            worker.start()
            assert acquired.wait(1), 'Video export blocked inference snapshot storage'
            worker.join(timeout=1)
        return original_run(*args, **kwargs)
    with patch('wash_dishes.tracing.subprocess.run', side_effect=export):
        kept = search.keep(job['id'], 0)
    assert kept['candidates'][0]['kept']
    assert len(store.obligation(record['id'])['evidence']) == 3
    search.keep(job['id'], 0)
    assert len(store.obligation(record['id'])['evidence']) == 3
    for path in media.buffer.glob('*.ts'):
        path.unlink()
    recovered = TraceSearch(store.root, store, media, data)
    assert recovered.get(job['id'])['candidates'][0]['kept']
    assert recovered.clip(job['id'], 0).is_file()
    saved = store.obligation(record['id'])
    assert saved['resident_id'] is None and not saved['timing_known'] and not saved['overdue_verified']
    # A change elsewhere in the image cannot become activity in this selected area.
    outside = before.copy()
    cv2.rectangle(outside, (250, 0), (315, 60), (255, 255, 255), -1)
    a = cv2.imencode('.jpg', before)[1].tobytes()
    b = cv2.imencode('.jpg', outside)[1].tobytes()
    assert change_score(region_signature(a, AREA['box']), region_signature(b, AREA['box'])) < .025
