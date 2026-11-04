"""Nearby people are review context, never a recorded arrival or an owner."""
import io
import json
import time
from datetime import timedelta

import pytest
from PIL import Image
from test_sequences import jpeg

from wash_dishes import filmstrip
from wash_dishes.journal import Journal
from wash_dishes.rerender import run
from wash_dishes.review_evidence import rate
from wash_dishes.store import stamp, utcnow

BOX = [.4, .6, .08, .06]
CLEAR = [.2, .15, .2, .65]
COVERED = [.3, .2, .3, .7]


def observation(at, people=()):
    return {'at': at, 'detections': [{'kind': 'person', 'score': .9, 'box': b} for b in people]}


def test_long_stays_beat_recent_pass_throughs_and_are_distinct():
    frames = [observation(t, [CLEAR]) for t in range(100, 701)]
    frames += [observation(t, [CLEAR]) for a, b in ((800, 901), (1000, 1101), (1200, 1261)) for t in range(a, b)]
    frames += [observation(t, [CLEAR]) for t in (1400, 1500, 1600)]
    selected = filmstrip.use_frames(frames, BOX, 1700)
    visits = {f['use_visit']['start'] for f in selected}
    assert len(visits) == 3 and {100, 1200} <= visits
    assert all(v < 1400 for v in visits)  # A pass-through cannot displace a sustained visit.
    assert len(selected) <= 9
    assert all(f['person_boxes'] == [CLEAR] for f in selected)


def test_no_nearby_person_invents_neither_visit_nor_person():
    assert filmstrip.use_frames([observation(t, [[.9, .1, .05, .2]]) for t in range(500)], BOX, 500) == []
    assert filmstrip.use_frames([observation(-6*3600-1, [CLEAR]), observation(1, [CLEAR])], BOX, 0) == []


def test_clear_solo_view_beats_body_covering_dish_or_multiple_people(monkeypatch, tmp_path):
    frames = [observation(100, [COVERED]), observation(110, [CLEAR, COVERED]), observation(120, [CLEAR])]
    selected = filmstrip.use_frames(frames, BOX, 140)
    found = {'box': BOX, 'window': {'before': 140, 'after': 150, 'arrival_recorded': False},
             'frames': selected + [{'at': 140, 'person_boxes': []}, {'at': 150, 'person_boxes': []}]}
    with Journal(tmp_path/'journal.db') as journal:
        for frame in frames:
            journal.record(frame['at'], frame['detections'])
        monkeypatch.setattr(filmstrip, 'footage_segments', lambda _: [])
        monkeypatch.setattr(filmstrip, '_photo_at', lambda _, at: (at, jpeg((60, 80, 100) if at >= 140 else (10, 20, 180))))
        displayed = filmstrip.display_frames(journal, tmp_path, found, [BOX], deadline=time.monotonic()+30)
    people = [f for f in displayed if f['person_boxes']]
    assert len(people) == 1 and people[0]['at'] == 120
    assert people[0]['use_visit']['end'] == 120
    assert filmstrip.identity_frame(None, displayed, [BOX], arrival_recorded=False) is None
    assert filmstrip.identity_frame(None, displayed, [BOX], arrival_recorded=True) is None
    assert rate({'window': found['window'], 'frames': displayed}, has_filmstrip=True, has_photo=True,
                dish_confident=False, remembered_near_match=False)['evidence'] == 'fair'


def test_missing_arrival_plan_keeps_person_boxes_and_honest_window(store, media, monkeypatch):
    now = utcnow()
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], source='vision',
                                   candidate=True, box=BOX, at=now)
    anchor = now.timestamp()
    monkeypatch.setattr(filmstrip, 'footage_segments', lambda _: [(anchor-700, anchor+40, store.root/'fake.ts')])
    monkeypatch.setattr(filmstrip, '_photo_at', lambda _, at: (at, jpeg((60, 80, 100))))
    with Journal(store.root/'journal.db') as journal:
        for i in range(100):
            journal.record(anchor-200+i, observation(0, [CLEAR])['detections'])
        found = filmstrip.plan(journal, media.buffer, item)
    assert found['window']['arrival_recorded'] is False
    assert any(f.get('use_visit') and f['person_boxes'] for f in found['frames'])
    assert all(f['at'] < anchor for f in found['frames'] if f.get('use_visit'))


