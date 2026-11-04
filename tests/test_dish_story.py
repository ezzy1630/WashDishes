import io
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from wash_dishes import dish_story
from wash_dishes.store import stamp
from wash_dishes.story_jobs import Stories

BOX = [.4, .6, .08, .07]
NEAR = {'kind': 'person', 'score': .9, 'box': [.25,.15,.12,.6]}
COVER = NEAR | {'box': [.35,.15,.2,.7]}


class History:
    def __init__(self, *, covered=False):
        self.observations = [{'at': float(t), 'detections':
            ([COVER if covered else NEAR] if 20<=t<=60 else [])} for t in range(240)]

    def frames(self, a, b):
        return (f for f in self.observations if a<=f['at']<=b)

    def bounds(self):
        return 0, 239


def image(present, light=0):
    im = Image.new('RGB', (800,480), (70+light,75+light,80+light))
    d = ImageDraw.Draw(im)
    if present:
        d.ellipse((321,289,382,319), fill=(150+light,145+light,140+light), outline=(30+light,30+light,30+light), width=4)
        d.line((334,300,373,307), fill=(40+light,50+light,60+light), width=3)
    else:
        d.line((321,290,380,315), fill=(80+light,85+light,90+light), width=2)
    out = io.BytesIO()
    im.save(out, 'JPEG')
    return out.getvalue()


def item():
    return {'id': 1, 'box': BOX, 'created_at': stamp(datetime.fromtimestamp(70,timezone.utc)),
            'due_at': stamp(datetime.fromtimestamp(180,timezone.utc)), 'location': 'Counter'}


def select(history=None, *, arrived=True, gone=False, light=False):
    def decode(t):
        present = (t>=45 if arrived else True) and not (gone and t>=180)
        return t, image(present, 20 if light and t>=45 else 0)
    return dish_story.select(history or History(), None, item(), now=239, decode=decode)


def test_story_requires_verified_after_and_keeps_real_times():
    story = select()
    assert isinstance(story, dict)
    assert [f['at'] for f in story['frames']] == sorted(f['at'] for f in story['frames'])
    assert story['frames'][0]['at'] < 20 and story['frames'][-1]['at'] >= 180
    assert story['after_comparison']['same'] and story['before_comparison']['different']
    assert 'post-deadline' in select(gone=True)
    with Image.open(io.BytesIO(dish_story.render(item(), story))) as im:
        assert im.width == 1080
    assert 'patch' not in dish_story.metadata(story)['frames'][0]


def test_no_before_faked_when_reference_is_already_present():
    assert 'Arrival unknown' in select(arrived=False)


def test_body_covering_spot_is_not_an_arrival():
    history = History(covered=True)
    # Only the body changes pixels. The actual dish is already present.
    def decode(t):
        return t, image(not 20<=t<=60)
    result = dish_story.select(history, None, item(), now=239, decode=decode)
    assert isinstance(result, str) and 'Arrival unknown' in result


def test_lighting_shift_alone_is_not_an_arrival():
    assert 'Arrival unknown' in select(arrived=False, light=True)
    a, b = [dish_story.patch(image(True, v), BOX) for v in (0,20)]
    assert not dish_story.comparison(a,b)['different']


def test_journal_gap_does_not_invent_a_visit_or_before():
    history = History()
    history.observations = [f for f in history.observations if not 19<=f['at']<=61]
    assert 'Arrival unknown' in select(history)


def test_jobs_preserve_names_membership_and_proof_checks(store, media, monkeypatch):
    now = datetime.now(timezone.utc)
    dish = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',
                                   at=now-timedelta(hours=3),box=BOX)
    # Use real schema: null reason must not block the next pending dish.
    second = store.create_obligation(None,[{'kind':'cup','quantity':1}],candidate=True,source='vision',
                                     at=now-timedelta(hours=3),box=BOX)
    worker = Stories(store,media,History())
    before = dict(store.conn.execute('SELECT * FROM obligations WHERE id=?',(dish['id'],)).fetchone())
    memberships = list(store.conn.execute('SELECT * FROM arrival_visit_members'))
    from wash_dishes.proofs import Proofs
    Proofs(store.root,store,media)
    proof = list(store.conn.execute('SELECT * FROM proofs'))
    found = select()
    monkeypatch.setattr(dish_story,'select',lambda *a,**kw:found)
    assert worker.generate(dish['id'],now=now)['status']=='saved'
    assert worker.candidates(now=now)==[second['id']]
    assert worker.generate(dish['id'],now=now)['status']=='saved'
    assert dict(store.conn.execute('SELECT * FROM obligations WHERE id=?',(dish['id'],)).fetchone()) == before
    assert list(store.conn.execute('SELECT * FROM arrival_visit_members'))==memberships
    assert list(store.conn.execute('SELECT * FROM proofs'))==proof
    assert store.conn.execute("SELECT count(*) FROM evidence WHERE label='dish story'").fetchone()[0]==1


