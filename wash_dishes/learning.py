"""Auditable human teaching, independent consensus and bounded active review.

Labels never change league points. Model workers consume truth(), never raw answers.
Question geometry and interested parties are captured by the server, not the client.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta

from .store import parse_stamp, stamp, utcnow

KINDS = ('plate', 'bowl', 'cup', 'pan', 'pot', 'utensil', 'not_dish')
ANSWERS = {'present': ('yes', 'no'), 'kind': KINDS, 'same': ('same', 'different'),
           'identity': ('me', 'not_me', 'name', 'not_name'), 'missed': ('box',), 'frame': ('boxes',)}
DAILY_CAP = 12


def migrate(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS learning_questions(
          id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, task TEXT NOT NULL,
          evidence_id TEXT NOT NULL REFERENCES evidence(id), other_evidence_id TEXT REFERENCES evidence(id),
          obligation_id INTEGER REFERENCES obligations(id), other_obligation_id INTEGER REFERENCES obligations(id),
          captured_at TEXT NOT NULL, box TEXT, interested TEXT NOT NULL, priority REAL NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS learning_labels(
          id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES learning_questions(id),
          actor_id INTEGER REFERENCES residents(id), answer TEXT NOT NULL, value TEXT NOT NULL,
          source TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT, undone_by INTEGER);
        CREATE INDEX IF NOT EXISTS learning_label_question ON learning_labels(question_id);
        CREATE TABLE IF NOT EXISTS learning_asks(
          question_id INTEGER NOT NULL REFERENCES learning_questions(id), actor_id INTEGER NOT NULL REFERENCES residents(id),
          day TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(question_id,actor_id));
        CREATE TABLE IF NOT EXISTS learning_runs(
          id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS learning_schedule(day TEXT PRIMARY KEY, run_id INTEGER);
    ''')


def add_question(store, *, source, task, evidence_id, obligation_id=None, box=None,
                 other_evidence_id=None, other_obligation_id=None, priority=1, reason='A second look helps'):
    if task not in ANSWERS:
        raise ValueError('Unknown teaching question.')
    evidence = store.evidence(evidence_id)
    if not evidence:
        raise ValueError('The teaching photo is missing.')
    interested = set()
    for oid in (obligation_id, other_obligation_id):
        if oid:
            item = dict(store.conn.execute('SELECT * FROM obligations WHERE id=?', (oid,)).fetchone())
            interested.update(item.get(k) for k in ('resident_id', 'original_resident_id', 'suggested_resident_id', 'dispute_by', 'named_by'))
    interested.discard(None)
    store.conn.execute('''INSERT OR IGNORE INTO learning_questions
        (source,task,evidence_id,other_evidence_id,obligation_id,other_obligation_id,captured_at,box,interested,priority,reason,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (source, task, evidence_id, other_evidence_id, obligation_id, other_obligation_id, evidence['created_at'],
         json.dumps(box) if box else None, json.dumps(sorted(interested)), priority, reason, stamp()))
    return store.conn.execute('SELECT id FROM learning_questions WHERE source=?', (source,)).fetchone()[0]


def interested(store, q):
    """Recheck current owners as well as the immutable question-time stake."""
    ids = set(json.loads(q['interested']))
    for oid in (q['obligation_id'], q['other_obligation_id']):
        if oid:
            row = store.conn.execute('SELECT resident_id,original_resident_id,suggested_resident_id,dispute_by,named_by FROM obligations WHERE id=?', (oid,)).fetchone()
            ids.update(v for v in row if v is not None)
    return ids


def question(store, qid):
    row = store.conn.execute('SELECT * FROM learning_questions WHERE id=?', (qid,)).fetchone()
    if not row:
        raise ValueError('This question is no longer available.')
    return dict(row)


def _verdict(store, q):
    rows = [dict(r) for r in store.conn.execute('SELECT * FROM learning_labels WHERE question_id=? AND undone_at IS NULL ORDER BY id', (q['id'],)) if r['answer'] != 'unsure']
    # An operator explicitly adjudicates a conflict. Undo restores all resident evidence.
    operators = [r for r in rows if r['actor_id'] is None]
    if operators:
        return {'state': 'trusted', 'label': operators[-1]}
    if q['task'] == 'identity':
        positives = [r for r in rows if r['answer'] in ('me', 'name')]
        negatives = [r for r in rows if r['answer'] in ('not_me', 'not_name')]
        values = {r['value'] for r in positives}
        denied = {json.loads(r['value'])['resident_id'] for r in negatives}
        if any(json.loads(r['value'])['resident_id'] in denied for r in positives):
            return {'state': 'conflict', 'label': None}
        rows = positives
    else:
        values = {r['value'] for r in rows}
    if len(values) > 1:
        return {'state': 'conflict', 'label': None}
    if not rows:
        return {'state': 'pending', 'label': None}
    stakes = interested(store, q)
    independent = {r['actor_id'] for r in rows if r['actor_id'] not in stakes}
    self_id = q['task'] == 'identity' and rows[0]['answer'] == 'me'
    # A self claim identifies a crop, never ownership, dish presence, or points.
    trusted = self_id or len(independent) >= 2
    return {'state': 'trusted' if trusted else 'pending', 'label': rows[0] if trusted else None}


def verdict(store, q):
    decision = _verdict(store, q)
    if decision['state'] != 'trusted' or q['task'] not in ('present', 'kind'):
        return decision
    presence = json.loads(decision['label']['value']) not in ('no', 'not_dish')
    for sibling in store.conn.execute("SELECT * FROM learning_questions WHERE evidence_id=? AND box IS ? AND task IN ('present','kind') AND id!=?", (q['evidence_id'], q['box'], q['id'])):
        other = _verdict(store, dict(sibling))
        if other['state'] == 'trusted' and (json.loads(other['label']['value']) not in ('no', 'not_dish')) != presence:
            return {'state':'conflict', 'label':None}
    return decision


def clean_answer(task, answer, value, actor_id):
    if answer == 'unsure':
        return None
    if answer not in ANSWERS[task]:
        raise ValueError('Choose an answer shown on the card.')
    if task == 'frame':
        if actor_id is not None or not isinstance(value, list) or len(value) > 300:
            raise ValueError('Complete frame labels require operator provenance.')
        return [clean_answer('missed', 'box', {'kind': 'utensil' if b.get('kind') in ('fork', 'knife', 'spoon') else b.get('kind'), 'box': b.get('box')}, None) | {'kind': b['kind']} for b in value]
    if task == 'missed':
        if not isinstance(value, dict) or set(value) != {'box', 'kind'} or value['kind'] not in KINDS[:-1]:
            raise ValueError('Draw a box and choose a dish kind.')
        box = value['box']
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError('A box needs four finite coordinates.')
        x, y, w, h = box
        if min(x, y) < 0 or min(w, h) < .005 or x + w > 1.00001 or y + h > 1.00001:
            raise ValueError('Keep the whole box inside the picture.')
        # Coarse grid permits two independent drags to agree without treating nearby dishes as exact matches.
        normalized = [round(v / .025) * .025 for v in box]
        normalized[2] = min(max(.025, normalized[2]), 1 - normalized[0])
        normalized[3] = min(max(.025, normalized[3]), 1 - normalized[1])
        return {'box': normalized, 'kind': value['kind']}
    if task == 'identity':
        if actor_id is None or answer in ('name', 'not_name'):
            if type(value) is not int:
                raise ValueError('Choose the resident shown in the person crop.')
            return {'resident_id': value, 'is_person': answer in ('me', 'name')}
        return {'resident_id': actor_id, 'is_person': answer in ('me', 'name')}
    return answer


def answer(store, qid, actor_id, answer, value=None, *, source='help', at=None, require_ask=True):
    q = question(store, qid)
    if actor_id is not None:
        store._resident(actor_id)
        if require_ask and not store.conn.execute('SELECT 1 FROM learning_asks WHERE question_id=? AND actor_id=?', (qid, actor_id)).fetchone():
            raise ValueError('Open this question before answering.')
        if actor_id in interested(store, q):
            raise ValueError('Someone independent must check a visit that could affect your points.')
    payload = clean_answer(q['task'], answer, value, actor_id)
    if q['task'] == 'identity' and payload:
        store._resident(payload['resident_id'])
    if store.conn.execute('SELECT 1 FROM learning_labels WHERE question_id=? AND actor_id IS ? AND undone_at IS NULL', (qid, actor_id)).fetchone():
        raise ValueError('Undo your previous answer before changing it.')
    cursor = store.conn.execute('INSERT INTO learning_labels(question_id,actor_id,answer,value,source,created_at) VALUES(?,?,?,?,?,?)',
                                (qid, actor_id, answer, json.dumps(payload, sort_keys=True), source, stamp(at)))
    return {'id': cursor.lastrowid, 'state': verdict(store, q)['state']}


def undo(store, label_id, actor_id):
    row = store.conn.execute('SELECT * FROM learning_labels WHERE id=?', (label_id,)).fetchone()
    if not row or (actor_id is not None and actor_id != row['actor_id']):
        raise ValueError('That answer does not belong to you.')
    store.conn.execute('UPDATE learning_labels SET undone_at=coalesce(undone_at,?),undone_by=? WHERE id=?', (stamp(), actor_id, label_id))
    return {'ok': True, 'question_id': row['question_id']}


def truth(store):
    result = []
    for row in store.conn.execute('SELECT * FROM learning_questions ORDER BY captured_at,id'):
        q = dict(row)
        if q['task'] == 'identity' and q['obligation_id'] and store.conn.execute("SELECT 1 FROM obligations WHERE id=? AND (lifecycle='disputed' OR dispute_state='open')", (q['obligation_id'],)).fetchone():
            continue
        decision = verdict(store, q)
        if decision['state'] != 'trusted':
            continue
        label = decision['label']
        result.append(q | {'label_id': label['id'], 'answer': label['answer'], 'value': json.loads(label['value']),
                           'labeled_at': label['created_at'], 'source_label': label['source']})
    return result


def seed(store):
    """Cheap retained-evidence queue. No image inference in API reads."""
    rows = store.conn.execute('''SELECT o.id,o.box,o.suggested_resident_id,e.id eid,s.score,
        (SELECT count(*) FROM dish_samples d WHERE d.obligation_id=o.id) recurrence
        FROM obligations o JOIN evidence e ON e.id=(SELECT id FROM evidence WHERE obligation_id=o.id
        AND kind='snapshot' AND expired=0 AND expires_at>?
        AND coalesce(json_extract(capture,'$.identity_source'),'')='' AND label NOT LIKE 'Person%'
        AND label!='Who put it down (from footage)' ORDER BY created_at DESC LIMIT 1)
        LEFT JOIN dish_samples s ON s.id=(SELECT max(id) FROM dish_samples WHERE obligation_id=o.id)
        WHERE o.source='vision' ORDER BY o.created_at DESC LIMIT 100''', (stamp(),)).fetchall()
    from . import review_evidence
    from .inventory import area_for
    probe = review_evidence.kitchen_probe(store.root)
    memories = [dict(r) for r in store.conn.execute('SELECT * FROM remembered_objects')]
    for memory in memories:
        memory['embedding'] = json.loads(memory['embedding'])
    areas = store.settings()['areas']
    for row in rows:
        box = json.loads(row['box']) if row['box'] else None
        uncertainty = 1 - abs((row['score'] if row['score'] is not None else .5) - .5) * 2
        priority = uncertainty * 3 + min(row['recurrence'], 5) * .2
        feature = store.conn.execute('SELECT embedding FROM dish_samples WHERE obligation_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
        signals = review_evidence.signals(feature[0] if feature else None, probe, memories, area_for(box, areas) if box else None)
        reason = 'Uncertain detection' if uncertainty > .6 else 'Recurring object'
        if feature and probe and not signals['dish_confident']:
            priority += 4
            reason = 'The detector and second look disagree'
        if signals['remembered_near_match']:
            priority += 3
            reason = 'A recurring object needs a check'
        for task in ('present', 'kind', 'missed'):
            add_question(store, source=f"evidence:{row['eid']}:{task}", task=task, evidence_id=row['eid'],
                         obligation_id=row['id'], box=box if task != 'missed' else None,
                         priority=priority + (task == 'present'), reason=reason)
    # Pair repeated overlapping spots for continuity teaching, without merging inventory.
    from .vision import box_iou
    for i, first in enumerate(rows[:30]):
        if not first['box']:
            continue
        for second in rows[i+1:30]:
            if second['box'] and box_iou(json.loads(first['box']), json.loads(second['box'])) > .7:
                add_question(store, source=f"same:{min(first['id'],second['id'])}:{max(first['id'],second['id'])}",
                             task='same', evidence_id=first['eid'], other_evidence_id=second['eid'],
                             obligation_id=first['id'], other_obligation_id=second['id'], priority=2,
                             reason='These recurring outlines overlap')
                break
    # Only anchored person samples, never a whole visit/filmstrip called a person crop.
    for row in store.conn.execute('''SELECT DISTINCT s.obligation_id,e.id FROM person_samples s JOIN evidence e ON e.id=s.evidence_id
        WHERE e.kind='snapshot' AND (e.label LIKE 'Person%' OR json_extract(e.capture,'$.identity_source') IN ('arrival_visit','putdown')) AND e.expired=0 AND e.expires_at>? LIMIT 40''', (stamp(),)):
        add_question(store, source=f"identity:{row['id']}", task='identity', evidence_id=row['id'], obligation_id=row['obligation_id'], priority=2, reason='A person crop needs a name')


def queue(store, actor_id, limit=6):
    now = utcnow()
    day = stamp(now)[:10]
    seed(store)
    used = store.conn.execute('SELECT count(*) FROM learning_asks WHERE actor_id=? AND day=?', (actor_id, day)).fetchone()[0]
    result = []
    for row in store.conn.execute('SELECT * FROM learning_questions ORDER BY priority DESC,id'):
        q = dict(row)
        if actor_id in interested(store, q) or verdict(store, q)['state'] in ('trusted', 'conflict'):
            continue
        if store.conn.execute('SELECT 1 FROM learning_labels WHERE question_id=? AND actor_id=? AND undone_at IS NULL', (q['id'], actor_id)).fetchone():
            continue
        evidence = store.evidence(q['evidence_id'])
        if not evidence or evidence['expired'] or parse_stamp(evidence['expires_at']) <= now:
            continue
        ask = store.conn.execute('SELECT day FROM learning_asks WHERE question_id=? AND actor_id=?', (q['id'], actor_id)).fetchone()
        if ask and ask['day'] != day:
            continue
        if not ask:
            if used >= DAILY_CAP:
                continue
            store.conn.execute('INSERT INTO learning_asks VALUES(?,?,?,?)', (q['id'], actor_id, day, stamp(now)))
            used += 1
        result.append(q | {'box': json.loads(q['box']) if q['box'] else None})
        if len(result) >= limit:
            break
    return result


def progress(store, actor_id):
    personal = store.conn.execute("SELECT count(*) FROM learning_labels WHERE actor_id=? AND undone_at IS NULL AND answer!='unsure'", (actor_id,)).fetchone()[0]
    runs = [dict(r) | {'details': json.loads(r['details'])} for r in store.conn.execute('SELECT * FROM learning_runs ORDER BY id DESC LIMIT 30')]
    measured = [r for r in reversed(runs) if r['status'] == 'adopted']
    active = {}
    for run in reversed(runs):
        if run['status'] == 'rolled_back' and run['details'].get('model'):
            active.pop(run['details']['model'], None)
        for model, result in run['details'].get('models', {}).items():
            if result['status'] == 'rolled_back':
                active.pop(model, None)
            elif result['status'] == 'adopted':
                active[model] = result | {'model': model, 'at': run['finished_at']}

    return {'personal_labels': personal, 'house_truth': len(truth(store)),
            'last_learned': measured[-1]['finished_at'] if measured else None, 'runs': runs, 'metrics': list(active.values())}


def split(rows):
    """Whole UTC days, one in five permanently reserved; later than ALL training rows.

    A 24-hour gap keeps adjacent captures out. Reserved days never train in a later run.
    Frames belonging to the same obligation share the earliest capture's day.
    """
    groups = {}
    for r in rows:
        key = r['obligation_id'] or r['evidence_id']
        groups[key] = min(groups.get(key, r['captured_at']), r['captured_at'])
    def day(r):
        return parse_stamp(groups[r['obligation_id'] or r['evidence_id']]).date()
    held = [r for r in rows if day(r).toordinal() % 5 == 0]
    if not held:
        return [], []
    latest = max(day(r) for r in held)
    test = [r for r in held if day(r) == latest]
    train = [r for r in rows if day(r).toordinal() % 5 != 0 and day(r) < latest - timedelta(days=1)
             and parse_stamp(r['captured_at']).date() < latest - timedelta(days=1)]
    return train, test


def capture_action(store, oid, action, body, actor):
    """Additive seam for names/disputes/verdicts. Caller owns the review transaction.

    The identity/junk lanes keep their operational decisions. This ledger never treats
    a single resident's attribution or exculpation as independent training truth.
    """
    evidence = store.conn.execute("SELECT * FROM evidence WHERE obligation_id=? AND kind='snapshot' AND coalesce(json_extract(capture,'$.identity_source'),'')='' AND label NOT LIKE 'Person%' AND label!='Who put it down (from footage)' ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
    if not evidence and action not in ('name', 'dispute', 'settle'):
        return
    actor_id = actor['id'] if actor else None
    item = store.obligation(oid)
    if action in ('name', 'dispute', 'settle'):
        person = body.get('resident_id') or item.get('resident_id') or item.get('original_resident_id')
        if person is None:
            return
        # Keep sighting naming separate from ownership: requires the anchored crop.
        source = store.conn.execute("SELECT e.* FROM evidence e JOIN person_samples s ON s.evidence_id=e.id WHERE s.obligation_id=? AND s.cue='outfit' AND e.kind='snapshot' ORDER BY e.created_at DESC LIMIT 1", (oid,)).fetchone()
        if not source:
            return
        evidence = source
        task, response, value = 'identity', 'name' if action == 'name' or body.get('decision') == 'them' else 'not_name', person
    elif action == 'verdict' and body['verdict'] in ('not_dish', 'clean'):
        task, response, value = 'present', 'no' if body['verdict'] == 'not_dish' else 'yes', None
    else:
        return
    qid = add_question(store, source=f"evidence:{evidence['id']}:{task}", task=task, evidence_id=evidence['id'],
                       obligation_id=oid, box=item.get('box') if task == 'present' else None,
                       priority=8, reason='A housemate’s correction needs an independent check')
    # These are unsolicited audit labels, so interested parties may submit but never confirm themselves.
    payload = clean_answer(task, response, value, actor_id)
    store.conn.execute("UPDATE learning_labels SET undone_at=? WHERE question_id=? AND actor_id IS ? AND undone_at IS NULL", (stamp(), qid, actor_id))
    store.conn.execute('INSERT INTO learning_labels(question_id,actor_id,answer,value,source,created_at) VALUES(?,?,?,?,?,?)',
                       (qid, actor_id, response, json.dumps(payload, sort_keys=True), f'house:{action}', stamp()))
    if action == 'dispute':
        # Any open ownership dispute excludes the associated identity labels from export.
        store.conn.execute("UPDATE learning_labels SET undone_at=? WHERE question_id=? AND source='house:name' AND undone_at IS NULL", (stamp(), qid))