def test_use_render_boxes_people_and_dish_and_does_not_accuse(monkeypatch):
    from wash_dishes import evidence_image
    seen = {}
    def capture(title, subtitle, panels, notes):
        seen.update(panels=panels, notes=notes)
        return b'pixels'
    monkeypatch.setattr(evidence_image, 'render', capture)
    frames = [{'at': 100, 'jpeg': b'candidate', 'person_boxes': [CLEAR],
               'use_visit': {'start': 100, 'end': 640, 'observations': 100}},
              {'at': 700, 'jpeg': b'detail', 'person_boxes': []}]
    filmstrip.render({'box': BOX, 'location': 'Counter'}, frames, already_at=700)
    candidate = seen['panels'][0]
    assert candidate['boxes'] == [BOX] and candidate['person_boxes']
    assert candidate['label'].startswith('Near this spot for 9 min, until ')
    text = ' '.join(seen['notes'])
    assert "Arrival wasn't recorded" in text and 'Only name someone if you know they used' in text
    assert 'not confirmed users' in text


@pytest.mark.parametrize('stale_person', [False, True])
def test_use_backfill_preserves_human_names_membership_expiry_and_resume(store, media, monkeypatch, stale_person):
    now = utcnow()
    person = store.add_resident('Test Resident')['id']
    items = [store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], box=BOX,
                                    candidate=True, source='vision', at=now) for _ in range(2)]
    store.action(items[0]['id'], 'confirm', resident_id=person)
    at = now.timestamp()
    old = {'obligation_ids': [i['id'] for i in items], 'canonical_obligation_ids': [items[0]['id']],
           'duplicate_of': {str(items[1]['id']): items[0]['id']}, 'dish_boxes': {str(i['id']): BOX for i in items},
           'window': {'before': at, 'after': at+10, 'arrival_recorded': stale_person, 'first_visible_at': at},
           'frames': [{'at': at, 'person_boxes': [CLEAR] if stale_person else []}, {'at': at+10, 'person_boxes': []}]}
    for vid in ('a', 'b'):
        eid = 'arrival-'+vid
        with store.conn:
            store.conn.execute("INSERT INTO evidence VALUES(?,?,?,'filmstrip',?,?,'arrival',0,?)",
                               (eid, items[0]['id'], eid+'.jpg', stamp(now), stamp(now+timedelta(days=7)), json.dumps(old)))
            store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?)', (vid, eid, stamp(now), json.dumps(old)))
        media.safe_path(eid+'.jpg').write_bytes(jpeg((60, 80, 100)))
    with store.conn:
        store.conn.executemany('INSERT INTO arrival_visit_members VALUES(?,?)', [(i['id'], 'a') for i in items])
    def state():
        return list(store.conn.execute('SELECT * FROM obligations')), list(store.conn.execute('SELECT * FROM arrival_visit_members'))
    before = state()
    dump = list(store.conn.iterdump())
    if stale_person:
        assert run(store.root, use_context=True)['actions'] == []
    target = items[0]['id'] if stale_person else None
    dry = run(store.root, use_context=True, limit=1, obligation_id=target)
    assert dry['next_visit_id'] == 'a' and list(store.conn.iterdump()) == dump
    frames = [{'at': at-100, 'person_boxes': [CLEAR], 'jpeg': jpeg((80, 90, 120)),
               'use_visit': {'start': at-100, 'end': at-10, 'observations': 70}},
              *[f | {'jpeg': jpeg((60, 80, 100))} for f in old['frames']]]
    monkeypatch.setattr(filmstrip, 'plan', lambda *a, **kw: {'window': old['window'], 'box': BOX, 'frames': frames})
    monkeypatch.setattr(filmstrip, 'display_frames', lambda *a, **kw: frames)
    saved = store.evidence('arrival-a')
    result = run(store.root, use_context=True, apply=True, limit=1, obligation_id=target)
    assert result['updated'][0]['target'] == 'a' and state() == before
    after = store.evidence('arrival-a')
    assert after['expires_at'] == saved['expires_at'] and after['id'] == saved['id']
    meta = json.loads(after['capture'])
    assert meta['obligation_ids'] == old['obligation_ids'] and meta['duplicate_of'] == old['duplicate_of']
    assert meta['window']['arrival_recorded'] is stale_person and meta['frames'][0]['person_boxes']
    assert Image.open(io.BytesIO(media.safe_path(after['filename']).read_bytes())).width == 1080
    assert run(store.root, use_context=True, after_visit_id=result['next_visit_id'], obligation_id=target)['actions'] == [{'kind': 'filmstrip', 'target': 'b'}]