def test_no_evidence_row_for_unverified_after(store,media,monkeypatch):
    now = datetime.now(timezone.utc)
    dish = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',
                                   at=now-timedelta(hours=3),box=BOX)
    worker = Stories(store,media,History())
    monkeypatch.setattr(dish_story,'select',lambda *a,**kw:'No clear post-deadline view verifies the reference dish at this spot.')
    assert worker.generate(dish['id'],now=now)['status']=='unavailable'
    assert not store.obligation(dish['id'])['evidence']


def test_wrists_rank_actual_target_contact_without_asserting_use(monkeypatch):
    def pose(jpeg, box, people):
        return {'available':True,'person_boxes':[], 'wrists':[[.44,.63,.9]] if jpeg==image(True) else []}
    story = dish_story.select(History(),None,item(),now=239,
                             decode=lambda t:(t,image(t>=45)),pose=pose)
    assert isinstance(story,dict)
    assert story['frames'][1]['pose']['wrists'] and story['frames'][2]['pose']['wrists']
    captured = []
    monkeypatch.setattr(dish_story.evidence_image,'render',lambda title,sub,panels,notes:captured.extend(panels))
    dish_story.render(item(),story)
    assert all('using' not in p['label'].lower() and 'cooking' not in p['label'].lower() for p in captured)


def test_untimed_baseline_uses_observation_followup_and_does_not_stall_queue(store,media,monkeypatch):
    now = datetime.now(timezone.utc)
    dish = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='baseline',
                                   at=now-timedelta(hours=3),box=BOX,timing_known=False)
    assert dish['due_at'] is None and dish['followup_at']
    worker = Stories(store,media,History())
    monkeypatch.setattr(dish_story,'select',lambda *a,**kw:'Arrival unknown: no change.')
    assert worker.run_once(now=now)==dish['id']
    assert worker.candidates(now=now)==[]


def test_changed_deadline_cannot_publish_a_stale_story(store,media,monkeypatch):
    now = datetime.now(timezone.utc)
    dish = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',
                                   at=now-timedelta(hours=3),box=BOX)
    worker = Stories(store,media,History())
    found = select()
    def changed(*a,**kw):
        with store.conn:
            store.conn.execute('UPDATE obligations SET due_at=? WHERE id=?',(stamp(now+timedelta(hours=2)),dish['id']))
        return found
    monkeypatch.setattr(dish_story,'select',changed)
    result = worker.generate(dish['id'],now=now)
    assert result['status']=='retry' and not result['evidence_id']
    assert not store.obligation(dish['id'])['evidence']
    assert not list(media.root.glob('media/dish-story*'))


def test_fragment_inside_larger_dish_cannot_supply_arrival_reference():
    history = History()
    for f in history.observations:
        f['detections'].append({'kind':'pan','score':.9,'box':[.35,.55,.2,.2]})
    assert 'fragment' in select(history)


def test_partial_occlusion_does_not_make_an_existing_dish_absent():
    ref = dish_story.patch(image(True),BOX)
    before = ref.copy()
    before[:, :24] = 30
    assert dish_story.partly_visible(before,ref)
    assert not dish_story.partly_visible(dish_story.patch(image(False),BOX),ref)


def test_taken_away_before_deadline_gets_no_story_even_if_similar_dish_returns():
    data = item() | {'lifecycle':'cleared'}
    assert 'before its deadline' in dish_story.select(History(),None,data,now=239,
                                                   decode=lambda t:(t,image(t>=45)))


