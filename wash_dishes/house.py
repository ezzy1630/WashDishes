"""Small house-facing API, separate from the private operator/camera dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, nullcontext, suppress
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import house_verdicts, house_visits, league
from .house_auth import HouseConfig, JWTVerifier, SupabaseAdmin
from .house_schema import slug_for
from .store import parse_stamp, stamp, utcnow


class Write(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Name(Write):
    resident_id: int = Field(ge=1, strict=True)


class Dispute(Write):
    note: str = Field(default='', max_length=1000)


class Settle(Write):
    decision: str


class Verdict(Write):
    verdict: str
    of_visit_id: str | None = Field(default=None, max_length=200)


class Join(Write):
    token: str = Field(min_length=20, max_length=100)
    password: str = Field(min_length=8, max_length=256)


class Weights(Write):
    pan: int = Field(default=2, ge=0, le=100, strict=True)
    pot: int = Field(default=2, ge=0, le=100, strict=True)
    default: int = Field(default=1, ge=0, le=100, strict=True)


class Rules(Write):
    enabled: bool = Field(default=True, strict=True)
    weights: Weights = Field(default_factory=Weights)
    min_points: int = Field(default=4, ge=1, le=10000, strict=True)
    min_lead: int = Field(default=2, ge=0, le=10000, strict=True)


class Override(Write):
    resident_id: int | None = Field(default=None, ge=1, strict=True)


class RateLimit:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries: dict[str, list[float]] = {}

    def check(self, key: str, *, count: int = 20, seconds: int = 60) -> None:
        now = time.monotonic()
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v[-1] > now - 900}
            recent = [t for t in self.entries.get(key, []) if t > now - seconds]
            if len(recent) >= count:
                raise HTTPException(429, 'A little too quick! Wait a minute before trying again.')
            if key not in self.entries and len(self.entries) >= 2048:
                raise HTTPException(429, 'The house is busy. Try again shortly.')
            self.entries[key] = [*recent, now]


class HouseBoundary:
    """Tunnel headers cannot reach the operator app; CORS is scoped to /house/."""
    def __init__(self, app, site_origin: str):
        self.app, self.site_origin = app, site_origin

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        house = scope['path'].startswith('/house/')
        if 'cf-connecting-ip' in headers and not house:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'Only the house app is available here.'}, status_code=403)(scope, receive, send)
        if not house or scope['type'] == 'websocket':
            return await self.app(scope, receive, send)
        origin = headers.get('origin')
        if origin and (not self.site_origin or origin != self.site_origin):
            return await JSONResponse({'detail': 'This house app origin is not allowed.'}, status_code=403)(scope, receive, send)

        async def house_send(message):
            if message['type'] == 'http.response.start':
                extra = [(b'cache-control', b'private, no-store')]
                if origin:
                    extra += [(b'access-control-allow-origin', origin.encode()), (b'vary', b'Origin')]
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control'] + extra
            await send(message)

        if scope.get('method') == 'OPTIONS':
            requested = {v.strip().lower() for v in headers.get('access-control-request-headers', '').split(',') if v.strip()}
            if headers.get('access-control-request-method') not in ('GET', 'POST') or requested - {'authorization', 'content-type'}:
                return await JSONResponse({'detail': 'That house request is not allowed.'}, status_code=403)(scope, receive, house_send)
            return await Response(status_code=204, headers={'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Access-Control-Max-Age': '600'})(scope, receive, house_send)
        return await self.app(scope, receive, house_send)


class House:
    def __init__(self, store, media, config: HouseConfig):
        self.store, self.media, self.config = store, media, config
        self.verifier = JWTVerifier(config)
        self.admin = SupabaseAdmin(config)
        self.limiter = RateLimit()
        self.join_lock = threading.Lock()
        path = store.root / 'house-media.key'
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as secret_file:
                secret_file.write(secrets.token_bytes(32))
        self.media_key = path.read_bytes()
        if len(self.media_key) != 32:
            raise ValueError('The house media signing key must contain exactly 32 bytes.')
        path.chmod(0o600)

    @asynccontextmanager
    async def scheduler(self, enabled: bool):
        async def run():
            from starlette.concurrency import run_in_threadpool
            while True:
                try:
                    await run_in_threadpool(league.close_weeks, self.store)
                except (OSError, sqlite3.Error):
                    logging.getLogger(__name__).exception('Weekly close failed; retrying on the next minute.')
                await asyncio.sleep(60)
        task = asyncio.create_task(run()) if enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def resident(self, request: Request) -> dict:
        if request.app.state.config.demo or request.app.state.config.snapshot:
            raise HTTPException(503, 'House accounts are unavailable in demo and snapshot mode.')
        scheme, _, token = request.headers.get('authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token:
            raise HTTPException(401, 'Sign in to see the house.')
        subject = self.verifier.subject(token)
        with self.store.lock:
            row = self.store.conn.execute('SELECT * FROM residents WHERE active=1 AND auth_user_id=?', (subject,)).fetchone()
        if not row:
            raise HTTPException(403, 'Your account is not linked to an active housemate. Ask the operator for a sign-in link.')
        resident = dict(row)
        if request.method == 'POST':
            self.limiter.check(f"user:{resident['id']}")
        return resident

    def signed_url(self, evidence: dict, *, now=None) -> str | None:
        now = now or utcnow()
        if not self.config.public_origin or evidence['expired'] or parse_stamp(evidence['expires_at']) <= now:
            return None
        exp = min(int(now.timestamp()) + 1800, int(parse_stamp(evidence['expires_at']).timestamp()))
        signature = hmac.new(self.media_key, f"{evidence['id']}:{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{self.config.public_origin}/house/media/{quote(evidence['id'], safe='')}?exp={exp}&sig={signature}"

    def evidence_for(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        return [dict(r) for r in self.store.conn.execute(
            f"""SELECT * FROM evidence WHERE kind IN ('snapshot','filmstrip') AND (
                obligation_id IN ({','.join('?' for _ in ids)}) OR (kind='filmstrip' AND label='arrival'
                AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids')
                    WHERE value IN ({','.join('?' for _ in ids)})))) ORDER BY created_at,id""", [*ids, *ids])]

    def dish(self, item: dict, residents: list[dict], settings: dict, *, actor: dict | None, closed: bool,
             evidence: list[dict]) -> dict:
        people = {r['id']: {'id': r['id'], 'name': r['name']} for r in residents}
        owner = people.get(item['resident_id']) if item['confidence'] == 'confirmed' and league.human_named(item) else None
        by = people.get(item.get('named_by'))
        dispute = None
        if item.get('dispute_state'):
            dispute = {'state': item['dispute_state'], 'by': people.get(item['dispute_by']),
                       'note': item['dispute_note'], 'at': item['dispute_at'],
                       'settled_by': people.get(item['dispute_settled_by'])}
        media = [e for e in evidence if e['obligation_id'] == item['id']]
        filmstrip = next((e for e in reversed(media) if e['kind'] == 'filmstrip' and e['label'] == 'arrival'), None)
        photo = next((e for e in media if e['kind'] == 'snapshot' and e['label'] in ('Dish left unattended', 'Matching dish still visible after the deadline')), None)
        photo = photo or next((e for e in media if e['kind'] == 'snapshot'), None)
        open_dispute = item.get('dispute_state') == 'open' or item['lifecycle'] == 'disputed'
        operator = actor is None
        actor_id = actor['id'] if actor else None
        eligible_settler = operator or actor_id not in (item.get('dispute_by') or item.get('resident_id'), item.get('named_by'))
        def photo_url(evidence):
            if not evidence or evidence['expired'] or parse_stamp(evidence['expires_at']) <= utcnow():
                return None
            return f"/api/evidence/{evidence['id']}" if operator else self.signed_url(evidence)
        return {'id': item['id'], 'kind': item['dishes'][0]['kind'],
                'title': self.store.dish_description(item['dishes']), 'location': item['location'],
                'out_since': item['created_at'], 'due_at': item['due_at'], 'gone_at': item['resolved_at'],
                'points': 0 if open_dispute else league.points(item, settings), 'resident': owner,
                'named_by': by, 'named_at': item.get('named_at'), 'dispute': dispute,
                'filmstrip_url': photo_url(filmstrip), 'photo_url': photo_url(photo),
                'can_name': not closed and not open_dispute and (operator or owner is None),
                'can_dispute': not closed and owner is not None and not open_dispute and (operator or owner['id'] == actor_id),
                'can_settle': not closed and open_dispute and eligible_settler}

    def week(self, start: str | None, actor: dict | None, *, _include_hidden=False) -> dict:
        with self.store.lock:
            data = league.result(self.store, start)
            closed = data['week']['closed']
            residents = data.get('residents', self.store.residents())
            evidence = data.get('evidence') if closed else self.evidence_for([d['id'] for d in data['dishes']])
            # Expiry/dismissal of media is live even for a frozen score: a snapshot cannot resurrect a deleted photo.
            evidence = [e | {'expired': current['expired'], 'expires_at': current['expires_at']}
                        for e in evidence if (current := self.store.evidence(e['id']))]
            dishes = [self.dish(d, residents, data['rules'], actor=actor, closed=closed, evidence=evidence) for d in data['dishes']]
            raw = {d['id']: d for d in data['dishes']}
            basis = data.get('guess_basis') if closed else self.store.suggestion_basis(list(raw))
            visits = self.visits(data['dishes'], dishes, evidence, residents, basis, actor=actor)
            hidden = set() if _include_hidden else {r[0] for r in self.store.conn.execute('SELECT visit_id FROM house_hidden_visits WHERE resident_id=?', (actor['id'] if actor else 0,))}
            hidden_dishes = {oid for v in visits if v['id'] in hidden for oid in v['dish_ids']}
            visits = [v for v in visits if v['id'] not in hidden]
            dishes = [d for d in dishes if d['id'] not in hidden_dishes]
            people = {r['id']: {'id': r['id'], 'name': r['name']} for r in residents}
            verdicts = [{'visit_id': v['visit_id'], 'title': v['title'], 'verdict': v['verdict'],
                         'by': people.get(v['by_id']), 'at': v['at'], 'of_visit_id': v['of_visit_id']}
                        for v in data.get('verdicts', [])]
            current = league.week_bounds()
            first = parse_stamp(stamp(league.week_times(current)[0]))
            weeks = []
            for offset in range(8):
                start_date = (first.astimezone(league.timezone()).date() - timedelta(weeks=offset)).isoformat()
                row = self.store.conn.execute("SELECT json_extract(snapshot,'$.pick.name') FROM league_weeks WHERE start=?", (start_date,)).fetchone()
                weeks.append({'start': start_date, 'pick_name': row[0] if row else None, 'closed': bool(row)})
            return {k: data[k] for k in ('week', 'rules', 'standings', 'pick', 'on_duty')} | {
                'dishes': dishes, 'visits': visits, 'unnamed_count': sum(d['resident'] is None for d in dishes),
                'unreviewed_count': sum(v['resident'] is None for v in visits),
                'verdicts': verdicts, 'weeks': weeks}

    def visits(self, raw_dishes, dishes, evidence, residents, basis, *, actor):
        raw = {d['id']: d for d in raw_dishes}
        formatted = {d['id']: d for d in dishes}
        people = {r['id']: r for r in residents}
        visits = []
        for group in house_visits.groups(raw_dishes, evidence):
            guesses = {raw[oid]['suggested_resident_id'] for oid in group['dish_ids']}
            guess_id = next(iter(guesses)) if len(guesses) == 1 else None
            person = people.get(guess_id)
            guess = {'resident_id': person['id'], 'name': person['name'],
                     'basis': next((basis.get(str(oid), basis.get(oid)) for oid in group['dish_ids']
                                    if basis.get(str(oid), basis.get(oid))), 'Appearance suggestion; check the arrival photos.')} if person else None
            filmstrip = group['filmstrip']
            url = None
            if filmstrip and not filmstrip['expired'] and parse_stamp(filmstrip['expires_at']) > utcnow():
                url = self.signed_url(filmstrip) if actor else f"/api/evidence/{filmstrip['id']}"
            visits.append(house_visits.serialize(group, raw, formatted, guess=guess, filmstrip_url=url))
        return visits

    def visit_for_dish(self, oid: int) -> dict | None:
        for row in self.store.conn.execute("""SELECT * FROM evidence WHERE kind='filmstrip' AND label='arrival'
            AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids') WHERE value=?)
            ORDER BY created_at DESC,id DESC""", (oid,)):
            group = house_visits.metadata(dict(row))
            if group and oid in group['dish_ids']:
                return group
        return None

    def change_visit(self, visit_id: str, action: str, body: dict, actor: dict | None) -> dict:
        store = self.store
        with store.lock:
            league.close_weeks(store)
            group = self.visit_group(visit_id)
            with store.conn:
                at = utcnow()
                for oid in group['dish_ids']:
                    self.change(oid, action, body, actor, _in_visit=True, _at=at)
            first = store.obligation(group['dish_ids'][0])
            week = league.week_bounds(now=parse_stamp(first['due_at']))['start']
            return next(v for v in self.week(week, actor, _include_hidden=True)['visits'] if v['id'] == visit_id)

    def visit_group(self, visit_id: str, *, allow_member=False) -> dict:
        for row in self.store.conn.execute("SELECT * FROM evidence WHERE kind='filmstrip' AND label='arrival' AND CAST(json_extract(capture,'$.visit_id') AS TEXT)=? ORDER BY created_at DESC,id DESC", (visit_id,)):
            if group := house_visits.metadata(dict(row)):
                return group
        if visit_id.startswith('dish-') and visit_id[5:].isdigit():
            oid = int(visit_id[5:])
            if not allow_member and self.visit_for_dish(oid):
                raise HTTPException(409, 'This dish belongs to a visit. Refresh the week and name the visit together.')
            return {'id': visit_id, 'dish_ids': [oid]}
        raise HTTPException(404, 'That visit was not found.')

    def verdict(self, visit_id: str, body: Verdict, actor: dict | None) -> dict:
        if body.verdict not in ('not_dish', 'clean', 'duplicate', 'cant_tell'):
            raise HTTPException(400, 'Choose not_dish, clean, duplicate, or cant_tell.')
        if body.verdict != 'duplicate' and body.of_visit_id is not None:
            raise HTTPException(400, 'Only duplicate answers need another visit.')
        store = self.store
        with store.lock:
            league.close_weeks(store)
            group = self.visit_group(visit_id)
            items = [store.obligation(oid) for oid in group['dish_ids']]
            for item in items:
                try:
                    league.assert_unfrozen(store, item)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from None
            if not items[0]['due_at']:
                raise HTTPException(404, 'That visit is not in the house league.')
            week = league.week_bounds(now=parse_stamp(items[0]['due_at']))['start']
            # Ignore this user's skip while retaining their signed-media/permission contract.
            current = next((v for v in self.week(week, actor, _include_hidden=True)['visits'] if v['id'] == visit_id), None)
            if current is None or set(current['dish_ids']) != set(group['dish_ids']):
                raise HTTPException(409, 'Some dishes in this visit cannot be reviewed. Refresh the week.')
            actor_id = actor['id'] if actor else None
            target = None
            if body.verdict == 'duplicate':
                if not body.of_visit_id or body.of_visit_id == visit_id:
                    raise HTTPException(400, 'Choose the other visit that shows this dish.')
                target = self.visit_group(body.of_visit_id, allow_member=True)
                if set(target['dish_ids']) & set(group['dish_ids']):
                    raise HTTPException(400, 'A visit cannot duplicate itself.')
                targets = [store.obligation(oid) for oid in target['dish_ids']]
                for target_item in targets:
                    bounds = league.week_bounds(now=parse_stamp(target_item['due_at'])) if target_item['due_at'] else None
                    if not bounds or not any(d['id'] == target_item['id'] for d in league.left_out(store, *league.week_times(bounds))):
                        raise HTTPException(400, 'Choose an original visit that is still in the league.')
            with store.conn:
                if body.verdict == 'cant_tell':
                    store.conn.execute('INSERT OR IGNORE INTO house_hidden_visits VALUES(?,?)', (actor_id or 0, visit_id))
                else:
                    before_keys = ('resident_id', 'lifecycle', 'confidence', 'resolved_at', 'cleaner_id',
                                   'reason', 'review_pending', 'overdue_verified', 'attribution', 'named_by', 'named_at')
                    store.conn.execute('INSERT OR REPLACE INTO house_verdict_before VALUES(?,?)',
                        (visit_id, json.dumps([{'id': item['id'], **{k: item[k] for k in before_keys}} for item in items])))
                    if body.verdict == 'not_dish':
                        store.mark_not_dish(group['dish_ids'], actor_id)
                    elif body.verdict == 'clean':
                        store.mark_clean(group['dish_ids'], actor_id)
                    else:
                        for item in items:
                            kinds = {d['kind'] for d in item['dishes']}
                            # A person can correct a detector kind error by choosing one exact
                            # original dish. Never require matching detector labels for that.
                            matching = targets if len(targets) == 1 else [t for t in targets if {d['kind'] for d in t['dishes']} == kinds]
                            if len(matching) != 1:
                                raise HTTPException(400, 'Choose the specific original dish from that visit.')
                            store.mark_duplicate(item['id'], matching[0]['id'], actor_id)
                    # Also keep an API-owned exclusion when Lane G replaces the adapters.
                    for item in items:
                        if not store.conn.execute('SELECT 1 FROM house_verdicts WHERE obligation_id=?', (item['id'],)).fetchone():
                            house_verdicts.mark(store, [item['id']], body.verdict, actor_id)
                    store.conn.execute('INSERT OR REPLACE INTO house_visit_verdicts VALUES(?,?,?,?,?,?,?)',
                        (visit_id, body.verdict, actor_id, stamp(), body.of_visit_id, json.dumps(group['dish_ids']), current['title']))
                    store._event(None, 'house_visit_verdict', f"{actor['name'] if actor else 'The operator'} marked {current['title']}: {body.verdict}.",
                        {'visit_id': visit_id, 'obligation_ids': group['dish_ids'], 'verdict': body.verdict, 'by': actor_id, 'of_visit_id': body.of_visit_id})
            return current | {'points': 0 if body.verdict != 'cant_tell' else current['points'],
                'hidden': body.verdict == 'cant_tell', 'verdict': None if body.verdict == 'cant_tell' else {
                    'verdict': body.verdict, 'by': {'id': actor['id'], 'name': actor['name']} if actor else None,
                    'at': stamp(), 'of_visit_id': body.of_visit_id},
                'can_name': False, 'can_dispute': False, 'can_settle': False}

    def undo_verdict(self, visit_id: str) -> dict:
        store = self.store
        with store.lock:
            row = store.conn.execute('SELECT * FROM house_visit_verdicts WHERE visit_id=?', (visit_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'That verdict was not found.')
            ids = json.loads(row['obligation_ids'])
            league.close_weeks(store)
            for oid in ids:
                try:
                    league.assert_unfrozen(store, store.obligation(oid))
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from None
            with store.conn:
                previous = store.conn.execute('SELECT data FROM house_verdict_before WHERE visit_id=?', (visit_id,)).fetchone()
                if previous:
                    for item in json.loads(previous[0]):
                        keys = [k for k in item if k != 'id']
                        store.conn.execute(f"UPDATE obligations SET {','.join(k+'=?' for k in keys)} WHERE id=?", [*(item[k] for k in keys), item['id']])
                    store.conn.execute('DELETE FROM house_verdict_before WHERE visit_id=?', (visit_id,))
                store.conn.executemany('DELETE FROM house_verdicts WHERE obligation_id=?', [(oid,) for oid in ids])
                store.conn.execute('DELETE FROM house_visit_verdicts WHERE visit_id=?', (visit_id,))
                store._event(None, 'house_visit_verdict_undone', 'The operator reversed a visit verdict.', {'visit_id': visit_id, 'obligation_ids': ids})
            return {'ok': True}

    def change(self, oid: int, action: str, body: dict, actor: dict | None, *, _in_visit=False, _at=None) -> dict:
        store, now = self.store, _at or utcnow()
        with store.lock:
            if not _in_visit:
                group = self.visit_for_dish(oid)
                if group:
                    self.change_visit(group['id'], action, body, actor)
                    return self.dish(store.obligation(oid), store.residents(), league.rules(store), actor=actor,
                                     closed=False, evidence=self.evidence_for([oid]))
                league.close_weeks(store, now=now)
            item = store.obligation(oid)
            try:
                league.assert_unfrozen(store, item)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            week = league.week_bounds(now=parse_stamp(item['due_at'])) if item['due_at'] else None
            eligible = next((d for d in league.left_out(store, *league.week_times(week)) if d['id'] == oid), None) if week else None
            if not eligible:
                raise HTTPException(404, 'That dish is not in the house league.')
            item['human_named'] = eligible['human_named']
            with nullcontext() if _in_visit else store.conn:
                operator = actor is None
                actor_id = actor['id'] if actor else None
                named = item['resident_id'] is not None and item['confidence'] == 'confirmed' and league.human_named(item)
                disputed = item['dispute_state'] == 'open' or item['lifecycle'] == 'disputed'
                if action == 'name':
                    if disputed:
                        raise HTTPException(409, 'Settle the dispute before naming this dish.')
                    if named and not operator:
                        raise HTTPException(409, 'This dish is already named. The named person can dispute it.')
                    resident = store._resident(body['resident_id'])
                    store._action_locked(oid, 'confirm', resident_id=resident['id'], note=item['reason'], now=now)
                    store.conn.execute('UPDATE obligations SET named_by=?,named_at=? WHERE id=?', (actor_id, stamp(now), oid))
                    store._event(oid, 'house_named', f"Named as {resident['name']} by {actor['name'] if actor else 'the operator'}.",
                                 {'resident_id': resident['id'], 'actor_id': actor_id}, at=now)
                elif action == 'dispute':
                    if not named or (not operator and actor_id != item['resident_id']):
                        raise HTTPException(403, 'Only the person named can dispute this dish.')
                    if disputed:
                        raise HTTPException(409, 'That dispute is already open.')
                    store._action_locked(oid, 'dispute', note=body['note'].strip(), now=now)
                    store.conn.execute('UPDATE obligations SET dispute_previous=? WHERE id=?',
                                       (json.dumps({'reason': item['reason']}), oid))
                    store.conn.execute("UPDATE obligations SET dispute_state='open',dispute_by=?,dispute_note=?,dispute_at=?,dispute_settled_by=NULL,dispute_settled_at=NULL WHERE id=?",
                                       (item['resident_id'], body['note'].strip(), stamp(now), oid))
                    store._event(oid, 'house_disputed', "That wasn't me; points paused while the house checks.", {'actor_id': actor_id}, at=now)
                else:
                    if body['decision'] not in ('them', 'not_them'):
                        raise HTTPException(400, 'Choose “them” or “not_them”.')
                    if not disputed:
                        raise HTTPException(409, 'There is no open dispute on this dish.')
                    if not operator and actor_id in (item['dispute_by'] or item['resident_id'], item['named_by']):
                        raise HTTPException(403, 'Another housemate must settle this one: neither the disputer nor the person who named it.')
                    upheld = body['decision'] == 'them'
                    lifecycle = 'resolved' if item['resolved_at'] else 'open'
                    previous = json.loads(item['dispute_previous'] or '{}')
                    store.conn.execute('''UPDATE obligations SET dispute_state=?,dispute_settled_by=?,dispute_settled_at=?,
                        lifecycle=?,review_pending=0,resident_id=?,confidence=?,named_by=?,named_at=? WHERE id=?''',
                        ('upheld' if upheld else 'overturned', actor_id, stamp(now), lifecycle,
                         item['resident_id'] if upheld else None, 'confirmed' if upheld else 'unknown',
                         item['named_by'] if upheld else None, item['named_at'] if upheld else None, oid))
                    if 'reason' in previous:
                        store.conn.execute('UPDATE obligations SET reason=? WHERE id=?', (previous['reason'], oid))
                    store.conn.execute('UPDATE person_samples SET resident_id=?,hint_resident_id=NULL WHERE obligation_id=?',
                                       (item['resident_id'] if upheld else None, oid))
                    store._refresh_identity_suggestions()
                    store._event(oid, 'house_dispute_settled', 'It was them; points restored.' if upheld else 'Not them; back to unnamed.',
                                 {'actor_id': actor_id, 'decision': body['decision'], 'resident_id': item['resident_id']}, at=now)
                updated = store.obligation(oid)
                updated['human_named'] = item['human_named'] or bool(updated['named_at'])
                return self.dish(updated, store.residents(), league.rules(store), actor=actor, closed=False, evidence=self.evidence_for([oid]))

    def join_link(self, resident_id: int) -> dict:
        if not self.config.site_origin:
            raise HTTPException(503, 'Set HOUSE_SITE_ORIGIN before making a sign-in link.')
        token = secrets.token_urlsafe(32)
        expires = stamp(utcnow() + timedelta(days=7))
        with self.join_lock, self.store.lock, self.store.conn:
            resident = self.store._resident(resident_id)
            self.store.conn.execute('DELETE FROM house_join_tokens WHERE resident_id=?', (resident_id,))
            self.store.conn.execute('INSERT INTO house_join_tokens VALUES(?,?,?,NULL)',
                                    (hashlib.sha256(token.encode()).hexdigest(), resident_id, expires))
            self.store._event(None, 'house_join_link_created', f"One-time sign-in link created for {resident['name']}.", {'resident_id': resident_id})
        return {'url': f'{self.config.site_origin}/join#{token}', 'expires_at': expires}

    def join(self, body: Join) -> dict:
        # Serialize redemption/revocation in the Jetson's single server process. Network
        # waits never hold Store.lock: camera recording/review must continue during sign-up.
        with self.join_lock:
            with self.store.lock, self.store.conn:
                row = self.store.conn.execute('''SELECT r.*,t.expires_at,t.used_at FROM house_join_tokens t
                    JOIN residents r ON r.id=t.resident_id WHERE t.token_hash=? AND r.active=1''',
                    (hashlib.sha256(body.token.encode()).hexdigest(),)).fetchone()
                if not row or row['used_at'] or parse_stamp(row['expires_at']) <= utcnow():
                    raise HTTPException(400, 'This sign-in link expired or was already used. Ask for a new one.')
                resident = dict(row)
                slug = slug_for(self.store, resident)
            auth_id = self.admin.account(resident, slug, body.password)
            with self.store.lock, self.store.conn:
                self.store._resident(resident['id'])  # Archiving during sign-up must not grant access.
                self.store.conn.execute('UPDATE residents SET auth_user_id=? WHERE id=?', (auth_id, resident['id']))
                self.store.conn.execute('UPDATE house_join_tokens SET used_at=? WHERE token_hash=?',
                                        (stamp(), hashlib.sha256(body.token.encode()).hexdigest()))
                self.store._event(None, 'house_account_ready', f"House sign-in saved for {resident['name']}.", {'resident_id': resident['id']})
        return {'login': slug}

    def media_file(self, evidence_id: str, exp: int, sig: str):
        now = utcnow()
        expected = hmac.new(self.media_key, f'{evidence_id}:{exp}'.encode(), hashlib.sha256).hexdigest()
        if exp <= int(now.timestamp()) or exp > int(now.timestamp()) + 1800 or not hmac.compare_digest(expected, sig):
            raise HTTPException(403, 'That photo link expired or is invalid. Refresh the week to see it again.')
        store = self.store
        with store.lock:
            evidence = store.evidence(evidence_id)
            if not evidence or evidence['kind'] not in ('snapshot', 'filmstrip') or not evidence['obligation_id']:
                raise HTTPException(404, 'Photo not found.')
            visible = False
            group = house_visits.metadata(evidence)
            ids = group['dish_ids'] if group else [evidence['obligation_id']]
            for oid in ids:
                item = store.obligation(oid)
                if not item['due_at']:
                    continue
                start = league.week_bounds(now=parse_stamp(item['due_at']))['start']
                result = league.result(store, start)
                visible = any(d['id'] == item['id'] for d in result['dishes'])
                if result['week']['closed']:
                    visible = visible and any(e['id'] == evidence_id for e in result['evidence'])
                if visible:
                    break
            if not visible:
                raise HTTPException(404, 'Photo not found in the house league.')
            if evidence['expired'] or parse_stamp(evidence['expires_at']) <= now:
                raise HTTPException(404, 'This photo is no longer kept.')
            path = self.media.safe_path(evidence['filename'])
            if not path.is_file():
                raise HTTPException(404, 'This photo is no longer kept.')
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})


def install(app: FastAPI, authenticated, *, config: HouseConfig) -> House:
    house = House(app.state.store, app.state.media, config)
    app.state.house = house
    app.add_middleware(HouseBoundary, site_origin=house.config.site_origin)
    router = APIRouter(prefix='/house')

    @router.get('/api/me')
    def me(resident=Depends(house.resident)):
        return {'resident': {'id': resident['id'], 'name': resident['name']}, 'house': 'Highest House'}

    @router.get('/api/week')
    def week(start: str | None = None, resident=Depends(house.resident)):
        return house.week(start, resident)

    @router.post('/api/dishes/{oid}/name')
    def name(oid: int, body: Name, resident=Depends(house.resident)):
        return house.change(oid, 'name', body.model_dump(), resident)

    @router.post('/api/dishes/{oid}/dispute')
    def dispute(oid: int, body: Dispute, resident=Depends(house.resident)):
        return house.change(oid, 'dispute', body.model_dump(), resident)

    @router.post('/api/dishes/{oid}/settle')
    def settle(oid: int, body: Settle, resident=Depends(house.resident)):
        return house.change(oid, 'settle', body.model_dump(), resident)

    @router.post('/api/visits/{visit_id}/name')
    def name_visit(visit_id: str, body: Name, resident=Depends(house.resident)):
        return house.change_visit(visit_id, 'name', body.model_dump(), resident)

    @router.post('/api/visits/{visit_id}/dispute')
    def dispute_visit(visit_id: str, body: Dispute, resident=Depends(house.resident)):
        return house.change_visit(visit_id, 'dispute', body.model_dump(), resident)

    @router.post('/api/visits/{visit_id}/settle')
    def settle_visit(visit_id: str, body: Settle, resident=Depends(house.resident)):
        return house.change_visit(visit_id, 'settle', body.model_dump(), resident)

    @router.post('/api/visits/{visit_id}/verdict')
    def verdict_visit(visit_id: str, body: Verdict, resident=Depends(house.resident)):
        return house.verdict(visit_id, body, resident)

    @router.post('/api/join')
    def join(body: Join, request: Request):
        if app.state.config.demo or app.state.config.snapshot:
            raise HTTPException(403, 'House accounts are unavailable in demo and snapshot mode.')
        house.limiter.check('join:' + (request.headers.get('cf-connecting-ip') or (request.client.host if request.client else 'local')), count=5, seconds=900)
        return house.join(body)

    @router.get('/api/login-names')
    def login_names():
        with house.store.lock, house.store.conn:
            return [{'slug': slug_for(house.store, r), 'name': r['name']} for r in house.store.residents() if r['active'] and r['auth_user_id']]

    @router.get('/media/{evidence_id}')
    def media(evidence_id: str, exp: int, sig: str):
        return house.media_file(evidence_id, exp, sig)

    app.include_router(router)
    operator = APIRouter(prefix='/api/league', dependencies=[Depends(authenticated)])

    @operator.get('/week')
    def operator_week(start: str | None = None):
        return house.week(start, None)

    @operator.patch('/rules')
    def configure(body: Rules):
        return league.configure(house.store, body.model_dump())

    @operator.get('/config')
    def league_config():
        with house.store.lock:
            league.close_weeks(house.store)
            return {'rules': league.rules(house.store), 'on_duty': league.duty(house.store)}

    @operator.post('/duty')
    def override(body: Override):
        return {'on_duty': league.override(house.store, body.resident_id)}

    @operator.post('/dishes/{oid}/name')
    def operator_name(oid: int, body: Name):
        return house.change(oid, 'name', body.model_dump(), None)

    @operator.post('/dishes/{oid}/settle')
    def operator_settle(oid: int, body: Settle):
        return house.change(oid, 'settle', body.model_dump(), None)

    @operator.post('/visits/{visit_id}/name')
    def operator_name_visit(visit_id: str, body: Name):
        return house.change_visit(visit_id, 'name', body.model_dump(), None)

    @operator.post('/visits/{visit_id}/settle')
    def operator_settle_visit(visit_id: str, body: Settle):
        return house.change_visit(visit_id, 'settle', body.model_dump(), None)

    @operator.post('/visits/{visit_id}/verdict')
    def operator_verdict_visit(visit_id: str, body: Verdict):
        return house.verdict(visit_id, body, None)

    @operator.post('/visits/{visit_id}/undo-verdict')
    def undo_verdict(visit_id: str):
        return house.undo_verdict(visit_id)

    @operator.get('/accounts')
    def accounts():
        return [{'resident_id': r['id'], 'has_account': bool(r['auth_user_id'])} for r in house.store.residents() if r['active']]

    @operator.post('/residents/{resident_id}/join-link')
    def join_link(resident_id: int):
        return house.join_link(resident_id)

    app.include_router(operator)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        if request.url.path.startswith('/house/'):
            return JSONResponse({'detail': 'Check the request fields. Passwords need at least 8 characters; notes allow up to 1,000.'}, status_code=400)
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)

    return house
