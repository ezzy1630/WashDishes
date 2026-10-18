"""One incident workspace; identity, responsibility and outcomes remain separate facts."""
from __future__ import annotations

import hashlib
import json

from .sequences import STAGES
from .sharing import readiness
from .store import CAMERA_CLOSED_NOTES, REMOVED_REVIEWED_NOTE, parse_stamp, stamp, utcnow


class ReviewConflict(ValueError):
    pass


def revision(item: dict) -> str:
    # Presence timestamps change continuously. Human decisions and evidence must not.
    facts = {k: item[k] for k in ('id', 'resident_id', 'confidence', 'lifecycle', 'resolved_at',
                                 'cleaner_id', 'review_pending', 'dishes', 'due_at', 'reason')}
    facts['events'] = [e['id'] for e in item['events']]
    facts['evidence'] = [(e['id'], e['expired'], e['capture']) for e in item['evidence'] if not e['capture'].get('derived_sequence')]
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def sequence_signature(manifest: dict) -> str:
    facts = {k: manifest.get(k) for k in ('owner', 'past_deadline', 'continuity_events')}
    facts['stages'] = [{k: s.get(k) for k in ('key', 'evidence_id', 'sha256', 'at', 'box', 'box_kind')} for s in manifest['stages']]
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def confirmed_late(item: dict, decision: dict, sequence: dict) -> bool:
    """One evidence rule for the feed, resident counts, and outbound late alerts."""
    if (not item['archived_at'] and item['source'] == 'manual' and item['lifecycle'] == 'resolved'
            and item['confidence'] == 'confirmed' and item['resident_id'] is not None
            and item['reason'] not in CAMERA_CLOSED_NOTES and item['overdue_verified']):
        # An explicitly logged deposit and human-confirmed washing time are direct
        # testimony. Camera naming or camera sightings alone cannot use this path.
        return True
    return bool(not item['archived_at'] and item['lifecycle'] in ('open', 'resolved', 'cleared')
                and not item['review_pending'] and item['resident_id'] is not None
                and item['confidence'] == 'confirmed' and item['overdue_verified']
                and decision.get('condition') == 'used' and sequence.get('complete')
                and all(decision.get('checks', {}).get(k) is True for k in STAGES)
                and decision.get('reviewed_at') and decision.get('sequence_signature') == sequence_signature(sequence))


def outcome(item: dict) -> str:
    if item['lifecycle'] == 'open' and item['review_pending']:
        return 'unknown'
    if item['lifecycle'] == 'resolved':
        return 'removed' if item['reason'] in CAMERA_CLOSED_NOTES else 'washed'
    return {'open': 'out', 'cleared': 'removed', 'dismissed': 'dismissed', 'disputed': 'disputed'}[item['lifecycle']]