def test_backfill_is_read_only_by_default_and_resumes_without_touching_names(store,monkeypatch):
    from wash_dishes import backfill_stories
    now = datetime.now(timezone.utc)
    items = [store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',
                                    at=now-timedelta(hours=3),box=BOX) for _ in range(3)]
    resident = store.add_resident('Synthetic reviewer')
    store.action(items[0]['id'],'confirm',resident_id=resident['id'])
    import json
    member_ids = [i['id'] for i in items]
    capture = {'visit_id':'preserved-visit','obligation_ids':member_ids,
               'canonical_obligation_ids':member_ids,'duplicate_of':{},'frames':[],
               'window':{'arrival_recorded':False}}
    with store.conn:
        store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?)',
                           ('preserved-visit',None,stamp(now),json.dumps(capture)))
        store.conn.executemany('INSERT INTO arrival_visit_members VALUES(?,?)',
                               [(oid,'preserved-visit') for oid in member_ids])
    before = [tuple(r) for r in store.conn.execute('SELECT * FROM obligations ORDER BY id')]
    membership = [tuple(r) for r in store.conn.execute('SELECT * FROM arrival_visit_members')]
    schema = list(store.conn.execute('SELECT sql FROM sqlite_master ORDER BY name'))
    dry = backfill_stories.run(store.root,limit=1,now=now)
    assert dry['dry_run'] and dry['ids']==[items[0]['id']] and dry['next_after_id']==items[0]['id']
    assert list(store.conn.execute('SELECT sql FROM sqlite_master ORDER BY name'))==schema
    monkeypatch.setattr(backfill_stories.os,'nice',lambda n:None)
    monkeypatch.setattr(dish_story,'select',lambda *a,**kw:'Arrival unknown: no verified change.')
    first = backfill_stories.run(store.root,apply=True,limit=1,now=now)
    second = backfill_stories.run(store.root,apply=True,limit=1,after_id=first['next_after_id'],now=now)
    assert first['results'][0]['obligation_id']==items[0]['id']
    assert second['results'][0]['obligation_id']==items[1]['id']
    assert [tuple(r) for r in store.conn.execute('SELECT * FROM obligations ORDER BY id')]==before
    assert [tuple(r) for r in store.conn.execute('SELECT * FROM arrival_visit_members')]==membership
    assert store.obligation(items[0]['id'])['resident_id']==resident['id']


def test_decoded_after_must_itself_be_past_deadline():
    history = History()
    history.bounds = lambda:(0,180)
    result = dish_story.select(history,None,item(),now=180,
                              decode=lambda t:(t-.5,image(t>=45)))
    assert 'post-deadline' in result


def test_colour_cast_can_match_only_with_strong_spatial_agreement():
    import cv2
    import numpy as np
    original = dish_story.patch(image(True), BOX)
    lab = cv2.cvtColor(original.astype(np.float32)/255, cv2.COLOR_BGR2LAB)
    lab += np.array([0, 7, 7], dtype=np.float32)
    shifted = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)*255, 0, 255).astype(np.uint8)
    match = dish_story.comparison(original, shifted)
    assert match['difference'] > .16
    assert match['correlation'] >= .9 and match['same']
    assert not match['different']
    assert not dish_story.comparison(original, dish_story.patch(image(False), BOX))['same']


def test_later_return_cannot_rescue_dish_gone_at_deadline():
    history = History()
    history.observations += [{'at': float(t), 'detections': []} for t in range(240, 4001)]
    def decode(at):
        return at, image(45 <= at < 180 or at >= 3000)
    result = dish_story.select(history, None, item(), now=4000, decode=decode)
    assert isinstance(result, str) and 'post-deadline' in result


def test_motion_proposals_keep_temporal_coverage_and_are_bounded():
    from wash_dishes.story_use import proposal_times, rank
    samples = [{'at':float(t),'motion':100 if t<15 else 1} for t in range(100)]
    proposed = proposal_times(samples,0,99)
    assert len(proposed)<=12
    assert all(any(lo<=at<lo+25 for at in proposed) for lo in (0,25,50,75))
    assert rank({'pose':{'wrists':[[.4,.5,.9]]}}) > rank({'pose':{'wrists':[[.4,.5,.4]]},'spot_motion':100})
    assert rank({'pose':{'wrists':[[.4,.5,.4]]}}) > rank({'dish_visible':True,'spot_motion':100})
