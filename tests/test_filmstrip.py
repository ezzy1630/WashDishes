import io
from datetime import timedelta

from PIL import Image
from test_journal import BOX, DISH, PERSON, frame
from test_sequences import jpeg

from wash_dishes import filmstrip
from wash_dishes.filmstrip import Filmstrips, arrival_window, select_frames
from wash_dishes.journal import Journal
from wash_dishes.store import parse_stamp, utcnow


def test_arrival_retains_bodies_that_hide_the_putdown_and_several_people():
    other = PERSON | {'track_id': 2}
    frames = ([frame(i) for i in range(0, 20)] +
              [frame(i, people=[PERSON if i < 80 else other]) for i in range(20, 180)] +
              [frame(i, dish=True) for i in range(180, 200)])
    window = arrival_window(iter(frames), BOX, 199, family='vessel')
    assert window['before'] == 19 and window['appeared'] == 180
    selected = select_frames(iter(frames), BOX, window)
    assert len(selected) == 6 and selected == sorted(selected, key=lambda f: f['at'])
    assert not selected[0]['person_boxes'] and not selected[-1]['person_boxes']
    assert sum(bool(f['person_boxes']) for f in selected) == 4


def test_flicker_does_not_replace_real_arrival_with_later_detector_return():
    frames = [frame(i, dish=i>=20 and not 70<=i<=74) for i in range(100)]
    window = arrival_window(iter(frames), BOX, 99, family='vessel')
    assert window['appeared'] == 20


def test_gap_and_already_present_do_not_invent_empty_spot():
    assert isinstance(arrival_window([frame(i, dish=True) for i in range(100)], BOX, 99), str)
    frames = [frame(i) for i in range(20)] + [frame(i, dish=True) for i in range(70, 100)]
    assert isinstance(arrival_window(frames, BOX, 99), str)


def test_window_uses_episode_near_logged_sighting_and_ignores_later_flicker():
    frames = [frame(i, dish=10<=i<50 or 90<=i<99) for i in range(110)]
    window = arrival_window(frames, BOX, 109)
    assert window['appeared'] == 90


def test_filmstrip_saved_once_with_real_frame_times_boxes_and_seven_day_expiry(store, media, monkeypatch):
    created = utcnow()-timedelta(hours=3)
    item = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',at=created,box=BOX)
    start = created.timestamp()-60
    with Journal(store.root/'journal.db') as journal:
        for i in range(100):
            journal.record(start+i, ([DISH] if i>=50 else []) + ([PERSON] if 20<=i<50 else []))
        monkeypatch.setattr(filmstrip.putdown, 'segments', lambda _: [(start-1,start+100,store.root/'source.ts')])
        monkeypatch.setattr(filmstrip.putdown, 'decoded_photos', lambda segment, **kw: [(kw['t0']+.1, jpeg((80,90,100)))])
        strips = Filmstrips(store,media,journal)
        saved = strips.generate(item['id'])
        assert saved['status'] == 'saved'
        assert strips.generate(item['id'])['evidence_id'] == saved['evidence_id']
        evidence = store.evidence(saved['evidence_id'])
        assert evidence['kind'] == 'filmstrip' and evidence['label'] == 'arrival'
        assert parse_stamp(evidence['expires_at'])-parse_stamp(evidence['created_at']) == timedelta(days=7)
        import json
        frames = json.loads(evidence['capture'])['frames']
        assert len(frames) == 2
        # Identical synthetic gray source frames cannot establish an absent-to-arrival change.
        assert json.loads(evidence['capture'])['window']['arrival_recorded'] is False
        assert len({f['at'] for f in frames}) == 2
        picture = media.safe_path(evidence['filename']).read_bytes()
        with Image.open(io.BytesIO(picture)) as image:
            assert image.width == 1080 and image.height < 1668
        assert len(picture) <= 350000
        assert store.conn.execute("SELECT count(*) FROM evidence WHERE kind='filmstrip'").fetchone()[0] == 1


