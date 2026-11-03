"""Arrival failures retry without inventing evidence or losing naming membership."""
import fcntl
import json
from datetime import timedelta

from test_journal import BOX, PERSON
from test_sequences import jpeg

from wash_dishes import filmstrip
from wash_dishes.filmstrip import TIMEOUT, Filmstrips
from wash_dishes.journal import Journal
from wash_dishes.recover_filmstrips import run
from wash_dishes.store import parse_stamp, stamp, utcnow


def make_item(store, at):
    return store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], source='vision', candidate=True, at=at, box=BOX)


def real_context(item):
    at = parse_stamp(item['created_at']).timestamp()
    return {'box': BOX, 'window': {'before': at-10, 'after': at+10, 'arrival_recorded': True},
            'frames': [{'at': at-10, 'jpeg': jpeg((50,60,70)), 'person_boxes': []},
                       {'at': at, 'jpeg': jpeg((80,90,100)), 'person_boxes': [PERSON['box']]},
                       {'at': at+10, 'jpeg': jpeg((110,120,130)), 'person_boxes': []}]}


def test_timeout_retries_after_backoff_without_permanent_visit(store, media, monkeypatch):
    now = utcnow()
    item = make_item(store, now-timedelta(hours=3))
    calls = []
    def timeout(*args, **kwargs):
        calls.append(True)
        return TIMEOUT
    monkeypatch.setattr(filmstrip, 'plan', timeout)
    with Journal(store.root/'journal.db') as journal:
        worker = Filmstrips(store, media, journal)
        assert worker.generate(item['id'], now=now)['status'] == 'retry'
        assert worker.generate(item['id'], now=now+timedelta(minutes=1))['status'] == 'retry'
        assert len(calls) == 1
        assert store.conn.execute('select count(*) from arrival_visits').fetchone()[0] == 0
        assert store.conn.execute("select count(*) from events where event='arrival_filmstrip_unavailable'").fetchone()[0] == 0
        assert store.obligation(item['id'])['evidence'] == []
        context = real_context(item)
        monkeypatch.setattr(filmstrip, 'plan', lambda *a, **kw: context)
        monkeypatch.setattr(filmstrip, 'display_frames', lambda *a, **kw: context['frames'])
        saved = worker.generate(item['id'], now=now+timedelta(minutes=6))
        assert saved['status'] == 'saved'
        assert worker.generate(item['id'], now=now+timedelta(minutes=12))['evidence_id'] == saved['evidence_id']
        assert store.conn.execute("select count(*) from evidence where kind='filmstrip'").fetchone()[0] == 1


def test_actual_footage_absence_stays_final(store, media):
    item = make_item(store, utcnow()-timedelta(hours=3))
    with Journal(store.root/'journal.db') as journal:
        worker = Filmstrips(store, media, journal)
        result = worker.generate(item['id'])
        assert result['status'] == 'unavailable' and 'No retained footage' in result['reason']
        assert worker.generate(item['id'], now=utcnow()+timedelta(hours=1)) == dict(store.conn.execute('select * from filmstrip_jobs').fetchone())
        assert store.conn.execute('select count(*) from arrival_visits').fetchone()[0] == 1


def seed_legacy_failure(store, media, now):
    item = make_item(store, now-timedelta(hours=3))
    Filmstrips(store, media, None)
    meta = {'visit_id': 'legacy', 'obligation_ids': [item['id']], 'canonical_obligation_ids': [item['id']],
            'duplicate_of': {}, 'frames': [], 'window': {'arrival_recorded': False, 'reason': TIMEOUT}}
    with store.conn:
        store.conn.execute('insert into arrival_visits values(?,?,?,?)', ('legacy',None,stamp(now-timedelta(hours=1)),json.dumps(meta)))
        store.conn.execute('insert into arrival_visit_members values(?,?)', (item['id'],'legacy'))
        store.conn.execute('insert into filmstrip_jobs values(?,?,?,?,?)', (item['id'],'unavailable',stamp(now-timedelta(hours=1)),TIMEOUT,None))
    return item


def test_recovery_dry_run_changes_nothing_and_success_preserves_visit_anchor_expiry(store, media, monkeypatch):
    now = utcnow()
    item = seed_legacy_failure(store, media, now)
    context = real_context(item)
    import wash_dishes.recover_filmstrips as recovery
    monkeypatch.setattr(recovery, 'footage_segments', lambda _: [(parse_stamp(item['created_at']).timestamp()-1, now.timestamp(), store.root/'test.ts')])
    monkeypatch.setattr(recovery.os, 'nice', lambda _: None)
    before = list(store.conn.iterdump()), list(store.root.rglob('*'))
    dry = run(store.root, now=now)
    assert dry['dry_run'] and dry['actions'][0]['retained']
    assert (list(store.conn.iterdump()), list(store.root.rglob('*'))) == before
    monkeypatch.setattr(filmstrip, 'plan', lambda *a, **kw: context)
    monkeypatch.setattr(filmstrip, 'display_frames', lambda *a, **kw: context['frames'])
    monkeypatch.setattr(filmstrip, 'identity_frame', lambda *a, **kw: context['frames'][1] | {'person_box': PERSON['box']})
    result = run(store.root, apply=True, now=now)
    assert result['results'][0]['status'] == 'saved'
    visit = store.arrival_visit('legacy')
    assert visit['obligation_ids'] == [item['id']]
    assert store.obligation(item['id'])['arrival_visit_id'] == 'legacy'
    evidence = store.evidence(visit['evidence_id'])
    assert parse_stamp(evidence['expires_at']) == parse_stamp(stamp(now-timedelta(hours=1)))+timedelta(days=7)
    assert visit['identity_pending_id'] and 'identity_evidence_id' not in visit
    identity_calls = []
    def identify(oid, frame, meta):
        identity_calls.append((oid,frame['at'],meta['visit_id']))
        return media.save_snapshot(oid, frame['jpeg'], at=parse_stamp(item['created_at']),
            capture={'identity_source':'arrival_visit','visit_id':meta['visit_id'],'person_box':frame['person_box'],'cues':{}})
    worker = Filmstrips(store, media, None, identify=identify)
    assert worker.complete_identity(now=now) == item['id']
    assert identity_calls == [(item['id'],parse_stamp(item['created_at']).timestamp(),'legacy')]
    visit = store.arrival_visit('legacy')
    assert 'identity_pending_id' not in visit and visit['identity_evidence_id']
    assert run(store.root, apply=True, now=now)['actions'] == []
    assert worker.generate(item['id'])['status'] == 'saved'
    assert store.conn.execute("select count(*) from evidence where kind='filmstrip'").fetchone()[0] == 1


