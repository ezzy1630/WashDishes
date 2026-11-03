"""Small house-facing API, separate from the private operator/camera dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager, nullcontext, suppress
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from . import house_verdicts, house_visits, league
from .house_auth import HouseConfig, JWTVerifier, SupabaseAdmin
from .house_schema import slug_for
from .store import parse_stamp, stamp, utcnow


@lru_cache(maxsize=2048)
def _jpeg_size(path: Path, modified: int, size: int) -> tuple[int, int]:
    # File identity invalidates replacements; Pillow reads only the JPEG header.
    with Image.open(path) as image:
        return image.size


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


class ReadBudget:
    """Bound admitted work and concurrent workers without waiting on Store.lock."""
    def __init__(self, *, per_identity: int, total: int, concurrent: int, per_concurrent: int = 1):
        self.rate = RateLimit()
        self.per_identity, self.total = per_identity, total
        self.slots = threading.BoundedSemaphore(concurrent)
        self.lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.per_concurrent = per_concurrent

    @contextmanager
    def admit(self, identity: str):
        self.rate.check(identity, count=self.per_identity)
        self.rate.check('global', count=self.total)
        with self.lock:
            if self.active.get(identity, 0) >= self.per_concurrent or not self.slots.acquire(blocking=False):
                raise HTTPException(429, 'The house is busy. Try again shortly.')
            self.active[identity] = self.active.get(identity, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                self.active[identity] -= 1
                if not self.active[identity]:
                    del self.active[identity]
                self.slots.release()


def client_identity(request: Request) -> str:
    # Only the dedicated loopback tunnel socket carries trusted edge metadata.
    value = request.headers.get('cf-connecting-ip')
    if request.scope.get('server', (None, None))[1] == request.app.state.house_tunnel_port and value:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise HTTPException(400, 'The client address is invalid.') from None
    return request.client.host if request.client else 'local'


class HouseBoundary:
    """Tunnel headers cannot reach the operator app; CORS is scoped to /house/."""
    def __init__(self, app, site_origin: str, tunnel_port: int):
        self.app, self.site_origin, self.tunnel_port = app, site_origin, tunnel_port

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        house = scope['path'].startswith('/house/')
        tunnel = scope.get('server', (None, None))[1] == self.tunnel_port
        if (tunnel or 'cf-connecting-ip' in headers) and not house:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'Only the house app is available here.'}, status_code=404 if tunnel else 403)(scope, receive, send)
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
        self.join_limiter = RateLimit()
        self.week_budget = ReadBudget(per_identity=60, total=240, concurrent=2)
        self.media_budget = ReadBudget(per_identity=120, total=600, concurrent=8, per_concurrent=8)
        self.media_visibility: OrderedDict[str, bool] = OrderedDict()
        self.media_revision = None
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
                obligation_id IN ({','.join('?' for _ in ids)}) OR id IN (
                    SELECT v.evidence_id FROM arrival_visits v JOIN arrival_visit_members m ON m.visit_id=v.id
                    WHERE m.obligation_id IN ({','.join('?' for _ in ids)})) OR (kind='filmstrip' AND label='arrival'
                AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids')
                    WHERE value IN ({','.join('?' for _ in ids)})))) ORDER BY created_at,id""", [*ids, *ids, *ids])]

    def image_size(self, evidence, url) -> dict:
        if not evidence or not url:
            return {'width': None, 'height': None}
        try:
            path = self.store.root / 'media' / evidence['filename']
            info = path.stat()
            width, height = _jpeg_size(path, info.st_mtime_ns, info.st_size)
            return {'width': width, 'height': height}
        except (OSError, ValueError):
            return {'width': None, 'height': None}

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
        photos = [e for e in reversed(media) if e['kind'] == 'snapshot' and not e['expired'] and parse_stamp(e['expires_at']) > utcnow()]
        # The site's “later photo” must be the matching deadline observation when retained.
        photo = next((e for e in photos if e['label'] == 'Matching dish still visible after the deadline'), None)
        photo = photo or next((e for e in photos if e['label'] == 'Dish left unattended'), None)
        photo = photo or next(iter(photos), None)
        open_dispute = item.get('dispute_state') == 'open' or item['lifecycle'] == 'disputed'
        operator = actor is None
        actor_id = actor['id'] if actor else None
        eligible_settler = operator or actor_id not in (item.get('dispute_by') or item.get('resident_id'), item.get('named_by'))
        def photo_url(evidence):
            if not evidence or evidence['expired'] or parse_stamp(evidence['expires_at']) <= utcnow():
                return None
            return f"/api/evidence/{evidence['id']}" if operator else self.signed_url(evidence)
        strip_url, still_url = photo_url(filmstrip), photo_url(photo)
        return {'id': item['id'], 'kind': item['dishes'][0]['kind'],
                'title': self.store.dish_description(item['dishes']), 'location': item['location'],
                'out_since': item['created_at'], 'due_at': item['due_at'], 'gone_at': item['resolved_at'],
                'points': 0 if open_dispute else league.points(item, settings), 'resident': owner,
                'named_by': by, 'named_at': item.get('named_at'), 'dispute': dispute,
                'filmstrip_url': strip_url, 'photo_url': still_url,
                **{f'filmstrip_{k}': v for k, v in self.image_size(filmstrip, strip_url).items()},
                **{f'photo_{k}': v for k, v in self.image_size(photo, still_url).items()},
                'can_name': not closed and not open_dispute and (operator or owner is None),
                'can_dispute': not closed and owner is not None and not open_dispute and (operator or owner['id'] == actor_id),
                'can_settle': not closed and open_dispute and eligible_settler}

    def week(self, start: str | None, actor: dict | None, *, _include_hidden=False) -> dict:
        # Copy a week-scoped snapshot under the camera lock. Signing, formatting,
        # JPEG header I/O and response serialization must never hold that lock.
        with self.store.lock:
            data = league.snapshot(self.store, start)
            closed = data['week']['closed']
            residents = data.get('residents') or self.store.residents()
            evidence = data.get('evidence', []) if closed else self.evidence_for([d['id'] for d in data['dishes']])
            live = {r['id']: dict(r) for r in self.store.conn.execute(
                'SELECT id,expired,expires_at,filename FROM evidence WHERE id IN (SELECT value FROM json_each(?))',
                (json.dumps([e['id'] for e in evidence]),))}
            evidence = [e | live[e['id']] for e in evidence if e['id'] in live]
            raw = {d['id']: d for d in data['dishes']}
            basis = data.get('guess_basis', {}) if closed else self.store.suggestion_basis(list(raw))
            hidden = set() if _include_hidden else {r[0] for r in self.store.conn.execute('SELECT visit_id FROM house_hidden_visits WHERE resident_id=?', (actor['id'] if actor else 0,))}
            first = league.week_times(league.week_bounds())[0]
            history = {r['start']: r['pick_name'] for r in self.store.conn.execute(
                "SELECT start,json_extract(snapshot,'$.pick.name') pick_name FROM league_weeks WHERE start>=?", ((first.date() - timedelta(weeks=7)).isoformat(),))}
        data = league.project(data)
        evidence_by_dish = {}
        for entry in evidence:
            evidence_by_dish.setdefault(entry['obligation_id'], []).append(entry)
        dishes = [self.dish(d, residents, data['rules'], actor=actor, closed=closed, evidence=evidence_by_dish.get(d['id'], [])) for d in data['dishes']]
        visits = self.visits(data['dishes'], dishes, evidence, residents, basis, actor=actor,
                             groups=data.get('visit_groups'), duplicates=data.get('duplicates'),
                             alias_guesses=data.get('alias_guesses', {}))
        hidden_dishes = {oid for v in visits if v['id'] in hidden for oid in v['dish_ids']}
        visits = [v for v in visits if v['id'] not in hidden]
        dishes = [d for d in dishes if d['id'] not in hidden_dishes]
        people = {r['id']: {'id': r['id'], 'name': r['name']} for r in residents}
        verdicts = [{'visit_id': v['visit_id'], 'title': v['title'], 'kind': v['verdict'],
                     'by': people.get(v['by_id']), 'at': v['at'], 'of_visit_id': v['of_visit_id']}
                    for v in data.get('verdicts', [])]
        weeks = []
        for offset in range(8):
            start_date = (first.date() - timedelta(weeks=offset)).isoformat()
            weeks.append({'start': start_date, 'pick_name': history.get(start_date), 'closed': start_date in history})
        return {k: data[k] for k in ('week', 'rules', 'standings', 'pick', 'on_duty')} | {
            'dishes': dishes, 'visits': visits, 'unnamed_count': sum(d['resident'] is None for d in dishes),
            'unreviewed_count': sum(v['resident'] is None for v in visits),
            'verdicts': verdicts, 'weeks': weeks}

    def visits(self, raw_dishes, dishes, evidence, residents, basis, *, actor, groups=None, duplicates=None, alias_guesses=None):
        raw = {d['id']: d for d in raw_dishes}
        formatted = {d['id']: d for d in dishes}
        people = {r['id']: r for r in residents}
        visits = []
        evidence_by_id = {e['id']: e for e in evidence}
        for group in house_visits.groups(raw_dishes, evidence, visits=groups, duplicates=duplicates):
            guess_ids = [*group['dish_ids'], *group['alias_ids']]
            guesses = {raw[oid]['suggested_resident_id'] if oid in raw else (alias_guesses or {}).get(str(oid), (alias_guesses or {}).get(oid))
                       for oid in guess_ids}
            guess_id = next(iter(guesses)) if len(guesses) == 1 else None
            person = people.get(guess_id)
            guess = {'resident_id': person['id'], 'name': person['name'],
                     'basis': next((basis.get(str(oid), basis.get(oid)) for oid in group['dish_ids']
                                    if basis.get(str(oid), basis.get(oid))), 'Appearance suggestion; check the arrival photos.')} if person else None
            filmstrip = group['filmstrip']
            # Refresh retention from live evidence; never fall back to a superseded strip.
            filmstrip = evidence_by_id.get(filmstrip['id']) if filmstrip else None
            url = None
            if filmstrip and not filmstrip['expired'] and parse_stamp(filmstrip['expires_at']) > utcnow():
                url = self.signed_url(filmstrip) if actor else f"/api/evidence/{filmstrip['id']}"
            for oid in group['dish_ids']:
                formatted[oid]['filmstrip_url'] = url
                formatted[oid].update({f'filmstrip_{k}': v for k, v in self.image_size(filmstrip, url).items()})
            visits.append(house_visits.serialize(group, raw, formatted, guess=guess, filmstrip_url=url))
        return visits

    def visit_for_dish(self, oid: int) -> dict | None:
        visits = house_visits.catalog(self.store)
        aliases = house_visits.duplicate_map(self.store, visits)
        canonical = aliases.get(oid, oid)
        for group in visits:
            if canonical in group['dish_ids']:
                return group | {'dish_ids': [i for i in group['dish_ids'] if i not in aliases]}
        if oid in aliases:
            return {'id': f'dish-{canonical}', 'dish_ids': [canonical]}
        return None

    def change_visit(self, visit_id: str, action: str, body: dict, actor: dict | None) -> dict:
        store = self.store
        with store.lock:
            league.close_weeks(store)
            group = self.visit_group(visit_id)
            first = store.obligation(group['dish_ids'][0])
            try:
                league.assert_unfrozen(store, first)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            week = league.week_bounds(now=parse_stamp(first['followup_at']))['start']
            current = next((v for v in self.week(week, actor, _include_hidden=True)['visits'] if v['id'] == visit_id), None)
            if current is None or set(current['dish_ids']) != set(group['dish_ids']):
                raise HTTPException(409, 'Some dishes in this visit cannot be reviewed. Refresh the week.')
            with store.conn:
                at = utcnow()
                if action == 'name' or (action == 'settle' and body['decision'] == 'them'):
                    filmstrip = group.get('filmstrip')
                    capture = json.loads(filmstrip['capture']) if filmstrip else {}
                    evidence_id = capture.get('identity_evidence_id')
                    # A name on this visit must never label a camera crop from
                    # a later passer-by or an ambiguous arrival. One visit has
                    # one teaching capture, even when it contains several dishes.
                    for oid in group['dish_ids']:
                        source = store.conn.execute("""SELECT * FROM evidence WHERE id=? AND obligation_id=? AND kind='snapshot'
                            AND json_extract(capture,'$.identity_source')='arrival_visit'
                            AND json_extract(capture,'$.visit_id')=?""", (evidence_id, oid, visit_id)).fetchone()
                        identity = json.loads(source['capture']) if source else {}
                        # A later proof may have replaced the active capture.
                        # Restore this strip's cues without committing the review transaction.
                        store._replace_person_samples_locked(oid, identity.get('cues', {}), evidence_id or '',
                            at=parse_stamp(source['created_at']) if source else at, context=identity.get('context'))
                for oid in group['dish_ids']:
                    self.change(oid, action, body, actor, _in_visit=True, _at=at)
            first = store.obligation(group['dish_ids'][0])
            week = league.week_bounds(now=parse_stamp(first['followup_at']))['start']
            return next(v for v in self.week(week, actor, _include_hidden=True)['visits'] if v['id'] == visit_id)

    def visit_group(self, visit_id: str, *, allow_member=False) -> dict:
        visits = house_visits.catalog(self.store)
        aliases = house_visits.duplicate_map(self.store, visits)
        for group in visits:
            if group['id'] == visit_id:
                members = [oid for oid in group['dish_ids'] if oid not in aliases]
                if not members:
                    raise HTTPException(409, 'This visit contains only duplicates. Review the original visit.')
                return group | {'dish_ids': members}
        if self.store.conn.execute("SELECT 1 FROM arrival_visits WHERE id=?", (visit_id,)).fetchone() or any(
                (group := house_visits.metadata(dict(row))) and group['id'] == visit_id
                for row in self.store.conn.execute("SELECT * FROM evidence WHERE kind='filmstrip' AND label='arrival'")):
            raise HTTPException(409, 'That visit was superseded. Refresh the week before reviewing it.')
        if visit_id.startswith('dish-') and visit_id[5:].isdigit():
            oid = int(visit_id[5:])
            if not allow_member and self.visit_for_dish(oid):
                raise HTTPException(409, 'This dish belongs to a visit. Refresh the week and name the visit together.')
            if oid in aliases:
                oid = aliases[oid]
            return {'id': f'dish-{oid}', 'dish_ids': [oid]}
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
            if not items[0]['followup_at']:
                raise HTTPException(404, 'That visit is not in the house league.')
            week = league.week_bounds(now=parse_stamp(items[0]['followup_at']))['start']
            # Ignore this user's skip while retaining their signed-media/permission contract.
            current = next((v for v in self.week(week, actor, _include_hidden=True)['visits'] if v['id'] == visit_id), None)
            if current is None or set(current['dish_ids']) != set(group['dish_ids']):
                raise HTTPException(409, 'Some dishes in this visit cannot be reviewed. Refresh the week.')
            actor_id = actor['id'] if actor else None
            reviewer = f'resident:{actor_id}' if actor else 'operator'
            with store.dish_corrections():
                if actor is not None and body.verdict != 'cant_tell':
                    for item in items:
                        named = item['resident_id'] is not None and item['confidence'] == 'confirmed' and league.human_named(item)
                        disputed = item['dispute_state'] == 'open' or item['lifecycle'] == 'disputed'
                        if named or disputed or not current['can_name']:
                            raise HTTPException(409, "Already named — use That wasn't me to dispute it.")
                target = None
                if body.verdict == 'duplicate':
                    if not body.of_visit_id or body.of_visit_id == visit_id:
                        raise HTTPException(400, 'Choose the other visit that shows this dish.')
                    target = self.visit_group(body.of_visit_id, allow_member=True)
                    if set(target['dish_ids']) & set(group['dish_ids']):
                        raise HTTPException(400, 'A visit cannot duplicate itself.')
                    targets = [store.obligation(oid) for oid in target['dish_ids']]
                    for target_item in targets:
                        bounds = league.week_bounds(now=parse_stamp(target_item['followup_at'])) if target_item['followup_at'] else None
                        if not bounds or not any(d['id'] == target_item['id'] for d in league.left_out(store, *league.week_times(bounds))):
                            raise HTTPException(400, 'Choose an original visit that is still in the league.')
                if body.verdict == 'cant_tell':
                    store.conn.execute('INSERT OR IGNORE INTO house_hidden_visits VALUES(?,?)', (actor_id or 0, visit_id))
                else:
                    if body.verdict == 'not_dish':
                        store.mark_not_dish(group['dish_ids'], reviewer)
                    elif body.verdict == 'clean':
                        store.mark_clean(group['dish_ids'], reviewer)
                    else:
                        for item in items:
                            kinds = {d['kind'] for d in item['dishes']}
                            # A person can correct a detector kind error by choosing one exact
                            # original dish. Never require matching detector labels for that.
                            matching = targets if len(targets) == 1 else [t for t in targets if {d['kind'] for d in t['dishes']} == kinds]
                            if len(matching) != 1:
                                raise HTTPException(400, 'Choose the specific original dish from that visit.')
                            store.mark_duplicate(item['id'], matching[0]['id'], reviewer)
                            house_verdicts.mark(store, [item['id']], 'duplicate', actor_id, matching[0]['id'])
                    # Keep resident attribution for the league alongside the correction audit.
                    for item in items:
                        if not store.conn.execute('SELECT 1 FROM house_verdicts WHERE obligation_id=?', (item['id'],)).fetchone():
                            house_verdicts.mark(store, [item['id']], body.verdict, actor_id)
                    store.conn.execute('INSERT OR REPLACE INTO house_visit_verdicts VALUES(?,?,?,?,?,?,?)',
                        (visit_id, body.verdict, actor_id, stamp(), body.of_visit_id, json.dumps(group['dish_ids']), current['title']))
                    store._event(None, 'house_visit_verdict', f"{actor['name'] if actor else 'The operator'} marked {current['title']}: {body.verdict}.",
                        {'visit_id': visit_id, 'obligation_ids': group['dish_ids'], 'verdict': body.verdict, 'by': actor_id, 'of_visit_id': body.of_visit_id})
            answer = current | {'points': 0 if body.verdict != 'cant_tell' else current['points'],
                'hidden': body.verdict == 'cant_tell', 'verdict': None if body.verdict == 'cant_tell' else {
                    'kind': body.verdict, 'by': {'id': actor['id'], 'name': actor['name']} if actor else None,
                    'at': stamp(), 'of_visit_id': body.of_visit_id},
                'can_name': False, 'can_dispute': False, 'can_settle': False}
            answer['aliases'] = [alias | {'verdict': answer['verdict']} for alias in current['aliases']]
            return answer

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
            with store.dish_corrections():
                try:
                    for oid in ids:
                        store.undo_dish_correction(oid, 'operator')
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from None
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
                    visit = self.change_visit(group['id'], action, body, actor)
                    canonical = house_visits.duplicate_map(store).get(oid, oid)
                    item = store.obligation(canonical)
                    item['due_at'] = item['followup_at']
                    result = self.dish(item, store.residents(), league.rules(store), actor=actor,
                                       closed=False, evidence=self.evidence_for([canonical]))
                    result.update({k: visit[k] for k in ('filmstrip_url', 'filmstrip_width', 'filmstrip_height')})
                    return result if canonical == oid else result | {'id': oid, 'duplicate_of': canonical, 'points': 0,
                        'can_name': False, 'can_dispute': False, 'can_settle': False}
                league.close_weeks(store, now=now)
            item = store.obligation(oid)
            item['due_at'] = item['followup_at']
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
                updated['due_at'] = updated['followup_at']
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
            # All local SQL writes (including worker/operator changes and week close)
            # invalidate this bounded predicate cache. data_version covers another connection.
            league.close_weeks(store)
            revision = (store.conn.total_changes, store.conn.execute('PRAGMA data_version').fetchone()[0])
            if revision != self.media_revision:
                self.media_visibility.clear()
                self.media_revision = revision
            if evidence_id not in self.media_visibility:
                groups = house_visits.catalog(store, [evidence['obligation_id']])
                group = next((g for g in groups if g['filmstrip'] and g['filmstrip']['id'] == evidence_id), None)
                # A superseded strip must not regain visibility from its legacy capture.
                group = group or house_visits.metadata(evidence)
                ids = group['dish_ids'] if group else [evidence['obligation_id']]
                visible = False
                for oid in ids:
                    item = store.obligation(oid)
                    if not item['followup_at']:
                        continue
                    week = league.week_bounds(now=parse_stamp(item['followup_at']))
                    frozen = store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?', (week['start'],)).fetchone()
                    if frozen:
                        snapshot = json.loads(frozen[0])
                        visible = any(d['id'] == oid for d in snapshot['dishes']) and any(e['id'] == evidence_id for e in snapshot['evidence'])
                    else:
                        visible = bool(league.left_out(store, *league.week_times(week), ids=[oid]))
                    if visible:
                        break
                self.media_visibility[evidence_id] = visible
                if len(self.media_visibility) > 2048:
                    self.media_visibility.popitem(last=False)
            visible = self.media_visibility[evidence_id]
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
    app.add_middleware(HouseBoundary, site_origin=house.config.site_origin, tunnel_port=app.state.house_tunnel_port)
    router = APIRouter(prefix='/house')

    @router.get('/api/me')
    def me(resident=Depends(house.resident)):
        return {'resident': {'id': resident['id'], 'name': resident['name']}, 'house': 'Highest House'}

    @router.get('/api/week')
    def week(start: str | None = None, resident=Depends(house.resident)):
        with house.week_budget.admit(f"user:{resident['id']}"):
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
        house.join_limiter.check('join:' + client_identity(request), count=5, seconds=900)
        return house.join(body)

    @router.get('/media/{evidence_id}')
    def media(evidence_id: str, exp: int, sig: str, request: Request):
        with house.media_budget.admit('ip:' + client_identity(request)):
            return house.media_file(evidence_id, exp, sig)

    app.include_router(router)
    operator = APIRouter(prefix='/api/league', dependencies=[Depends(authenticated)])

    @operator.get('/week')
    def operator_week(start: str | None = None):
        return house.week(start, None)

    @operator.get('/share')
    def share_week(start: str | None = None):
        from .week_image import render
        with house.store.lock:
            data = league.result(house.store, start)
            if data['week']['closed']:
                # History's duty banner answers who is on duty now. A shared closed result
                # must instead retain the duty assignment made by that week's close.
                snapshot = house.store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?',
                                                     (data['week']['start'],)).fetchone()
                data['on_duty'] = json.loads(snapshot[0])['on_duty']
        return Response(render(data, demo=app.state.config.demo or app.state.config.snapshot),
                        media_type='image/jpeg', headers={
                            'Content-Disposition': f'attachment; filename="highest-house-{data["week"]["start"]}.jpg"',
                            'Cache-Control': 'no-store'})

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