def test_stable_stay_frame_beats_moving_clear_box():
    # Moving boxes can be a few seconds out of step with retained pixels. Keep
    # the stable cooking view even if a fleeting box exposes more of the dish.
    frames = [observation(100, [CLEAR]), observation(103, [[.34,.3,.1,.4]])]
    frames += [observation(t, [COVERED]) for t in range(110, 200)]
    chosen = filmstrip.use_frames(frames, BOX, 210)
    assert all(f['at'] >= 110 and f['person_stability'] >= .4 for f in chosen)


def test_use_backfill_does_not_overlap_the_live_worker(store):
    import fcntl
    with (store.root/'.filmstrip.lock').open('a') as claim:
        fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(store.root, use_context=True, apply=True, after_visit_id='last-completed')
    assert result['busy'] and result['next_visit_id'] == 'last-completed'


def test_nearby_use_context_cannot_merge_naming_visits():
    found = {'box': BOX, 'window': {'arrival_recorded': True},
             'frames': [{'at': 100, 'person_boxes': [CLEAR], 'use_visit': {'start': 100, 'end': 200}}]}
    assert not filmstrip.same_visit(None, found, found)


def test_unchanged_background_cannot_supply_a_person():
    from wash_dishes.evidence_image import person_changed
    background = jpeg((60, 80, 100))
    assert not person_changed(background, background, CLEAR)
    assert person_changed(jpeg((160, 180, 200)), background, CLEAR)


def test_supported_arrival_stays_unchanged_but_fleeting_box_gets_context(tmp_path):
    class History:
        def __init__(self, frames):
            self.observations = frames
        def frames(self, start, end):
            return (f for f in self.observations if start <= f['at'] <= end)
    found = {'box': BOX, 'window': {'arrival_recorded': True, 'before': 190, 'after': 210},
             'frames': [{'at': 200, 'person_boxes': [CLEAR]}, {'at': 210, 'person_boxes': []}]}
    sustained = [observation(t, [CLEAR]) for t in range(190, 208)]
    segments = [(0, 220, tmp_path/'retained.ts')]
    assert filmstrip.with_use_context(History(sustained), found, 210, segments) == found
    earlier = [observation(t, [CLEAR]) for t in range(100)] + [observation(200, [CLEAR])]
    extended = filmstrip.with_use_context(History(earlier), found, 210, segments)
    assert extended['window'] == found['window']
    assert any(f.get('use_visit') and f['use_visit']['start'] == 0 for f in extended['frames'])


def test_recorded_arrival_drops_empty_who_panel(monkeypatch):
    from wash_dishes import evidence_image
    labels = []
    monkeypatch.setattr(evidence_image, 'render', lambda title, subtitle, panels, notes:
                        labels.extend(p['label'] for p in panels) or b'image')
    background = jpeg((60, 80, 100))
    frames = [{'at': 100, 'jpeg': background, 'person_boxes': [CLEAR]},
              {'at': 110, 'jpeg': background, 'person_boxes': []}]
    filmstrip.render({'box': BOX, 'location': 'Counter'}, frames)
    assert labels == ['Dish detail']
    labels.clear()
    frames[0]['jpeg'] = jpeg((160, 180, 200))
    filmstrip.render({'box': BOX, 'location': 'Counter'}, frames)
    assert 'Who was at the counter' in labels


def test_sparse_passes_do_not_become_a_sustained_stay():
    observations = [observation(t, [CLEAR]) for t in range(121)]
    observations += [observation(t, [CLEAR]) for t in (300, 350, 400)]
    chosen = filmstrip.use_frames(observations, BOX, 500)
    assert {f['use_visit']['start'] for f in chosen} == {0}


def test_recorder_restart_does_not_stretch_its_first_segment(tmp_path):
    import os
    start = time.time()-1000
    previous = tmp_path/'111-000099.ts'
    first = tmp_path/'222-000000.ts'
    second = tmp_path/'222-000001.ts'
    for filename, at in ((previous,start),(first,start+18),(second,start+28)):
        filename.write_bytes(b'synthetic segment index only')
        os.utime(filename,(at,at))
    segments = {p.name:(a,b) for a,b,p in filmstrip.footage_segments(tmp_path)}
    assert segments[first.name] == (start+8,start+18)
    assert segments[second.name] == (start+18,start+28)