def test_service_retry_is_selected_after_backoff(store, media, monkeypatch):
    now = utcnow()
    item = make_item(store, now-timedelta(hours=3))
    with store.conn:
        store.conn.execute('update obligations set left_out_at=? where id=?', (stamp(now-timedelta(minutes=30)),item['id']))
    monkeypatch.setattr(filmstrip, 'plan', lambda *a, **kw: TIMEOUT)
    monkeypatch.setattr(store, 'observed_left_out_at', lambda _: stamp(now))
    worker = Filmstrips(store, media, None)
    assert worker.run_once(now=now) == item['id']
    assert worker.run_once(now=now+timedelta(minutes=1)) is None
    assert worker.run_once(now=now+timedelta(minutes=6)) == item['id']


def test_finished_filmstrip_does_not_block_the_dishes_behind_it(store, media, monkeypatch):
    # A saved job has a NULL reason; it must count as finished, or the oldest one is reselected forever.
    now = utcnow()
    older, newer = make_item(store, now-timedelta(hours=3)), make_item(store, now-timedelta(hours=2))
    worker = Filmstrips(store, media, None)
    with store.conn:
        store.conn.execute('update obligations set left_out_at=? where id in (?,?)',
                           (stamp(now-timedelta(minutes=30)), older['id'], newer['id']))
        store.conn.execute("insert into filmstrip_jobs(obligation_id,status,reason,attempted_at) values (?,'saved',NULL,?)",
                           (older['id'], stamp(now-timedelta(hours=1))))
    monkeypatch.setattr(filmstrip, 'plan', lambda *a, **kw: TIMEOUT)
    monkeypatch.setattr(store, 'observed_left_out_at', lambda _: stamp(now))
    assert worker.run_once(now=now) == newer['id']


def test_recovery_and_worker_do_not_wait_on_another_footage_search(store, media):
    item = make_item(store, utcnow())
    worker = Filmstrips(store, media, None)
    with (store.root/'.filmstrip.lock').open('a') as claim:
        fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.generate(item['id'])['status'] == 'busy'
    assert store.conn.execute('select count(*) from filmstrip_jobs').fetchone()[0] == 0


def test_rerender_candidates_do_not_expand_saved_endpoints(monkeypatch, tmp_path):
    from wash_dishes.rerender import ReadJournal
    context = {'window': {'before': 100, 'after': 110, 'context_start': 55, 'arrival_recorded': True},
               'frames': [{'at': 100, 'person_boxes': []}, {'at': 110, 'person_boxes': []}]}
    monkeypatch.setattr(filmstrip, 'footage_segments', lambda _: [])
    monkeypatch.setattr(filmstrip, 'select_frames', lambda *a, **kw: [{'at': t, 'person_boxes': []} for t in (55,100,105,110)])
    monkeypatch.setattr(filmstrip, '_photo_at', lambda _, at: (at, jpeg((50,60,70))))
    frames = filmstrip.display_frames(ReadJournal(tmp_path/'missing.db'), tmp_path, context, [BOX], deadline=float('inf'))
    assert frames[0]['at'] == 100 and frames[-1]['at'] == 110


def test_batched_decoder_keeps_requested_pts_and_omits_unneeded_jpegs(tmp_path):
    import shutil
    import subprocess
    import time

    import pytest

    from wash_dishes import putdown
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    video = tmp_path/'synthetic.ts'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=96x64:rate=15',
                    '-t','4','-c:v','libx264','-f','mpegts',str(video)], check=True, capture_output=True)
    result = putdown.decoded_photos((100,104,video), fps=2, t0=100, t1=104,
                                   sample_times=[100.5,103], deadline=time.monotonic()+10)
    assert [at for at,_ in result] == [100.5,103]
    assert all(data.startswith(b'\xff\xd8') for _,data in result)


def test_plan_expiry_cannot_fall_through_to_already_present(store, media, monkeypatch):
    import time
    item = make_item(store, utcnow())
    at = parse_stamp(item['created_at']).timestamp()
    monkeypatch.setattr(filmstrip.putdown, 'segments', lambda _: [(at-1, at+10, store.root/'source.ts')])
    with Journal(store.root/'journal.db') as journal:
        assert filmstrip.plan(journal, media.buffer, item, deadline=time.monotonic()-1) == TIMEOUT