def test_missing_footage_stores_plain_reason_without_a_fake_evidence_row(store, media):
    item = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',box=BOX)
    with Journal(store.root/'journal.db') as journal:
        strips = Filmstrips(store,media,journal)
        assert strips.generate(item['id'])['status'] == 'unavailable'
        assert 'footage' in strips.generate(item['id'])['reason']
        assert store.obligation(item['id'])['evidence'] == []


def test_same_visit_requires_continuous_same_person_at_adjacent_spots(store):
    from wash_dishes.filmstrip import same_visit
    def planned(first,last,box=BOX):
        return {'box':box,'frames':[{'at':first,'person_boxes':[PERSON['box']]},{'at':last,'person_boxes':[PERSON['box']]}]}
    with Journal(store.root/'journal.db') as journal:
        for i in range(10,51):
            journal.record(i,[PERSON])
        assert same_visit(journal,planned(10,25),planned(30,50,[.5,.6,.08,.07]))
        assert not same_visit(journal,planned(10,25),planned(30,50,[.85,.6,.08,.07]))
        journal.record(28,[])
        assert not same_visit(journal,planned(10,25),planned(30,50))
        journal.record(28,[PERSON|{'track_id':2}])
        assert not same_visit(journal,planned(10,25),planned(30,50))


def test_three_dishes_from_one_visit_share_one_evidence_and_current_consensus_guess(store,media,monkeypatch):
    import json
    created = utcnow()-timedelta(hours=3)
    items=[store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',at=created+timedelta(seconds=i),box=[.4+i*.03,.6,.04,.04]) for i in range(3)]
    start=created.timestamp()-60
    with Journal(store.root/'journal.db') as journal:
        for i in range(100):
            journal.record(start+i,[PERSON] if 10<=i<=80 else [])
        def planned(journal,buffer,item,**kw):
            return {'box':item['box'],'window':{'before':start,'after':start+99},'frames':[
                {'at':start+i,'person_boxes':[PERSON['box']] if 10<=i<=80 else []} for i in (0,20,30,40,60,99)]}
        monkeypatch.setattr(filmstrip,'plan',planned)
        monkeypatch.setattr(filmstrip.putdown,'segments',lambda _: [(start-1,start+101,store.root/'source.ts')])
        monkeypatch.setattr(filmstrip.putdown,'decoded_photos',lambda segment,**kw:[(kw['t0']+.1,jpeg((80,90,100)))])
        strips=Filmstrips(store,media,journal)
        result=strips.generate(items[0]['id'])
        assert result['status']=='saved'
        rows=store.conn.execute("SELECT * FROM evidence WHERE kind='filmstrip'").fetchall()
        assert len(rows)==1
        metadata=json.loads(rows[0]['capture'])
        assert metadata['obligation_ids']==[i['id'] for i in items]
        assert len(metadata['dish_boxes'])==3
        for item in items:
            saved=store.obligation(item['id'])
            assert saved['arrival_visit_id']==metadata['visit_id']
            assert saved['evidence'][0]['id']==rows[0]['id']
            assert strips.generate(item['id'])['evidence_id']==rows[0]['id']
        alex=store.add_resident('Alex')['id']
        sam=store.add_resident('Sam')['id']
        for item in items:
            store.set_suggestion(item['id'],alex,1.0,{'outfit_today':.99})
        assert store.arrival_visit(metadata['visit_id'])['suggested_resident_id']==alex
        store.set_suggestion(items[1]['id'],sam,1.0,{'outfit_today':.99})
        assert store.arrival_visit(metadata['visit_id'])['suggested_resident_id'] is None
        store.set_suggestion(items[1]['id'],None,None)
        assert store.arrival_visit(metadata['visit_id'])['suggested_resident_id'] is None
        # Ordinary responsibility review cannot extend shared context past its seven-day retention.
        store.action(items[0]['id'],'confirm',resident_id=alex)
        evidence=store.evidence(rows[0]['id'])
        assert parse_stamp(evidence['expires_at'])-parse_stamp(evidence['created_at'])==timedelta(days=7)


def test_coexisting_slivers_and_overlapping_records_share_one_physical_dish_slot(store):
    from wash_dishes.filmstrip import duplicate_clusters
    from wash_dishes.store import stamp
    created=utcnow()-timedelta(hours=4)
    def item(index,box,kind='pan',hours=0):
        return {'id':index,'box':box,'dishes':[{'kind':kind,'quantity':1}],
                'created_at':stamp(created+timedelta(hours=hours)), 'last_seen_at':stamp(created+timedelta(hours=3+hours))}
    whole=item(1,[.30,.69,.09,.08])
    rim=item(2,[.32,.70,.04,.015],'bowl')
    side=item(3,[.30,.71,.015,.05],'pot')
    adjacent=item(4,[.46,.69,.08,.08])
    later=item(5,whole['box'],hours=5)
    groups=duplicate_clusters([whole,rim,side,adjacent,later])
    assert [[i['id'] for i in g] for g in groups]==[[1,2,3],[4],[5]]


def test_journal_gap_after_an_arrival_does_not_reuse_its_before_frame():
    frames=[frame(i,dish=i>=20) for i in range(40)]+[frame(i) for i in range(60,70)]
    assert isinstance(arrival_window(frames,BOX,69),str)


def test_a_nearby_visit_cannot_replace_an_already_present_dish_arrival(store,media,monkeypatch):
    """An oversized pan detection still vetoes the claimed empty-before frame."""
    created=utcnow()-timedelta(hours=3)
    item=store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,source='vision',at=created,box=BOX)
    start=created.timestamp()-80
    with Journal(store.root/'journal.db') as journal:
        huge=DISH|{'box':[.32,.52,.26,.28]}
        for i in range(130):
            journal.record(start+i,[huge]+([PERSON] if 30<=i<60 else []))
        monkeypatch.setattr(filmstrip.putdown,'segments',lambda _: [(start-1,start+140,store.root/'source.ts')])
        monkeypatch.setattr(filmstrip.putdown,'decoded_photos',lambda segment,**kw:[(kw['t0']+.5,jpeg((80,90,100)))])
        result=filmstrip.plan(journal,media.buffer,item)
        assert result['window']['arrival_recorded'] is False
        assert result['window']['source']=='already_present'
        assert 'arrival not recorded' in result['window']['reason']


