import json
from datetime import timedelta

import pytest
from test_sequences import jpeg

from wash_dishes.proofs import Proofs
from wash_dishes.store import stamp, utcnow

LATE = "Matching dish still visible after the deadline; dirty status requires review"
BOX = [.3, .5, .1, .1]


def late_dish(store, media, *, seen_minutes_ago=5, timing_known=True):
    now = utcnow()
    item = store.create_obligation(None, [{"kind": "cup", "quantity": 1}], candidate=True, source="vision",
                                   at=now - timedelta(hours=3), box=BOX, location="Dining table", timing_known=timing_known)
    media.save_snapshot(item["id"], jpeg((90, 90, 90)), LATE, at=now - timedelta(minutes=20), capture={"stage": "later", "box": BOX})
    seen(store, item["id"], seen_minutes_ago)
    return item


def seen(store, obligation_id, minutes_ago):
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET last_seen_at=? WHERE id=?", (stamp(utcnow() - timedelta(minutes=minutes_ago)), obligation_id))


def found(box, logged, until, **kwargs):
    return {key: {"at": logged - 60 + i * 10, "diff": 1.0, "jpeg": jpeg((40 + i * 60, 40, 40))}
            for i, key in enumerate(("before", "putting", "left"))}


def test_a_late_dish_is_traced_once_and_shown_only_while_it_is_out(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    assert proofs.run_once(found) == item["id"]
    assert proofs.run_once(found) is None
    [shown] = proofs.items()
    assert shown["dish"] == "cup" and shown["resident_name"] is None
    assert proofs.image(item["id"]).startswith(b"\xff\xd8")
    seen(store, item["id"], 45)
    assert proofs.items() == []
    seen(store, item["id"], 1)
    store.action(item["id"], "resolve")
    assert proofs.items() == []


def test_dishes_first_seen_already_out_are_searched_too_and_progress_is_counted(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media, timing_known=False)
    store.create_obligation(None, [{"kind": "bowl", "quantity": 1}], candidate=True, source="vision", box=BOX)
    assert proofs.progress() == {"checking": 1, "upcoming": 1}
    assert proofs.run_once(found) == item["id"]
    assert proofs.progress()["checking"] == 0 and len(proofs.items()) == 1


def test_dishes_without_a_visible_put_down_are_never_shown_or_retried(store, media):
    proofs = Proofs(store.root, store, media)
    untraced, nobody = late_dish(store, media), late_dish(store, media)
    assert proofs.run_once(lambda *args, **kwargs: None) == untraced["id"]
    assert proofs.run_once(found, identify=lambda *_: None) == nobody["id"]
    assert proofs.run_once(found) is None
    assert proofs.items() == []


def test_naming_puts_the_name_on_a_new_picture_and_rejecting_removes_it(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    proofs.run_once(found, identify=lambda *_: [.25, .2, .2, .5])
    unnamed, before = proofs.items()[0], proofs.image(item["id"])
    sam = store.add_resident("Sam")["id"]
    store.action(item["id"], "confirm", resident_id=sam)
    named = proofs.items()[0]
    assert named["resident_name"] == "Sam" and named["image_url"] != unnamed["image_url"]
    assert proofs.image(item["id"]) != before
    proofs.reject(item["id"])
    assert proofs.items() == []


def test_proof_api_lists_serves_and_rejects(authenticated):
    client = authenticated
    runtime = client.app.state.runtime
    item = late_dish(runtime.store, runtime.media)
    runtime.proofs.run_once(found)
    data = client.get("/api/proofs").json()
    assert [p["id"] for p in data["items"]] == [item["id"]]
    image = client.get(data["items"][0]["image_url"])
    assert image.headers["content-type"] == "image/jpeg" and "max-age" in image.headers["cache-control"]
    assert client.post(f"/api/proofs/{item['id']}/reject").status_code == 200
    assert client.get("/api/proofs").json()["items"] == []
    assert client.get(f"/api/proofs/{item['id']}/image").status_code == 404


def test_check_rejection_stores_reason_and_never_calls_identity(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    calls = []
    proofs.run_once(found, identify=lambda *args: calls.append(args), check=lambda *_: "the spot is hidden in the before picture")
    row = store.conn.execute('SELECT * FROM proofs').fetchone()
    assert row['status'] == 'untraced'
    assert json.loads(row['data'])['reason'] == 'the spot is hidden in the before picture'
    assert calls == [] and proofs.items() == []
    assert not (proofs.dir / str(item['id'])).exists()


def test_selected_picture_and_person_reach_identity_and_collage_metadata(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    candidate = {'at': utcnow().timestamp() - 8000, 'jpeg': jpeg((10, 80, 120)), 'person_box': [.1, .2, .3, .4]}
    calls = []
    def identify(*args, person_box=None):
        # Lane I freezes readiness in its prediction when this callback runs.
        row = store.conn.execute('SELECT status,data FROM proofs WHERE obligation_id=?', (item['id'],)).fetchone()
        assert row['status'] == 'ready' and json.loads(row['data'])['checks'] == {'probe': .85}
        assert (proofs.dir / str(item['id']) / 'putting.jpg').read_bytes() == candidate['jpeg']
        calls.append((*args, person_box))
        return candidate['person_box']
    proofs.run_once(found, check=lambda *_: {'putting': candidate, 'checks': {'probe': .85}}, identify_selected=identify)
    assert calls[0][1] == candidate['jpeg'] and calls[0][-1] == candidate['person_box']
    data = json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])
    assert data['checks'] == {'probe': .85} and data['person_box'] == candidate['person_box']
    assert (proofs.dir / str(item['id']) / 'putting.jpg').read_bytes() == candidate['jpeg']
    assert proofs.image(item['id']).startswith(b'\xff\xd8')


def test_a_dish_whose_footage_cannot_be_read_does_not_hold_up_the_next(store, media):
    proofs = Proofs(store.root, store, media)
    stuck, nxt = late_dish(store, media), late_dish(store, media)
    def find(box, logged, until, **kwargs):
        if not tried:
            tried.append(1)
            raise OSError('segment unreadable')
        return found(box, logged, until)
    tried = []
    assert proofs.run_once(find) == stuck['id']
    assert proofs.run_once(find) == nxt['id']
    statuses = dict(store.conn.execute('SELECT obligation_id, status FROM proofs').fetchall())
    assert statuses == {stuck['id']: 'untraced', nxt['id']: 'ready'}


def test_selected_identity_absence_withdraws_proof_but_description_failure_keeps_checked_pictures(store, media):
    proofs = Proofs(store.root, store, media)
    missing = late_dish(store, media)
    proofs.run_once(found, check=lambda *_: {}, identify_selected=lambda *args, **kwargs: None)
    assert proofs.items() == [] and not (proofs.dir / str(missing['id'])).exists()
    item = late_dish(store, media)
    def broken(*args, **kwargs):
        raise OSError('identity description unavailable')
    with pytest.raises(OSError):
        proofs.run_once(found, check=lambda *_: {'checks': {'person_verified': True}}, identify_selected=broken)
    assert [p['id'] for p in proofs.items()] == [item['id']]
    assert proofs.image(item['id']).startswith(b'\xff\xd8')


def test_waiting_needs_new_late_photo_and_delay_then_reuses_trace_after_restart(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    now = utcnow()
    traces, identities, checked = [], [], []
    def find(*args, **kwargs):
        traces.append(args)
        return found(*args) | {'method': 'journal', 'putting_action': 'left it behind'}
    def check(oid, trace, late, box, late_box):
        checked.append((trace, late))
        return {'status': 'waiting', 'reason': 'someone is still at the dish', 'checks': {'busy': True}}
    proofs.run_once(find, identify=lambda *_: identities.append(1), now=now, check=check)
    assert len(traces) == 1 and identities == [] and proofs.items() == []
    assert proofs.run_once(find, now=now+timedelta(minutes=6)) is None
    fresh = jpeg((20, 30, 40))
    moved = [.5, .6, .1, .1]
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET box=? WHERE id=?', (json.dumps(moved), item['id']))
    media.save_snapshot(item['id'], fresh, LATE, at=now+timedelta(minutes=1), capture={'box': moved})
    assert proofs.run_once(find, now=now+timedelta(minutes=2)) is None
    proofs = Proofs(store.root, store, media)
    def accept(oid, trace, late, box, late_box):
        assert trace['left']['jpeg'] == checked[0][0]['left']['jpeg']
        assert late == fresh
        assert box == BOX and late_box == moved
        return {'checks': {'busy': False}}
    proofs.run_once(find, now=now+timedelta(minutes=6), check=accept)
    assert len(traces) == 1
    assert len(proofs.items(now+timedelta(minutes=6))) == 1
    assert (proofs.dir / str(item['id']) / 'late.jpg').read_bytes() == fresh
    saved = json.loads(store.conn.execute('SELECT data FROM proofs WHERE obligation_id=?', (item['id'],)).fetchone()[0])
    assert saved['method'] == 'journal' and saved['left_behind'] is True  # the caption survives the retry


def test_newest_matching_photo_is_checked_even_if_older_one_was_clear(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    newest = jpeg((15, 50, 90))
    media.save_snapshot(item['id'], newest, LATE, capture={'box': BOX})
    def check(oid, found, late, box, late_box):
        assert late == newest
        return {'status': 'waiting', 'reason': 'someone is still at the dish'}
    proofs.run_once(found, check=check)
    assert proofs.items() == []


def test_retry_capture_requires_fresh_match_and_saves_only_one_photo(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    now = utcnow()
    proofs.run_once(found, now=now, check=lambda *_: {'status': 'waiting', 'reason': 'still in use'})
    at = now+timedelta(minutes=6)
    current = {'obligation_id': item['id'], 'box': BOX, 'role': 'dirty', 'last_seen_at': stamp(at)}
    assert proofs.refresh_waiting(jpeg((20, 20, 20)), [current], at) == 0
    store.record_seen(item['id'], at)
    assert proofs.refresh_waiting(jpeg((20, 20, 20)), [current | {'last_seen_at': stamp(now)}], at) == 0
    assert proofs.refresh_waiting(jpeg((20, 20, 20)), [current | {'role': 'clean'}], at) == 0
    assert proofs.refresh_waiting(jpeg((20, 20, 20)), [current], at) == 1
    assert proofs.refresh_waiting(jpeg((20, 20, 20)), [current], at) == 0
    assert proofs.candidate(at)['late_at'] == stamp(at)


def test_items_contract_and_batched_identity_basis(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    proofs.run_once(found)
    calls = []
    def basis(ids):
        calls.append(ids)
        return {item['id']: 'same outfit as earlier'}
    store.suggestion_basis = basis
    [shown] = proofs.items()
    assert calls == [[item['id']]]
    assert set(shown) == {'id', 'dish', 'location', 'put_down_at', 'late_at', 'resident_id', 'resident_name',
                          'guess_id', 'guess_name', 'image_url', 'guess_basis'}
    assert shown['guess_basis'] == 'same outfit as earlier'


def test_reject_validates_reason_and_not_a_dish_uses_review_dismissal(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    proofs.run_once(found, check=lambda *_: {'checks': {'left_embedding': [1., 0.]}})
    with pytest.raises(ValueError):
        proofs.reject(item['id'], 'anything')
    proofs.reject(item['id'], 'not_a_dish')
    record = store.obligation(item['id'])
    assert record['lifecycle'] == 'dismissed' and record['reason'] == 'Not a dish'
    event = store.conn.execute("SELECT data FROM events WHERE event='dismiss' AND obligation_id=?", (item['id'],)).fetchone()
    assert json.loads(event[0])['note'] == 'Not a dish'
    with pytest.raises(LookupError):
        proofs.reject(item['id'])


def test_a_picked_up_dish_leaves_the_page_at_once_and_returns_if_seen_again(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    proofs.run_once(found)
    assert [p["id"] for p in proofs.items()] == [item["id"]]
    store.event("dish_picked_up", "Cup picked up from Dining table", item["id"])
    assert proofs.items() == []
    seen(store, item["id"], -1)   # an arm over the cup read as a pickup; the camera sees it there again
    assert [p["id"] for p in proofs.items()] == [item["id"]]


def test_only_a_dish_older_than_the_journal_goes_back_to_the_patch_search(authenticated, monkeypatch):
    from wash_dishes import runtime as module
    runtime = authenticated.app.state.runtime
    monkeypatch.setattr(module, 'find_putdown', lambda *args, **kwargs: 'patch search')
    for journal, expected in ((module.JOURNAL_TOO_NEW, 'patch search'), ('a recording gap', 'a recording gap')):
        monkeypatch.setattr(module, 'find_putdown_journal', lambda *args, **kwargs: journal)
        assert runtime._find_putdown(BOX, 0.0, 1.0) == expected


def test_already_there_retries_before_rejected_before_and_checks_the_new_trace(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    calls, checked = [], []
    def find(box, logged, until, *, earlier_than=None, deadline=None):
        calls.append((logged, until, earlier_than, deadline))
        return found(box, logged if earlier_than is None else earlier_than - 100, until) | {'method': 'journal'}
    def check(oid, trace, *args):
        checked.append(trace)
        return 'dish already there in the before picture' if len(checked) == 1 else {'checks': {'verified': True}}
    proofs.run_once(find, check=check)
    assert len(calls) == len(checked) == 2
    assert calls[1][:2] == calls[0][:2]  # Logging and late evidence stay anchored to this dish.
    assert calls[1][2] == checked[0]['before']['at']
    assert calls[1][3] == calls[0][3]
    data = json.loads(store.conn.execute('SELECT data FROM proofs WHERE obligation_id=?', (item['id'],)).fetchone()[0])
    assert data['putting_at'] == checked[1]['putting']['at']
    assert data['checks'] == {'verified': True} and data['method'] == 'journal'
    assert len(proofs.items()) == 1


@pytest.mark.parametrize('reason', [
    'someone is handling something over the spot in the before picture',
    'things around the dish changed too: it was uncovered, not put down',
    'no picture shows a person at the dish without their body hiding it',
    'a different dish is in that spot later',
    'dish already there in the before picture (uncertain)',
])
def test_other_rejections_never_retry(store, media, reason):
    proofs = Proofs(store.root, store, media)
    late_dish(store, media)
    calls = []
    def find(*args, **kwargs):
        calls.append(kwargs)
        return found(*args)
    proofs.run_once(find, check=lambda *_: reason)
    assert len(calls) == 1 and proofs.items() == []
    assert json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])['reason'] == reason


def test_already_there_stops_after_three_earlier_attempts(store, media):
    from wash_dishes.proofs import ALREADY_THERE
    proofs = Proofs(store.root, store, media)
    late_dish(store, media)
    calls, checks = [], []
    def find(box, logged, until, *, earlier_than=None, deadline=None):
        calls.append(earlier_than)
        return found(box, logged if earlier_than is None else earlier_than, until)
    def check(*args):
        checks.append(args)
        return ALREADY_THERE
    proofs.run_once(find, check=check, identify=lambda *_: pytest.fail('Rejected proof cannot identify someone'))
    assert len(calls) == len(checks) == 3
    assert calls[2] < calls[1]
    assert proofs.items() == []
    assert json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])['reason'] == ALREADY_THERE


@pytest.mark.parametrize('candidate_only', [False, True])
def test_retry_cannot_reuse_same_or_later_arrival_even_as_an_alternate(store, media, candidate_only):
    from wash_dishes.proofs import ALREADY_THERE
    proofs = Proofs(store.root, store, media)
    late_dish(store, media)
    checks = []
    def find(box, logged, until, *, earlier_than=None, deadline=None):
        trace = found(box, logged if earlier_than is None else earlier_than, until)
        if earlier_than is not None:
            repeated = trace['putting'] | {'at': earlier_than}  # Exclusive, including exact equality.
            if candidate_only:
                trace['candidates'] = [repeated]
            else:
                trace['putting'] = repeated
        return trace
    def check(*args):
        checks.append(args)
        return ALREADY_THERE if len(checks) == 1 else {}
    proofs.run_once(find, check=check)
    assert len(checks) == 1 and proofs.items() == []
    assert json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])['reason'] == 'the finder did not return an earlier arrival'


@pytest.mark.parametrize('expires_in', ['finder', 'checker'])
def test_shared_deadline_stops_work_and_never_saves_an_unchecked_trace(store, media, monkeypatch, expires_in):
    from wash_dishes import proofs as module
    clock = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    proofs = Proofs(store.root, store, media)
    late_dish(store, media)
    calls, checks = [], []
    def find(*args, deadline=None, **kwargs):
        calls.append(deadline)
        if expires_in == 'finder':
            clock[0] = deadline
        return found(*args)
    def check(*args):
        checks.append(args)
        clock[0] = calls[-1]
        return module.ALREADY_THERE
    proofs.run_once(find, check=check)
    assert len(calls) == 1 and len(checks) == (expires_in == 'checker')
    assert proofs.items() == []
    assert 'time budget' in json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])['reason']


def test_retry_waiting_keeps_earlier_trace_and_reuses_it_with_new_late_photo(store, media):
    from wash_dishes.proofs import ALREADY_THERE
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    now = utcnow()
    calls, checks = [], []
    def find(box, logged, until, *, earlier_than=None, **kwargs):
        calls.append(earlier_than)
        return found(box, logged if earlier_than is None else earlier_than, until) | {'method': 'journal', 'putting_action': 'left it behind'}
    def check(oid, trace, *args):
        checks.append(trace)
        return ALREADY_THERE if len(checks) == 1 else {'status': 'waiting', 'reason': 'still busy'}
    proofs.run_once(find, check=check, now=now)
    assert len(calls) == 2 and proofs.items() == []
    media.save_snapshot(item['id'], jpeg((9, 8, 7)), LATE, at=now + timedelta(minutes=1), capture={'box': BOX})
    proofs = Proofs(store.root, store, media)
    proofs.run_once(find, check=lambda *_: {}, now=now + timedelta(minutes=6))
    assert len(calls) == 2 and len(proofs.items(now + timedelta(minutes=6))) == 1
    data = json.loads(store.conn.execute('SELECT data FROM proofs').fetchone()[0])
    assert data['putting_at'] == checks[1]['putting']['at']
    assert data['method'] == 'journal' and data['left_behind'] is True


def test_runtime_passes_cutoff_and_shared_budget_through_journal_and_only_allowed_fallback(authenticated, monkeypatch):
    from wash_dishes import runtime as module
    runtime = authenticated.app.state.runtime
    calls = []
    def journal(*args, **kwargs):
        calls.append(('journal', args, kwargs))
        return module.JOURNAL_TOO_NEW
    def patch(*args, **kwargs):
        calls.append(('patch', args, kwargs))
        return 'no earlier patch arrival'
    monkeypatch.setattr(module, 'find_putdown_journal', journal)
    monkeypatch.setattr(module, 'find_putdown', patch)
    assert runtime._find_putdown(BOX, 100, 200, earlier_than=50, deadline=999) == 'no earlier patch arrival'
    assert [call[0] for call in calls] == ['journal', 'patch']
    assert all(call[2]['earlier_than'] == 50 and call[2]['deadline'] == 999 for call in calls)


def test_retry_reason_matches_the_real_checker_contract():
    from test_proof_checks import BOX, FOUND, LATE, LEFT, PERSON, PUTTING, Verifier

    from wash_dishes.proof_checks import ProofChecker
    from wash_dishes.proofs import ALREADY_THERE
    check = ProofChecker(lambda raw, box: [{'kind': 'person', 'box': PERSON}] if raw == PUTTING else [],
                         Verifier(), lambda _: {'label': 'soda can', 'margin': -.1})
    already = FOUND | {'before': {'at': 10, 'jpeg': LEFT}}
    assert check(1, already, LATE, BOX, None) == ALREADY_THERE


def test_a_late_dish_with_no_proof_is_listed_until_it_is_gone_or_proved(store, media):
    # 2026-10-23: four dishes sat out past their deadline with no proof, and the page said one was out.
    proofs = Proofs(store.root, store, media)
    untraced, traced = late_dish(store, media), late_dish(store, media)
    store.create_obligation(None, [{"kind": "bowl", "quantity": 1}], candidate=True, source="vision", box=BOX)  # not due yet
    late_dish(store, media, seen_minutes_ago=45)  # not seen lately
    assert [p["id"] for p in proofs.out_items()] == [untraced["id"], traced["id"]]
    assert proofs.run_once(lambda *args, **kwargs: None) == untraced["id"]
    assert proofs.run_once(found) == traced["id"]
    [out] = proofs.out_items()
    assert out["id"] == untraced["id"] and out["resident_name"] is None and out["timing_known"]
    assert [p["id"] for p in proofs.items()] == [traced["id"]]
    assert proofs.out_image(untraced["id"]).startswith(b"\xff\xd8")
    store.event("dish_picked_up", "Cup picked up from Dining table", untraced["id"])
    assert proofs.out_items() == []
    with pytest.raises(LookupError):
        proofs.out_image(untraced["id"])


def test_naming_or_dismissing_a_still_out_dish_changes_its_card(authenticated):
    client = authenticated
    runtime = client.app.state.runtime
    named, wrong = late_dish(runtime.store, runtime.media), late_dish(runtime.store, runtime.media)
    sam = runtime.store.add_resident("Sam")["id"]
    before = {p["id"]: p for p in client.get("/api/proofs").json()["out"]}
    image = client.get(before[named["id"]]["image_url"])
    assert image.headers["content-type"] == "image/jpeg" and "max-age" in image.headers["cache-control"]
    client.post(f"/api/obligations/{named['id']}/action", json={"action": "confirm", "resident_id": sam, "note": "Named from the still-out photo"})
    client.post(f"/api/obligations/{wrong['id']}/action", json={"action": "dismiss", "note": "Not a dish"})
    [after] = client.get("/api/proofs").json()["out"]
    assert after["id"] == named["id"] and after["resident_name"] == "Sam"
    assert after["image_url"] != before[named["id"]]["image_url"]
    assert client.get(after["image_url"]).status_code == 200
    assert client.get(before[wrong["id"]]["image_url"]).status_code == 404
