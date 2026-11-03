import json
from datetime import datetime, timedelta, timezone

import pytest

from wash_dishes import learning, learning_jobs
from wash_dishes.store import stamp, utcnow


def setup(store, task='present', owner=None, at=None, suffix=''):
    people = store.residents()
    if not people:
        people = [store.add_resident(n) for n in ('A', 'B', 'C', 'D')]
    item = store.create_obligation(owner, [{'kind': 'pan', 'quantity': 1}], at=at or utcnow())
    eid = f"e-{item['id']}{suffix}"
    store.add_evidence(item['id'], eid, eid + '.jpg', 'snapshot', 'Dish left unattended', at=at or utcnow())
    with store.conn:
        q = learning.add_question(store, source=eid+task, task=task, evidence_id=eid, obligation_id=item['id'], box=[.1, .2, .3, .4])
    return q, [p['id'] for p in people]


def label(store, q, person, answer='yes', value=None):
    with store.conn:
        return learning.answer(store, q, person, answer, value, require_ask=False)


def test_two_independent_residents_operator_and_conflict(store):
    q, people = setup(store)
    assert label(store, q, people[0])['state'] == 'pending'
    with pytest.raises(ValueError):
        label(store, q, people[0])
    assert not learning.truth(store)
    assert label(store, q, people[1])['state'] == 'trusted'
    assert len(learning.truth(store)) == 1
    conflict = label(store, q, people[2], 'no')
    assert conflict['state'] == 'conflict' and not learning.truth(store)
    resolution = label(store, q, None, 'yes')
    assert resolution['state'] == 'trusted'
    with store.conn:
        learning.undo(store, resolution['id'], None)
    assert not learning.truth(store)


def test_own_exculpation_never_confirms_itself_and_new_owner_invalidates(store):
    q, p = setup(store)
    with store.conn:
        store.conn.execute('UPDATE obligations SET resident_id=? WHERE id=(SELECT obligation_id FROM learning_questions WHERE id=?)', (p[0], q))
    with pytest.raises(ValueError, match='independent'):
        label(store, q, p[0], 'no')
    label(store, q, p[1], 'no')
    assert not learning.truth(store)
    label(store, q, p[2], 'no')
    assert learning.truth(store)
    with store.conn:
        store.conn.execute('UPDATE obligations SET resident_id=? WHERE id=(SELECT obligation_id FROM learning_questions WHERE id=?)', (p[1], q))
    assert not learning.truth(store)


def test_identity_self_claim_not_ownership_negative_does_not_identify_someone_else(store):
    q, p = setup(store, task='identity')
    claim = label(store, q, p[0], 'me')
    assert claim['state'] == 'trusted'
    assert learning.truth(store)[0]['value']['resident_id'] == p[0]
    label(store, q, p[1], 'not_me')
    assert learning.truth(store)
    with store.conn:
        learning.undo(store, claim['id'], p[0])
    assert not learning.truth(store)


def test_undo_preserves_audit_and_removes_export(store):
    q, p = setup(store)
    saved = label(store, q, p[0])
    label(store, q, p[1])
    with pytest.raises(ValueError):
        learning.undo(store, saved['id'], p[2])
    with store.conn:
        learning.undo(store, saved['id'], p[0])
    assert not learning.truth(store)
    assert store.conn.execute('SELECT undone_at FROM learning_labels WHERE id=?', (saved['id'],)).fetchone()[0]
    assert label(store, q, p[0])['state'] == 'trusted'


def test_box_validation_and_unsure(store):
    q, p = setup(store, task='missed')
    for box in ([0, 0, 2, 1], [0, 0, float('nan'), .1], [0, 0, 0, 0]):
        with pytest.raises(ValueError):
            label(store, q, p[0], 'box', {'box': box, 'kind': 'cup'})
    assert label(store, q, p[0], 'unsure')['state'] == 'pending'
    assert not learning.truth(store)


def test_active_learning_excludes_every_stake_and_caps_reservations(store):
    q, p = setup(store)
    with store.conn:
        store.conn.execute('UPDATE learning_questions SET interested=?', (json.dumps([p[0]]),))
        for _ in range(18):
            setup(store)
        queue = learning.queue(store, p[0], limit=50)
    assert q not in [r['id'] for r in queue]
    assert len(queue) == learning.DAILY_CAP
    assert len(learning.queue(store, p[0], limit=50)) == learning.DAILY_CAP
    for row in queue:
        assert p[0] not in learning.interested(store, row)


