"""Saved four-stage evidence drafts; capture completeness never assigns responsibility."""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
from pathlib import Path

from PIL import Image, ImageFont, UnidentifiedImageError

from .media import Media
from .notify import timezone
from .store import Store, parse_stamp, stamp, utcnow

STAGES = ('before', 'using', 'leaving', 'later')
TITLES = ('Earlier', 'Activity near this dish', 'Left out', 'After two hours')
LIMIT = 'Separate observations do not prove continuous non-washing. A nearby person is not automatically the owner.'
PREFIXES = {'before': ('Before it was put down',), 'using': ('Person handling',),
            'leaving': ('Dish left unattended', 'Already present at startup', 'Possible unattended dish'),
            'later': ('Matching dish still visible after the deadline',)}


def _font(size: int, bold: bool = False):
    for name in (f'/usr/share/fonts/truetype/dejavu/DejaVuSans{"-Bold" if bold else ""}.ttf',
                 f'/System/Library/Fonts/Supplemental/Arial{" Bold" if bold else ""}.ttf'):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def render_collage(manifest: dict, pictures: dict[str, bytes]) -> bytes:
    """Available observations only; absence and responsibility are never inferred."""
    from . import evidence_image as visual
    from .collage import place
    available = [s for s in manifest['stages'] if pictures.get(s['key'])]
    first = next((s for s in available if s['key'] == 'leaving'), available[0] if available else None)
    if first is None:
        raise ValueError('No retained photos to render.')
    day = parse_stamp(first['at']).astimezone(timezone()).strftime('%b %-d')
    dish_boxes = [s['box'] for s in available if s.get('box') and s['key'] != 'using']
    region = visual.region(dish_boxes,pictures[first['key']])
    panels = []
    for stage in available:
        key = stage['key']
        label = {'before': 'Before · check this spot', 'using': 'Who was at the counter',
                 'leaving': 'After' if 'before' in pictures else 'First saved view',
                 'later': 'Seen again'}[key]
        if key == 'later':
            seconds = (parse_stamp(stage['at'])-parse_stamp(first['at'])).total_seconds()
            from .collage import _later
            label = 'Seen again · ' + _later(max(0,seconds))
        people = [[x,y,w,min(h,max(w,h*.6))] for x,y,w,h in stage.get('person_boxes',[])]
        if key == 'using' and not people:
            label = 'Counter view'
        panel = {'jpeg':pictures[key], 'label':label,
                 'time':parse_stamp(stage['at']).astimezone(timezone()).strftime('%b %-d · %-I:%M %p') +
                        (' · approximate' if stage.get('time_basis') != 'capture' or manifest.get('render_sources',{}).get(key,{}).get('timestamp_estimated') else ''),
                 'boxes':people if people else ([stage['box']] if stage.get('box') else []),
                 'expected':key == 'before', 'people':key == 'using'}
        if key != 'using':
            panel['region'] = region
        panels.append(panel)
    notes = []
    if 'before' not in pictures:
        clock = parse_stamp(first['at']).astimezone(timezone()).strftime('%-I:%M %p')
        notes.append(f"Arrival wasn't recorded — it was already here at {clock}.")
    elif manifest.get('review_pending') or manifest.get('continuity_events'):
        notes.append('Check that these views show the same dish.')
    if 'using' in pictures:
        notes.append('Only name someone if you know they left these dishes')
    if manifest.get('outcome') in ('resolved','cleared'):
        notes.append('This record is closed; these are earlier photos.')
    elif manifest.get('outcome') in ('dismissed','disputed') or manifest.get('status') == 'rejected':
        notes.append('This record was disputed or set aside.')
    # Detector nouns are unconfirmed (a bottle can be called a cup). Let the pixels
    # identify the object rather than asserting a species or an owner in a title.
    return visual.render('Dishes to check',f"{day} · {place(manifest.get('location'))}",panels,notes)