class Incidents:
    def __init__(self, store, media, sequences):
        self.store, self.media, self.sequences = store, media, sequences

    def _decision(self, oid: int) -> dict:
        row = self.store.conn.execute('SELECT data FROM incident_decisions WHERE obligation_id=?', (oid,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _revision(self, item: dict) -> str:
        return hashlib.sha256((revision(item) + self.sequences._source_token(item)).encode()).hexdigest()

    def detail(self, oid: int) -> dict:
        with self.store.lock:
            item = self.store.obligation(oid)
            decision = self._decision(oid)
            jobs = [dict(r) for r in self.store.conn.execute('SELECT label,status,error,attempts FROM clip_jobs WHERE obligation_id=?', (oid,))]
        try:
            sequence = self.sequences.get(oid)
            if item['archived_at'] and sequence['stale']:
                self.sequences.refresh(obligation_id=oid)
                sequence = self.sequences.get(oid)
        except LookupError:
            sequence = None
        identified = (sequence or {}).get('visible_person_id') if sequence and not sequence.get('stale') else None
        share_ready, blocked = readiness(item, sequence, decision)
        return {'share_ready': share_ready, 'share_reason': blocked,
                'share_url': f'/api/sequences/{oid}/share' if share_ready else None, 'record': item, 'revision': self._revision(item), 'sequence': sequence, 'decision': decision,
                'identified_person_id': identified,
                'outcome': 'clean' if decision.get('condition') == 'clean' else outcome(item),
                'capture_jobs': jobs}

    def page(self, *, filter: str = 'attention', resident_id: int | None = None, limit: int = 24,
             offset: int = 0, days: int | None = None) -> dict:
        from datetime import timedelta
        with self.store.lock:
            rows = self.store.conn.execute('''SELECT o.*,r.name resident_name,s.manifest,s.image_id,d.data decision
                FROM obligations o LEFT JOIN residents r ON r.id=o.resident_id
                LEFT JOIN evidence_sequences s ON s.obligation_id=o.id
                LEFT JOIN incident_decisions d ON d.obligation_id=o.id ORDER BY o.created_at DESC,o.id DESC''').fetchall()
        observed = {i.get('obligation_id') for i in self.store.load_scene()
                    if not i.get('unseen_since') and not i.get('ignored') and i.get('role') in ('dirty', 'sink')}
        cards = []
        for row in rows:
            item = dict(row)
            seq = json.loads(item.pop('manifest')) if item['manifest'] else {}
            decision = json.loads(item.pop('decision')) if item['decision'] else {}
            known = item['confidence'] == 'confirmed' and item['resident_id'] is not None
            active = (not item['archived_at'] and item['lifecycle'] == 'open' and (not item['review_pending'] or item['id'] in observed)
                      and decision.get('condition') != 'clean')
            later = bool(seq.get('past_deadline'))
            review_current = bool(decision.get('reviewed_at') and decision.get('sequence_signature') == sequence_signature(seq)) if seq else False
            late = confirmed_late(item, decision, seq)
            attention = active and later and seq.get('object_localized', False) and not review_current
            # Newly discovered dishes remain visible as a separate class, not offenses.
            status = 'attention' if attention else 'late' if late else 'out' if active else 'history'
            cards.append({'id': item['id'], 'dishes': json.loads(item['dishes']), 'title': self.store.dish_description(json.loads(item['dishes'])),
                          'created_at': item['created_at'], 'followup_at': item['due_at'], 'due_at': item['due_at'] if item['timing_known'] else None,
                          'location': item['location'], 'owner': item['resident_name'] if known else None,
                          'resident_id': item['resident_id'] if known else None,
                          'outcome': 'clean' if decision.get('condition') == 'clean' else outcome(item), 'status': status,
                          'archived': bool(item['archived_at']), 'active': bool(active), 'late': late, 'past_deadline': later, 'baseline': not bool(item['timing_known']),
                          'followup_captured': seq.get('followup_captured', False), 'captured': seq.get('captured', 0), 'complete': seq.get('complete', False),
                          'reviewed_at': decision.get('reviewed_at') if review_current else None, 'condition': decision.get('condition', 'unknown'),
                          'image_url': f"/api/sequences/{item['id']}/image" if item['image_id'] else None})
        if filter in ('photos', 'tracking'):
            for card in cards:
                if card['archived']:
                    card.update(share_ready=False, share_url=None, share_reason='Archived')
                    continue
                detail = self.detail(card['id'])
                card.update({k:detail[k] for k in ('share_ready', 'share_url', 'share_reason')})
                card['visible_person'] = (detail['sequence'] or {}).get('visible_person')
                if card['share_ready']:
                    stages = detail['sequence']['stages']
                    card['elapsed_minutes'] = int((parse_stamp(stages[-1]['at'])-parse_stamp(stages[-2]['at'])).total_seconds()/60)
        if days:
            since = stamp(utcnow() - timedelta(days=days))
            cards = [c for c in cards if c['created_at'] >= since]
        people = {}
        for card in cards:
            if card['resident_id'] is not None and card['late']:
                p = people.setdefault(card['resident_id'], {'id': card['resident_id'], 'name': card['owner'], 'late': 0})
                p['late'] += 1
        if resident_id is not None:
            cards = [c for c in cards if c['resident_id'] == resident_id]
        counts = {'attention': sum(c['status'] == 'attention' for c in cards),
                  'late': sum(c['late'] for c in cards), 'out': sum(c['active'] for c in cards),
                  'all': sum(not c['archived'] for c in cards), 'archive': sum(c['archived'] for c in cards),
                  'photos': sum(c.get('share_ready', False) for c in cards),
                  'tracking': sum(c['active'] and not c.get('share_ready', False) for c in cards)}
        cards = [c for c in cards if c['archived'] == (filter == 'archive')]
        if filter == 'photos':
            cards = [c for c in cards if c.get('share_ready')]
        elif filter == 'tracking':
            cards = [c for c in cards if c['active'] and not c.get('share_ready')]
        elif filter == 'attention':
            cards = [c for c in cards if c['status'] == 'attention']
        elif filter == 'late':
            cards = [c for c in cards if c['late']]
        elif filter == 'out':
            cards = [c for c in cards if c['active']]
        elif filter == 'unknown':
            cards = [c for c in cards if c['active'] and not c['owner']]
        total = len(cards)
        selected = cards[offset:offset+limit]
        # Check image availability for this page only, not the entire archive on each poll.
        for card in selected:
            if card['image_url']:
                card['image_url'] = self.sequences.get(card['id'])['image_url']
        return {'items': selected, 'total': total, 'next_offset': offset+limit if offset+limit < total else None,
                'counts': counts, 'people': sorted(people.values(), key=lambda p: (-p['late'], p['name']))}

    def review(self, oid: int, body: dict) -> dict:
        # All facts commit together. Derived images can retry without losing the answer.
        with self.sequences.lock, self.media.lock, self.store.lock, self.store.conn:
            item = self.store.obligation(oid)
            if body['revision'] != self._revision(item):
                raise ReviewConflict('This incident changed. Reload its evidence before saving your decision.')
            previous = self._decision(oid)
            was_confirmed_late = oid in self.store.confirmed_late_ids()
            checks = body['checks']
            manifest, _ = self.sequences._draft(item)
            if any(checks.get(s['key']) is True and not s['sha256'] for s in manifest['stages']):
                raise ValueError('A missing or expired stage cannot be confirmed. Review the available evidence first.')
            if all(checks.get(k) is True for k in STAGES) and not manifest['complete']:
                raise ValueError('Four distinct captured stages are required to approve the whole sequence.')
            person_id = body['person_id']
            if person_id is not None:
                self.store._resident(person_id)
            if body['cleaner_id'] is not None:
                self.store._resident(body['cleaner_id'])
            if body['responsibility'] == 'confirm':
                if person_id is None or not all(checks.get(k) is True for k in ('using', 'leaving')):
                    raise ValueError('Confirm the handling and departure evidence and choose the responsible person.')
                if body['condition'] not in ('used', 'clean'):
                    raise ValueError('Confirm whether the dish was used or clean before assigning responsibility.')
            now = utcnow()
            if body['responsibility'] == 'confirm':
                if item['lifecycle'] in ('dismissed', 'disputed'):
                    raise ValueError('Reopen this incident before assigning responsibility.')
                self.store.conn.execute("UPDATE obligations SET resident_id=?,confidence='confirmed',review_pending=0 WHERE id=?", (person_id, oid))
                self.store._event(oid, 'confirm', 'Responsibility confirmed from handling and departure evidence', {'resident_id': person_id})
            if body['identity'] == 'named':
                if person_id is None:
                    raise ValueError('Choose the person visible in the evidence.')
                self.store.conn.execute('UPDATE person_samples SET resident_id=?,hint_resident_id=NULL WHERE obligation_id=?', (person_id, oid))
                if item['suggested_resident_id'] is not None and item['suggestion_correct'] is None:
                    self.store.conn.execute('UPDATE obligations SET suggestion_correct=? WHERE id=?',
                                            (int(item['suggested_resident_id'] == person_id), oid))
                self.store._event(oid, 'person_identified', 'Person in the evidence identified; responsibility is a separate decision',
                                  {'resident_id': person_id, 'evidence_id': next(s['evidence_id'] for s in manifest['stages'] if s['key'] == 'using')})
            elif body['identity'] == 'unknown':
                self.store.conn.execute('UPDATE person_samples SET resident_id=NULL,hint_resident_id=NULL WHERE obligation_id=?', (oid,))
                self.store._event(oid, 'person_unidentified', 'Identity cannot be established from this evidence')
            if body['responsibility'] == 'unknown':
                self.store.conn.execute("UPDATE obligations SET resident_id=NULL,confidence='candidate',overdue_verified=0,attribution=NULL WHERE id=?", (oid,))
            if body['condition'] == 'not_dish':
                if body['responsibility'] == 'confirm':
                    raise ValueError('A non-dish cannot be assigned to someone.')
                self.store._action_locked(oid, 'dismiss', note='Not a dish', now=now)
            elif body['outcome'] == 'washed':
                self.store._action_locked(oid, 'resolve', cleaner_id=body['cleaner_id'], note=body['note'],
                                          resolved_at=parse_stamp(body['resolved_at']) if body['resolved_at'] else now, now=now)
            elif body['outcome'] == 'removed':
                # A removal is not a washing observation, and never creates lateness by itself.
                when = parse_stamp(body['resolved_at']) if body['resolved_at'] else now
                if when < parse_stamp(item['created_at']) or when > now:
                    raise ValueError('Removal time must fall between first observation and now.')
                self.store.conn.execute("UPDATE obligations SET lifecycle='resolved',resolved_at=?,cleaner_id=NULL,review_pending=0,reason=? WHERE id=?",
                                        (stamp(when), REMOVED_REVIEWED_NOTE, oid))
                self.store._event(oid, 'removed_reviewed', 'Removal confirmed by a person; washing not established', {'at': stamp(when)})
            elif body['outcome'] == 'disputed':
                self.store._action_locked(oid, 'dispute', note=body['note'], now=now)
            elif body['outcome'] == 'out' and item['lifecycle'] != 'open':
                self.store._action_locked(oid, 'reopen', note=body['note'], now=now)
            current = self.store.obligation(oid)
            # Recognition alone never creates an offense. A clean item is not a dirty dish.
            if body['condition'] == 'clean' or checks.get('later') is False:
                self.store.conn.execute('UPDATE obligations SET overdue_verified=0 WHERE id=?', (oid,))
            elif (body['condition'] == 'used' and checks.get('later') is True and manifest['past_deadline']
                  and current['confidence'] == 'confirmed' and current['lifecycle'] in ('open', 'resolved')
                  and not current['review_pending']):
                self.store.conn.execute('UPDATE obligations SET overdue_verified=1 WHERE id=?', (oid,))
            current = self.store.obligation(oid)
            current_manifest, _ = self.sequences._draft(current)
            changes = {k: body[k] for k in ('identity', 'person_id', 'responsibility', 'condition', 'outcome', 'note') if body[k] != 'keep'}
            decision = dict(previous, **changes,
                            checks=checks, reviewed_at=stamp(now), sequence_signature=sequence_signature(current_manifest))
            self.store.conn.execute('INSERT INTO incident_decisions(obligation_id,data) VALUES(?,?) ON CONFLICT(obligation_id) DO UPDATE SET data=excluded.data', (oid, json.dumps(decision)))
            if not was_confirmed_late and confirmed_late(current, decision, current_manifest):
                self.store._event(oid, 'overdue_observed', 'Human-reviewed dirty dish visible after its deadline')
            self.store._event(oid, 'incident_reviewed', 'Evidence, identity, responsibility and outcome review saved', {'checks': checks})
        try:
            self.sequences.refresh(obligation_id=oid)
        except (OSError, ValueError):
            self.sequences.error = 'Decision saved. Its image will update on the next evidence pass.'
        return self.detail(oid)