def test_time_split_never_reuses_reserved_truth_even_on_later_nights():
    day = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [{'id': i, 'obligation_id': i, 'evidence_id': str(i), 'captured_at': stamp(day + timedelta(days=i))} for i in range(30)]
    previously_held = set()
    for night in range(5, 30):
        train, test = learning.split(rows[:night])
        assert not ({r['id'] for r in train} & previously_held)
        previously_held.update(r['id'] for r in test)
        assert not ({r['id'] for r in train} & {r['id'] for r in test})
        if train and test:
            assert max(r['captured_at'] for r in train) < min(r['captured_at'] for r in test)


def test_scheduler_exclusive_busy_claim_and_gate(store, monkeypatch):
    monkeypatch.setattr(learning, 'truth', lambda s: [{'id':1,'label_id':1}, {'id':2,'label_id':2}])
    monkeypatch.setattr(learning, 'split', lambda r: (r[:1], r[1:]))
    assert learning_jobs.run(store, busy=lambda:True)['status'] == 'skipped_busy'
    assert learning_jobs.run(store, busy=lambda:False)['status'] == 'already_ran'
    with learning_jobs.exclusive(store.root):
        assert learning_jobs.run(store, busy=lambda:False, day='next')['status'] == 'locked'
    assert not learning_jobs.gate([1]*30,[1]*30)[0]
    assert learning_jobs.gate([0]*30,[1]*30)[0]
    assert not learning_jobs.gate([0]*19,[1]*19)[0]


def test_candidate_adoption_readback_regression_and_rollback(store, monkeypatch):
    rows = [{'id':1,'label_id':1}, {'id':2,'label_id':2}]
    monkeypatch.setattr(learning, 'truth', lambda s:rows)
    monkeypatch.setattr(learning, 'split', lambda r:(r[:1],r[1:]))
    target = store.root / learning_jobs.ARTIFACTS['probe']
    target.parent.mkdir(exist_ok=True)
    target.write_text('old')
    candidate = store.root / 'candidate.json'
    candidate.write_text('new')
    class Adapter:
        def __call__(self, train, test):
            assert not set(r['id'] for r in train) & set(r['id'] for r in test)
            return {'candidate':str(candidate),'before':[0]*30,'after':[1]*30}
        def verify(self, path, test):
            assert path.read_text() == 'new'
            return [1]*30
    assert learning_jobs.run(store,busy=lambda:False,adapters={'probe':Adapter()})['status'] == 'adopted'
    assert target.read_text() == 'new'
    learning_jobs.rollback(store,'probe')
    assert target.read_text() == 'old'
    class Bad(Adapter):
        def verify(self,path,test):
            return [0]*30
    result = learning_jobs.run(store,busy=lambda:False,adapters={'probe':Bad()},day='next')
    assert result['details']['models']['probe']['status'] == 'failed'
    assert target.read_text() == 'old'


def test_quiet_camera_heartbeat_fails_closed_and_requires_a_minute(store, monkeypatch):
    from wash_dishes import learning_state
    now = utcnow()
    monkeypatch.setattr(learning_state, 'utcnow', lambda: now)
    assert learning_state.camera_busy(store.root, active=True)
    assert not learning_state.camera_busy(store.root, active=False)
    learning_state.note(store.root, stamp(now), 0, loaded=True)
    assert learning_state.camera_busy(store.root, active=True)
    for _ in range(7):
        now += timedelta(seconds=10)
        learning_state.note(store.root, stamp(now), 0, loaded=True)
    assert not learning_state.camera_busy(store.root, active=True)
    learning_state.note(store.root, stamp(now), 1, loaded=True)
    assert learning_state.camera_busy(store.root, active=True)
    now += timedelta(seconds=31)
    assert learning_state.camera_busy(store.root, active=True)
    learning_state.note(store.root, stamp(now), 0, loaded=True)
    assert learning_state.camera_busy(store.root, active=True)


def test_busy_between_models_and_undo_during_training_prevent_adoption(store, monkeypatch):
    rows = [{'id':1,'label_id':1}, {'id':2,'label_id':2}]
    monkeypatch.setattr(learning, 'truth', lambda s:rows)
    monkeypatch.setattr(learning, 'split', lambda r:(r[:1],r[1:]))
    candidate=store.root/'new.json'
    candidate.write_text('new')
    def adapter(train,test):
        rows.clear()
        return {'candidate':str(candidate),'before':[0]*30,'after':[1]*30}
    result=learning_jobs.run(store,busy=lambda:False,adapters={'probe':adapter})
    assert result['details']['models']['probe']['status']=='failed'
    assert not (store.root/learning_jobs.ARTIFACTS['probe']).exists()