class SequenceCollection:
    def __init__(self, store: Store, media: Media):
        self.store, self.media = store, media
        self.lock = threading.RLock()
        self.error: str | None = None

    def _source_token(self, item: dict) -> str:
        sources = []
        for e in item['evidence']:
            if e['kind'] != 'snapshot' or not (e['capture'].get('stage') in STAGES or any(e['label'].startswith(p) for p in PREFIXES.values())):
                continue
            saved = self.store.evidence(e['id'])
            try:
                info = self.media.safe_path(saved['filename']).stat()
                state = (info.st_size, info.st_mtime_ns)
            except OSError:
                state = None
            sources.append((e, state, parse_stamp(e['expires_at']) <= utcnow()))
        facts = {k: item[k] for k in ('dishes', 'location', 'resident_name', 'confidence', 'due_at', 'timing_known', 'lifecycle', 'review_pending', 'followup_at')}
        facts['review'] = next((e['id'] for e in item['events'] if e['event'] == 'incident_reviewed'), None)
        facts['identity'] = next((e['id'] for e in item['events'] if e['event'] in ('person_identified', 'person_unidentified')), None)
        facts['gaps'] = [e['id'] for e in item['events'] if e['event'] in ('observation_gap', 'dish_continuity_uncertain')]
        return hashlib.sha256(json.dumps([8, sources, facts], sort_keys=True).encode()).hexdigest()

    def _draft(self, item: dict) -> tuple[dict, dict[str, bytes]]:
        stages, pictures = [], {}
        continued = any(e['event'] == 'continued_use' for e in item['events'])
        for key, title in zip(STAGES, TITLES):
            choices = [e for e in item['evidence'] if e['kind'] == 'snapshot' and
                       (e['capture'].get('stage') == key or e['label'].startswith(PREFIXES[key]))]
            if key == 'later':
                choices = [e for e in choices if e['created_at'] >= item['followup_at']]
            if key in ('before', 'using') and (not item['timing_known'] or item['source'] == 'baseline'):
                choices = [e for e in choices if e['capture'].get('recovered')]
            # A missing early capture must not hide a retained replacement. Prefer original
            # capture metadata to legacy record times; the image still needs action review.
            choices.sort(key=lambda e: e['created_at'], reverse=key in ('using', 'leaving'))
            choices.sort(key=lambda e: e['capture'].get('stage') != key)
            photo, data = None, None
            for candidate in choices:
                if candidate['expired'] or parse_stamp(candidate['expires_at']) <= utcnow():
                    continue
                saved = self.store.evidence(candidate['id'])
                try:
                    candidate_data = self.media.safe_path(saved['filename']).read_bytes()
                    with Image.open(io.BytesIO(candidate_data)) as image:
                        image.verify()
                except (OSError, ValueError, UnidentifiedImageError):
                    continue
                photo, data = candidate, candidate_data
                break
            photo = photo or (choices[0] if choices else None)
            if not item['timing_known'] and not continued and key == 'leaving':
                title = 'First observed out'
            elif photo and photo['capture'].get('recovered') and key == 'before':
                title = 'Earlier saved view'
            stage = {'key': key, 'title': title, 'evidence_id': photo['id'] if photo else None,
                     'at': photo['created_at'] if photo else None, 'sha256': None, 'box': None, 'box_kind': None,
                     'url': photo['url'] if data is not None else None}
            stage['time_basis'] = 'capture' if photo and photo['capture'].get('stage') == key else 'legacy_record'
            if photo:
                capture = photo['capture']
                stage.update(box=capture.get('box'), box_kind=capture.get('box_kind'))
                if stage['box_kind'] not in ('candidate_item', 'expected_position'):
                    stage['box'] = None
            if data is not None:
                pictures[key] = data
                stage['sha256'] = hashlib.sha256(data).hexdigest()
            stages.append(stage)
        complete = len(pictures) == 4 and len({s['sha256'] for s in stages}) == 4
        times = [s['at'] for s in stages]
        if complete:
            complete = all(parse_stamp(a) < parse_stamp(b) for a, b in zip(times, times[1:]))
        object_localized = all(s['box'] and s['box_kind'] == 'candidate_item' and s['sha256'] for s in stages[1:])
        complete = bool(complete and object_localized)
        warnings = []
        if not item['timing_known']:
            warnings.append('Already present: the timer uses an observed unattended view. Original departure and owner are unknown.')
        if sum(d['quantity'] for d in item['dishes']) > 1:
            warnings.append('This episode includes multiple dishes. Check every listed item; the primary box alone does not establish the whole episode.')
        if any(s['sha256'] and s['time_basis'] == 'legacy_record' for s in stages):
            warnings.append('Older record times can differ from the frame. Read the original camera clock for precise timing.')
        if not complete:
            warnings.append('Four ordered views with the dish localized in handling, departure and later frames are required.')
        if item['review_pending'] or any(e['event'] in ('observation_gap', 'dish_continuity_uncertain') for e in item['events']):
            warnings.append('Continuity needs review: check that the later photo shows the same item.')
        if item['lifecycle'] in ('resolved', 'cleared', 'dismissed', 'disputed'):
            warnings.append(f"Record outcome: {item['lifecycle']}. Include this context when reviewing or sharing.")
        identity = next((e for e in item['events'] if e['event'] in ('person_identified', 'person_unidentified')), None)
        # A name identifies a person in one reviewed photo, not every future
        # activity photo recovered for the same dish. Preserve legacy bindings.
        row = self._row(item['id'])
        previous = json.loads(row['manifest']) if row else {}
        identity_photo = identity['data'].get('evidence_id') if identity else None
        if identity and not identity_photo and previous.get('identity_event_id', identity['id']) == identity['id']:
            identity_photo = previous.get('identity_evidence_id') if 'identity_evidence_id' in previous else next(
                (s['evidence_id'] for s in previous.get('stages', []) if s['key'] == 'using'), None)
        using_photo = next(s['evidence_id'] for s in stages if s['key'] == 'using')
        person_id = identity['data'].get('resident_id') if identity and identity_photo and identity_photo == using_photo else None
        person = next((p['name'] for p in self.store.residents() if p['id'] == person_id), None)
        manifest = {'visible_person': person, 'visible_person_id': person_id,
                    'identity_event_id': identity['id'] if identity else None, 'identity_evidence_id': identity_photo,
                    'obligation_id': item['id'], 'dishes': self.store.dish_description(item['dishes']),
                    'location': item['location'], 'followup_at': item['followup_at'], 'timer_basis': 'departure' if item['timing_known'] or continued else 'first_observed', 'stages': stages, 'captured': len(pictures), 'complete': complete,
                    'object_localized': bool(object_localized),
                    'continuity_events': [e['id'] for e in item['events'] if e['event'] in ('observation_gap', 'dish_continuity_uncertain', 'dish_not_seen')],
                    'later_captured': 'later' in pictures, 'status': 'review' if complete else 'incomplete',
                    'warnings': warnings, 'limitation': LIMIT, 'owner': item['resident_name'] if item['confidence'] == 'confirmed' else None,
                    'outcome': item['lifecycle'], 'review_pending': bool(item['review_pending'])}
        later = stages[-1]
        manifest['followup_captured'] = bool('later' in pictures and later['at'] >= item['followup_at'])
        manifest['past_deadline'] = bool('later' in pictures and item['due_at'] and later['at'] >= item['due_at'])
        if item['timing_known'] and 'later' in pictures and not manifest['past_deadline']:
            warnings.append('This later view does not establish a missed deadline.')
        manifest['source_token'] = self._source_token(item)
        manifest['fingerprint'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        return manifest, pictures

    def _render_sources(self, manifest: dict, pictures: dict) -> tuple[dict, dict]:
        from .render_sources import SourceFrames
        source = SourceFrames(self.media.buffer)
        rendered, provenance = {}, {}
        for stage in manifest['stages']:
            key = stage['key']
            if key not in pictures:
                continue
            # Legacy record time is not a camera timestamp. Keep those stills.
            at = parse_stamp(stage['at']).timestamp() if stage['time_basis'] == 'capture' else None
            rendered[key], provenance[key] = source.best(at, pictures[key])
            if key == 'using' and at is not None:
                stage['person_boxes'] = source.people(at,[s['box'] for s in manifest['stages'] if s.get('box')])
        return rendered, provenance

    def _row(self, obligation_id: int):
        with self.store.lock:
            return self.store.conn.execute('SELECT * FROM evidence_sequences WHERE obligation_id=?', (obligation_id,)).fetchone()

    def refresh(self, limit: int = 20, *, obligation_id: int | None = None, force: bool = False) -> int:
        """One bounded background batch. Saved originals and the existing media quota remain authoritative."""
        with self.lock:
            with self.store.lock:
                ids = [r[0] for r in self.store.conn.execute("SELECT obligation_id FROM evidence WHERE kind='snapshot' AND obligation_id IS NOT NULL GROUP BY obligation_id ORDER BY max(label LIKE 'Matching dish still visible after the deadline%') DESC, obligation_id DESC")]
            if obligation_id is not None:
                ids = [obligation_id]
            updated = 0
            for oid in ids:
                if updated >= limit:
                    break
                item = self.store.obligation(oid)
                if item['archived_at'] and obligation_id is None:
                    continue
                if not any(e['capture'].get('stage') == 'leaving' or e['label'].startswith(PREFIXES['leaving']) for e in item['evidence']):
                    continue
                row = self._row(oid)
                current_render = row and json.loads(row['manifest']).get('render_version') == 1
                if not force and current_render and row and json.loads(row['manifest']).get('source_token') == self._source_token(item):
                    continue
                manifest, pictures = self._draft(item)
                if not force and current_render and row and row['fingerprint'] == manifest['fingerprint']:
                    continue
                if not pictures:
                    continue
                checks, reviewed_at = {}, None
                if row:
                    from .incidents import sequence_signature
                    previous = json.loads(row['manifest'])
                    if previous.get('source_token') == manifest['source_token'] and sequence_signature(previous) == sequence_signature(manifest):
                        checks,reviewed_at = json.loads(row['checks']),row['reviewed_at']
                with self.store.lock:
                    decision = self.store.conn.execute('SELECT data FROM incident_decisions WHERE obligation_id=?', (oid,)).fetchone()
                if decision:
                    from .incidents import sequence_signature
                    decision = json.loads(decision[0])
                    if decision.get('sequence_signature') == sequence_signature(manifest):
                        checks, reviewed_at = decision['checks'], decision['reviewed_at']
                        manifest['status'] = 'rejected' if any(v is False for v in checks.values()) else 'ready' if manifest['complete'] and all(checks.get(k) is True for k in STAGES) else manifest['status']
                if any(v is False for v in checks.values()):
                    manifest['status'] = 'rejected'
                elif manifest['complete'] and all(checks.get(k) is True for k in STAGES):
                    manifest['status'] = 'ready'
                manifest['render_version'] = 1
                pictures, manifest['render_sources'] = self._render_sources(manifest, pictures)
                data = render_collage(manifest, pictures)
                image_id = row['image_id'] if row else None
                existing = self.store.evidence(image_id) if image_id else None
                with self.media.lock, self.store.lock:
                    if self._source_token(self.store.obligation(oid)) != manifest['source_token']:
                        continue
                    if existing and not existing['expired'] and parse_stamp(existing['expires_at']) > utcnow():
                        path = self.media.safe_path(existing['filename'])
                        temp = path.with_suffix('.sequence-tmp')
                        temp.write_bytes(data)
                        temp.chmod(0o600)
                        os.replace(temp, path)
                    else:
                        image_id = self.media.save_snapshot(oid, data, 'Four-stage evidence collage; review before sharing', capture={'derived_sequence': True})
                    manifest['updated_at'] = stamp()
                    with self.store.lock, self.store.conn:
                        self.store.conn.execute("INSERT INTO evidence_sequences(obligation_id,fingerprint,image_id,manifest) VALUES(?,?,?,?) ON CONFLICT(obligation_id) DO UPDATE SET fingerprint=excluded.fingerprint,image_id=excluded.image_id,manifest=excluded.manifest,checks='{}',reviewed_at=NULL",
                                                (oid, manifest['fingerprint'], image_id, json.dumps(manifest)))
                        self.store.conn.execute('UPDATE evidence_sequences SET checks=?,reviewed_at=? WHERE obligation_id=?',
                                                (json.dumps(checks), reviewed_at, oid))
                        if manifest['past_deadline'] and (not row or not json.loads(row['manifest']).get('past_deadline')):
                            self.store._event(oid, 'sequence_later_captured', 'Later dish photo saved; review the four-stage evidence before assigning responsibility')
                updated += 1
            self.error = None
            return updated

    def get(self, obligation_id: int) -> dict:
        row = self._row(obligation_id)
        if not row:
            raise LookupError('No saved sequence for this record yet.')
        saved = json.loads(row['manifest'])
        checks = json.loads(row['checks'])
        if any(v is False for v in checks.values()):
            saved['status'] = 'rejected'
        elif saved['complete'] and all(checks.get(k) is True for k in STAGES):
            saved['status'] = 'ready'
        image = self.store.evidence(row['image_id'])
        stale = saved['source_token'] != self._source_token(self.store.obligation(obligation_id))
        if stale:
            saved['status'] = 'review' if saved['complete'] else 'incomplete'
        saved.update(checks=checks, reviewed_at=row['reviewed_at'], image_id=row['image_id'],
                     stale=stale, image_url=f'/api/sequences/{obligation_id}/image' if not stale and image and not image['expired'] and parse_stamp(image['expires_at']) > utcnow() and self.media.safe_path(image['filename']).exists() else None)
        return saved

    def recent(self) -> list[dict]:
        with self.store.lock:
            ids = [r[0] for r in self.store.conn.execute('SELECT s.obligation_id FROM evidence_sequences s JOIN obligations o ON o.id=s.obligation_id WHERE o.archived_at IS NULL ORDER BY s.obligation_id DESC LIMIT 200')]
        return [self.get(oid) for oid in ids]

    def _catalog(self) -> list[dict]:
        with self.store.lock:
            rows = self.store.conn.execute('SELECT s.manifest,s.checks,o.lifecycle FROM evidence_sequences s JOIN obligations o ON o.id=s.obligation_id WHERE o.archived_at IS NULL ORDER BY o.id DESC').fetchall()
        items = []
        for row in rows:
            item, checks = json.loads(row['manifest']), json.loads(row['checks'])
            item['outcome'] = row['lifecycle']
            if any(v is False for v in checks.values()):
                item['status'] = 'rejected'
            elif item['complete'] and all(checks.get(k) is True for k in STAGES):
                item['status'] = 'ready'
            items.append(item)
        return items

    @staticmethod
    def needs_review(item: dict) -> bool:
        return item['past_deadline'] and item['status'] in ('review', 'incomplete') and item['outcome'] == 'open'

    def page(self, *, filter: str = 'all', limit: int = 40, offset: int = 0) -> dict:
        items = self._catalog()
        if filter == 'attention':
            items = [i for i in items if self.needs_review(i)]
        elif filter == 'ready':
            items = [i for i in items if i['status'] == 'ready']
        total = len(items)
        return {'items': [self.get(i['obligation_id']) for i in items[offset:offset+limit]],
                'total': total, 'next_offset': offset+limit if offset+limit < total else None,
                'summary': self.summary()}

    def summary(self) -> dict:
        items = self._catalog()
        return {'saved': len(items), 'attention': sum(self.needs_review(i) for i in items),
                'ready': sum(i['status'] == 'ready' for i in items), 'error': self.error}

    def image(self, obligation_id: int) -> Path:
        saved = self.get(obligation_id)
        if not saved['image_url']:
            raise LookupError('This saved collage expired or is no longer available.')
        return self.media.safe_path(self.store.evidence(saved['image_id'])['filename'])

    def review(self, obligation_id: int, fingerprint: str, checks: dict[str, bool | None]) -> dict:
        with self.lock:
            # Recompute now so a late photo, corrected ownership or dispute cannot race a stale review.
            item = self.store.obligation(obligation_id)
            manifest, pictures = self._draft(item)
            saved = self.get(obligation_id)
            if saved['fingerprint'] != fingerprint or manifest['fingerprint'] != fingerprint:
                raise ValueError('The evidence changed. Reload and review the current pictures.')
            if not saved['complete'] and all(checks.get(k) is True for k in STAGES):
                raise ValueError('All four distinct stages must be captured before the sequence can be approved.')
            updated = dict(saved)
            updated['status'] = 'rejected' if any(v is False for v in checks.values()) else 'ready' if all(checks.get(k) is True for k in STAGES) else manifest['status']
            pictures, updated['render_sources'] = self._render_sources(updated,pictures)
            data = render_collage(updated, pictures)
            with self.media.lock, self.store.lock:
                if self._source_token(self.store.obligation(obligation_id)) != manifest['source_token']:
                    raise ValueError('The evidence changed. Reload and review the current pictures.')
                path = self.image(obligation_id)
                temp = path.with_suffix('.sequence-tmp')
                temp.write_bytes(data)
                temp.chmod(0o600)
                os.replace(temp, path)
                with self.store.lock, self.store.conn:
                    self.store.conn.execute('UPDATE evidence_sequences SET checks=?,reviewed_at=? WHERE obligation_id=?', (json.dumps(checks), stamp(), obligation_id))
                    self.store._event(obligation_id, 'sequence_reviewed', 'Four-stage evidence review saved; ownership and washing outcome unchanged', {'checks': checks})
            return self.get(obligation_id)
