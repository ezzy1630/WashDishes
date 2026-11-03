"""Authenticated teaching endpoints; signed retained photos never expose filesystem paths."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import learning, learning_jobs
from .store import parse_stamp, utcnow


class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(max_length=30)
    value: dict | int | None = None


class Correct(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: str


def install(app, house, authenticated):
    store = house.store
    router = APIRouter()

    def safe(call):
        try:
            with store.lock, store.conn:
                return call()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def media_url(q, actor):
        exp = int(utcnow().timestamp()) + 1800
        message = f"learning:{q['id']}:{actor}:{exp}"
        sig = hmac.new(house.media_key, message.encode(), hashlib.sha256).hexdigest()
        return f"{house.config.public_origin}/house/learning-media/{q['id']}?actor={actor}&exp={exp}&sig={sig}"

    @router.get('/house/api/learning')
    def questions(resident=Depends(house.resident)):
        with house.week_budget.admit(f"user:{resident['id']}"):
            cards = safe(lambda: learning.queue(store, resident['id']))
            return safe(lambda: learning.progress(store, resident['id'])) | {'questions': [
                {k: q[k] for k in ('id', 'task', 'box', 'reason', 'captured_at')} | {'image_url': media_url(q, resident['id']),
                    'other_image_url': media_url(q, resident['id']) + '&other=1' if q['other_evidence_id'] else None} for q in cards]}

    @router.post('/house/api/learning/{qid}/answer')
    def answer(qid: int, body: Answer, resident=Depends(house.resident)):
        return safe(lambda: learning.answer(store, qid, resident['id'], body.answer, body.value))

    @router.post('/house/api/learning/labels/{label_id}/undo')
    def undo(label_id: int, resident=Depends(house.resident)):
        return safe(lambda: learning.undo(store, label_id, resident['id']))

    @router.post('/house/api/visits/{visit_id}/feedback')
    def correction(visit_id: str, body: Correct, resident=Depends(house.resident)):
        if body.task not in ('present', 'kind', 'missed'):
            raise HTTPException(400, 'Choose presence, kind or a missed dish.')
        # Resolve through the existing house projection; clients cannot submit arbitrary private photos.
        visit = next((v for v in house.week(None, resident)['visits'] if v['id'] == visit_id), None)
        if not visit:
            raise HTTPException(404, 'That visit is no longer available.')
        def create():
            oid = visit['dish_ids'][0]
            evidence = store.conn.execute("SELECT * FROM evidence WHERE obligation_id=? AND kind='snapshot' AND expired=0 AND coalesce(json_extract(capture,'$.identity_source'),'')='' AND label NOT LIKE 'Person%' AND label!='Who put it down (from footage)' ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
            if not evidence:
                raise ValueError('The original frame is no longer kept.')
            item = store.obligation(oid)
            qid = learning.add_question(store, source=f"evidence:{evidence['id']}:{body.task}", task=body.task,
                evidence_id=evidence['id'], obligation_id=oid, box=(json.loads(item['box']) if isinstance(item.get('box'), str) else item.get('box')) if body.task != 'missed' else None,
                priority=10, reason='A housemate flagged this box')
            store.conn.execute('UPDATE learning_questions SET priority=max(priority,10),reason=? WHERE id=?', ('A housemate flagged this box', qid))
            # Interested people may flag an error, but cannot independently confirm it.
            return {'question_id': qid, 'independent_review': resident['id'] in learning.interested(store, learning.question(store, qid))}
        return safe(create)

    @router.get('/house/learning-media/{qid}')
    def image(qid: int, actor: int, exp: int, sig: str, other: bool = False):
        now = utcnow()
        message = f'learning:{qid}:{actor}:{exp}'
        expected = hmac.new(house.media_key, message.encode(), hashlib.sha256).hexdigest()
        if not int(now.timestamp()) < exp <= int(now.timestamp()) + 1800 or not hmac.compare_digest(expected, sig):
            raise HTTPException(403, 'Refresh Help the camera to see this picture.')
        def resolve():
            q = learning.question(store, qid)
            store._resident(actor)
            if actor in learning.interested(store, q) or not store.conn.execute('SELECT 1 FROM learning_asks WHERE question_id=? AND actor_id=?', (qid, actor)).fetchone():
                raise ValueError('This question requires someone independent.')
            e = store.evidence(q['other_evidence_id'] if other else q['evidence_id'])
            if not e or e['expired'] or parse_stamp(e['expires_at']) <= now:
                raise ValueError('This picture is no longer kept.')
            path = house.media.safe_path(e['filename'])
            if not path.is_file():
                raise ValueError('This picture is no longer kept.')
            return path
        return FileResponse(safe(resolve), media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})

    @router.get('/api/learning', dependencies=[Depends(authenticated)])
    def operator():
        def overview():
            conflicts = [dict(q) for q in store.conn.execute('SELECT * FROM learning_questions') if learning.verdict(store, dict(q))['state'] == 'conflict']
            for q in conflicts:
                q['labels'] = [dict(r) for r in store.conn.execute('SELECT * FROM learning_labels WHERE question_id=? AND undone_at IS NULL', (q['id'],))]
            return learning.progress(store, None) | {'conflicts': conflicts, 'models': list(learning_jobs.ARTIFACTS)}
        return safe(overview)

    @router.post('/api/learning/{qid}/resolve', dependencies=[Depends(authenticated)])
    def resolve(qid: int, body: Answer):
        def save():
            # Replacement resolution remains auditable and undoable.
            old = store.conn.execute('SELECT id FROM learning_labels WHERE question_id=? AND actor_id IS NULL AND undone_at IS NULL', (qid,)).fetchall()
            for row in old:
                learning.undo(store, row['id'], None)
            return learning.answer(store, qid, None, body.answer, body.value, source='operator')
        return safe(save)

    @router.post('/api/learning/labels/{label_id}/undo', dependencies=[Depends(authenticated)])
    def operator_undo(label_id: int):
        return safe(lambda: learning.undo(store, label_id, None))

    @router.post('/api/learning/rollback/{model}', dependencies=[Depends(authenticated)])
    def rollback(model: str):
        try:
            return learning_jobs.rollback(store, model)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    app.include_router(router)