def test_later_truth_can_automatically_restore_previous_model(store, monkeypatch):
    rows=[{'id':1,'label_id':1},{'id':2,'label_id':2}]
    monkeypatch.setattr(learning,'truth',lambda s:rows)
    monkeypatch.setattr(learning,'split',lambda r:(r[:1],r[1:]))
    target=store.root/learning_jobs.ARTIFACTS['probe']
    target.parent.mkdir(exist_ok=True)
    target.write_text('regressed')
    target.with_name(target.name+'.learning-previous').write_text('previous')
    class Adapter:
        def __call__(self,train,test):
            return {'candidate':str(target),'before':[0]*30,'after':[0]*30}
        def verify(self,path,test):
            return [int(path.read_text()=='previous')]*30
    result=learning_jobs.run(store,busy=lambda:False,adapters={'probe':Adapter()})
    assert result['details']['models']['probe']['status']=='rolled_back'
    assert target.read_text()=='previous'


def test_presence_and_kind_contradictions_are_excluded_and_surfaced(store):
    q, p=setup(store)
    raw=learning.question(store,q)
    with store.conn:
        other=learning.add_question(store,source='other-kind',task='kind',evidence_id=raw['evidence_id'],obligation_id=raw['obligation_id'],box=[.1,.2,.3,.4])
    label(store,q,None,'no')
    label(store,other,None,'pan')
    assert not learning.truth(store)
    assert learning.verdict(store,raw)['state']=='conflict'


def test_same_dish_question_excludes_owners_of_both_visits(store):
    q,p=setup(store)
    second=store.create_obligation(p[1],[{'kind':'pan','quantity':1}])
    original=learning.question(store,q)
    with store.conn:
        pair=learning.add_question(store,source='pair',task='same',evidence_id=original['evidence_id'],other_evidence_id=original['evidence_id'],obligation_id=original['obligation_id'],other_obligation_id=second['id'])
    with pytest.raises(ValueError,match='independent'):
        label(store,pair,p[1],'same')


def test_training_gallery_contains_no_evaluation_row(store, monkeypatch):
    from wash_dishes import identity_fit
    q, people=setup(store,task='identity')
    item=learning.question(store,q)
    label(store,q,None,'me',people[0])
    train=learning.truth(store)
    with store.conn:
        store.conn.execute("INSERT INTO person_samples(obligation_id,created_at,embedding,evidence_id,cue) VALUES(?,?,?,?,'outfit')", (item['obligation_id'],stamp(), '[1,0]',item['evidence_id']))
        # A later capture on the SAME obligation is not the picture that was labelled.
        store.add_evidence(item['obligation_id'],'later-crop','later.jpg','snapshot','Person crop')
        store.conn.execute("INSERT INTO person_samples(obligation_id,created_at,embedding,evidence_id,cue) VALUES(?,?,?,?,'outfit')", (item['obligation_id'],stamp(), '[0,1]','later-crop'))
        # A held-out name must never enter the staged trainer database.
        reserved, _ = setup(store,task='identity')
    label(store,reserved,None,'me',people[1])
    held=learning.truth(store)[1:]
    held=[r|{'evidence_id':str(i)} for i in range(20) for r in held]
    seen=[]
    def refit(staged):
        seen.extend(r[0] for r in staged.conn.execute('SELECT id FROM obligations'))
        assert seen==[item['obligation_id']]
        assert [r[0] for r in staged.conn.execute('SELECT embedding FROM person_samples')]==['[1,0]']
        assert not staged.conn.execute('SELECT 1 FROM learning_labels').fetchone()
        raise ValueError('Intentional stop after inspecting isolated training store')
    monkeypatch.setattr(identity_fit,'refit',refit)
    with pytest.raises(ValueError,match='Intentional stop'):
        learning_jobs.Candidate(store,store.root,'identity')(train,held)
    assert seen==[item['obligation_id']]


def test_dish_questions_never_use_the_latest_person_crop(store):
    q, _=setup(store)
    original=learning.question(store,q)
    oid=original['obligation_id']
    store.add_evidence(oid,'person-later','person.jpg','snapshot','Person shown in arrival visit',at=utcnow()+timedelta(seconds=10),capture={'identity_source':'arrival_visit'})
    with store.conn:
        store.conn.execute("UPDATE obligations SET source='vision' WHERE id=?", (oid,))
        learning.seed(store)
        learning.capture_action(store,oid,'verdict',{'verdict':'not_dish'},None)
    assert all(r['evidence_id']!='person-later' for r in store.conn.execute("SELECT * FROM learning_questions WHERE task IN ('present','kind','missed')"))