def test_partial_boxes_collapse_but_a_nested_complete_pot_does_not(store):
    from wash_dishes.filmstrip import duplicate_clusters
    from wash_dishes.store import stamp
    start=utcnow()-timedelta(hours=4)
    boxes=[(.307,.698,.083,.072),(.325,.708,.038,.016),(.265,.700,.125,.090),(.308,.713,.015,.055),(.276,.733,.048,.051)]
    items=[{'id':i,'box':list(b),'dishes':[{'kind':k,'quantity':1}], 'created_at':stamp(start),
            'last_seen_at':stamp(start+timedelta(hours=3))} for i,(b,k) in enumerate(zip(boxes,['pan','bowl','pan','pot','pan']))]
    groups=duplicate_clusters(items)
    assert [[m['id'] for m in g] for g in groups]==[[0,1,2,3],[4]]


def test_short_filmstrip_fits_its_frames_and_uses_housemate_copy(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from PIL import ImageDraw

    start = datetime(2026, 11, 2, 11, 14, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    item = {'dishes': [{'kind': 'plate', 'quantity': 1}], 'location': 'Dining table', 'box': BOX}
    frames = [{'at': start + i * 120, 'jpeg': jpeg((80, 90, 100)), 'person_boxes': []} for i in range(4)]
    text = []
    original = ImageDraw.ImageDraw.text
    def track(self, xy, value, *args, **kwargs):
        text.append(value)
        return original(self, xy, value, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', track)
    picture = filmstrip.render(item, frames)
    with Image.open(io.BytesIO(picture)) as image:
        assert image.width == 1080 and image.height < 1177
    assert 'Dishes to check' in text
    assert 'Nov 2 · Dining table' in text
    assert not any('11:14–11:14' in t for t in text)
    assert not any('arrival context' in value or 'human review' in value for value in text)
    text.clear()
    filmstrip.render(item, frames, already_at=start)
    assert 'already here at 11:14 AM.' in ' '.join(text)
