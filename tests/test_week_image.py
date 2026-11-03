from datetime import timedelta
from io import BytesIO

from PIL import Image

from wash_dishes import league
from wash_dishes.store import stamp, utcnow
from wash_dishes.week_image import render


def test_week_image_requires_operator_and_does_not_enter_house_api(client):
    assert client.get('/api/league/share').status_code == 401
    assert client.get('/house/api/share').status_code == 404
    assert client.get('/api/operator-status').status_code == 401


def test_shared_week_uses_current_then_frozen_result(authenticated):
    client = authenticated
    store = client.app.state.store
    person = store.add_resident('Sam')
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 3}], candidate=True, source='camera', at=utcnow() - timedelta(hours=3))
    week = league.week_bounds()
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET due_at=?,overdue_verified=1 WHERE id=?', (stamp(utcnow() - timedelta(minutes=1)), item['id']))
    url = '/api/league/share?start=' + week['start']
    before = client.get(url)
    assert before.status_code == 200
    assert before.headers['content-type'] == 'image/jpeg'
    assert before.headers['cache-control'] == 'no-store'
    assert week['start'] in before.headers['content-disposition']
    with Image.open(BytesIO(before.content)) as image:
        assert image.width == 1080 and image.height > 600
    assert len(before.content) < 600_000
    store.action(item['id'], 'confirm', resident_id=person['id'])
    named = client.get(url).content
    assert named != before.content
    league.close_weeks(store, now=league.week_times(week)[1] + timedelta(hours=13))
    frozen = client.get(url).content
    with store.lock, store.conn:
        store.conn.execute('UPDATE residents SET name=? WHERE id=?', ('Changed later', person['id']))
    assert client.get(url).content == frozen
    assert client.get('/api/league/share?start=not-a-date').status_code == 400


def test_week_image_handles_long_names_extra_scorers_off_and_demo():
    board = [{'resident_id': i, 'name': 'Very long housemate name ' * 2, 'points': 20 - i} for i in range(10)]
    data = {'week': league.week_bounds(), 'rules': league.DEFAULT_RULES, 'standings': board,
            'pick': league.pick(board, league.DEFAULT_RULES), 'on_duty': None}
    normal = render(data)
    off_rules = league.DEFAULT_RULES | {'enabled': False}
    off = render(data | {'rules': off_rules, 'pick': league.pick(board, off_rules)}, demo=True)
    assert normal != off
    for picture in (normal, off):
        with Image.open(BytesIO(picture)) as image:
            assert image.width == 1080 and image.height > 1000
        assert len(picture) < 600_000


def test_home_ready_only_skips_expensive_incomplete_sequences(authenticated, monkeypatch):
    runtime = authenticated.app.state.runtime
    monkeypatch.setattr(runtime.proofs, 'out_items', lambda: (_ for _ in ()).throw(AssertionError('Home fetched incomplete sequences')))
    response = authenticated.get('/api/proofs?ready_only=true')
    assert response.status_code == 200
    assert set(response.json()) == {'items', 'people'}


def test_operator_status_is_small_and_keeps_unknowns_explicit(authenticated):
    response = authenticated.get('/api/operator-status')
    data = response.json()
    assert response.status_code == 200
    assert data['analyzed_at'] is None
    assert data['footage_hours'] == 0
    assert not data['camera']['connected']
    assert len(response.content) < 2000
    assert 'obligations' not in data


def test_closed_no_pick_share_keeps_its_duty_assignment(authenticated):
    client = authenticated
    store = client.app.state.store
    sam, bo = store.add_resident('Sam'), store.add_resident('Bo')
    week = league.week_bounds()
    league.override(store, sam['id'])
    league.close_weeks(store, now=league.week_times(week)[1] + timedelta(hours=13))
    url = '/api/league/share?start=' + week['start']
    picture = client.get(url).content
    league.override(store, bo['id'])
    assert client.get(url).content == picture


def test_shared_image_names_the_pick_without_repeating_it_in_the_explanation(monkeypatch):
    from PIL import ImageDraw
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', record)
    board = [{'resident_id': 1, 'name': 'Theo', 'points': 6}, {'resident_id': 2, 'name': 'Wren', 'points': 2}]
    render({'week': league.week_bounds(), 'rules': league.DEFAULT_RULES, 'standings': board,
            'pick': league.pick(board, league.DEFAULT_RULES), 'on_duty': None})
    assert drawn.count('Theo') == 2  # One standings row, one duty name.
    assert '6 points · 4 ahead' in drawn
    assert not any('Theo' in text and text != 'Theo' for text in drawn)
