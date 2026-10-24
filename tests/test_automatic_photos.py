from datetime import timedelta

from test_inventory import Kitchen
from test_sequences import jpeg

from wash_dishes.incidents import Incidents
from wash_dishes.inventory import Inventory
from wash_dishes.sequences import SequenceCollection
from wash_dishes.store import parse_stamp, utcnow


def test_existing_dishes_get_visible_records_and_one_two_hour_followup(store, media):
    store.set_settings({'deadline_minutes': 120})
    kitchen = Kitchen(store, media)
    kitchen.put('pan', 'pan', 'counter')()
    kitchen.put('cup', 'cup', 'sink')()
    kitchen.put('clean', 'plate', 'rack')()
    kitchen.wait(40)
    records = kitchen.items()
    assert len(records) == 2
    assert all(not r['timing_known'] and r['due_at'] is None for r in records)
    for item in kitchen.inventory.items.values():
        if item.get('obligation_id'):
            record = store.obligation(item['obligation_id'])
            due = parse_stamp(record['followup_at'])
            kitchen.inventory._record_seen(item, jpeg('red'), due-timedelta(seconds=1))
            assert not any(e['capture'].get('stage') == 'later' for e in store.obligation(record['id'])['evidence'])
            kitchen.inventory._record_seen(item, jpeg('blue'), due)
            kitchen.inventory._record_seen(item, jpeg('green'), due+timedelta(seconds=10))
            assert len([e for e in store.obligation(record['id'])['evidence'] if e['capture'].get('stage') == 'later']) == 1
    page = Incidents(store, media, SequenceCollection(store, media)).page(filter='out')
    assert page['total'] == 2
    assert not store.confirmed_late_ids()


def test_continuity_review_does_not_block_collecting_actual_later_photo(store, media):
    item = store.create_obligation(None, [{'kind':'pan','quantity':1}], candidate=True, at=utcnow()-timedelta(hours=3))
    store.note_missing(item['id'], 'Stove')
    inventory = Inventory(store, media)
    inventory._record_seen({'obligation_id':item['id'],'role':'dirty','box':[.3,.7,.05,.05]}, jpeg('blue'), utcnow())
    updated = store.obligation(item['id'])
    assert any(e['capture'].get('stage') == 'later' for e in updated['evidence'])
    assert updated['review_pending'] and not updated['overdue_verified']


def test_due_photo_runs_while_zone_is_busy_and_never_uses_cached_presence(store, media):
    from wash_dishes.inventory import Zone
    store.set_settings({'deadline_minutes':120})
    start = utcnow().replace(microsecond=0)-timedelta(hours=3)
    record = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,at=start)
    item = {'key':'pan','kind':'pan','box':[.3,.7,.1,.1], 'role':'dirty','area_id':'stove','obligation_id':record['id']}
    inventory=Inventory(store,media)
    inventory.items={'pan':item}
    inventory.zones={'stove':Zone({'id':'stove','name':'Stove','role':'dirty','box':[.2,.6,.3,.3]},active_at=start)}
    due=parse_stamp(record['followup_at'])
    inventory._capture_due(jpeg('blue'),due,lambda area:[])
    assert not store.obligation(record['id'])['evidence']
    inventory._capture_due(jpeg('red'),due+timedelta(seconds=5),lambda area:[{'kind':'pan','score':.8,'box':item['box']}])
    assert [e['capture']['stage'] for e in store.obligation(record['id'])['evidence']] == ['later']


def test_saved_capture_recovery_rejects_nearby_people_and_preserves_sources():
    from wash_dishes.recovery import earlier_candidates
    item={'created_at':'2026-10-17T21:44:27+00:00','timing_known':True,'dishes':[{'kind':'pan'}],'box':[.32,.725,.064,.082]}
    frame={'id':'1','created_at':'2026-10-17T21:35:45+00:00','predicted':[
        {'kind':'pan','score':.8,'box':item['box']},
        {'kind':'person','score':.9,'box':[.18,.63,.18,.37]}]}
    assert earlier_candidates(item,[frame]) == (None,None)
    frame['predicted'][1]['box']=[.30,.4,.2,.6]
    before={'id':'0','created_at':'2026-10-17T21:20:00+00:00','predicted':[]}
    first,using=earlier_candidates(item,[before,frame])
    assert first['id']=='0' and using['id']=='1'
    frame['predicted'].append({'kind':'person','score':.9,'box':[.30,.4,.2,.6]})
    assert earlier_candidates(item,[before,frame]) == (None,None)


def test_nearby_person_does_not_block_the_two_hour_photo(store, media):
    from wash_dishes.inventory import Zone
    store.set_settings({'deadline_minutes': 120})
    start = utcnow().replace(microsecond=0)-timedelta(hours=3)
    box = [.3,.7,.1,.1]
    record = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,at=start)
    media.save_snapshot(record['id'],jpeg('red'),'Dish left unattended',at=start,
                        capture={'stage':'leaving','box':box,'box_kind':'candidate_item'})
    inventory = Inventory(store,media)
    item = {'key':'pan','kind':'pan','box':box,'role':'dirty','area_id':'stove','obligation_id':record['id']}
    inventory.items = {'pan':item}
    departure = start+timedelta(minutes=20)
    inventory.zones = {'stove':Zone({'id':'stove','name':'Stove','role':'dirty','box':[.2,.6,.3,.3]},
                                  quiet_at=departure,quiet_jpeg=jpeg('green'),settled_since=departure-timedelta(seconds=30))}
    inventory.evidence = {'pan':{'continued_handling':{'at':departure-timedelta(minutes=1),'frame':jpeg('blue'),
                         'box':[.25,.4,.3,.5],'dishes':[{'kind':'pan','box':box}]}}}
    # Legacy nearby-person metadata cannot be mistaken for a new use of the pan.
    inventory._record_seen(item,jpeg('yellow'),start+timedelta(hours=2))
    updated = store.obligation(record['id'])
    assert parse_stamp(updated['followup_at']) == start+timedelta(hours=2)
    later = [e for e in updated['evidence'] if e['capture'].get('stage') == 'later']
    assert len(later) == 1
    assert parse_stamp(later[0]['created_at']) == start+timedelta(hours=2)


def test_neighbouring_saucepan_is_not_front_pan_handling_evidence():
    visitor={'box':[.2,.4,.2,.5],'dishes':[{'kind':'pan','box':[.2766,.7134,.0700,.0611]}]}
    assert Inventory._handling_dishes(visitor,[.3222,.7319,.0533,.0524],'pan') == []
    assert len(Inventory._handling_dishes(visitor,[.2904,.7130,.0553,.0602],'pan')) == 1


def test_due_photo_does_not_borrow_a_neighbour_that_is_not_due(store, media):
    from wash_dishes.inventory import Zone
    start = utcnow().replace(microsecond=0)-timedelta(hours=3)
    record = store.create_obligation(None,[{'kind':'pan','quantity':1}],candidate=True,at=start)
    target={'key':'front','kind':'pan','box':[.3222,.7319,.0533,.0524],'role':'dirty','area_id':'stove','obligation_id':record['id']}
    neighbour={'key':'rear','kind':'pan','box':[.2904,.7130,.0553,.0602],'role':'dirty','area_id':'stove','obligation_id':None}
    inventory=Inventory(store,media)
    inventory.items={'front':target,'rear':neighbour}
    inventory.zones={'stove':Zone({'id':'stove','name':'Stove','role':'dirty','box':[.2,.6,.3,.3]})}
    inventory._capture_due(jpeg('red'),utcnow(),lambda area:[{'kind':'pan','score':.8,'box':neighbour['box']}])
    assert not store.obligation(record['id'])['evidence']


def test_a_dish_still_out_gets_a_fresh_photo_each_hour_it_is_seen(store, media):
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], candidate=True, at=utcnow()-timedelta(hours=5))
    inventory = Inventory(store, media)
    tracked = {'obligation_id': item['id'], 'role': 'dirty', 'box': [.3, .7, .05, .05]}
    due = parse_stamp(store.obligation(item['id'])['followup_at'])
    for minutes in (1, 30, 61, 90, 122):
        inventory._record_seen(tracked, jpeg('blue'), due + timedelta(minutes=minutes))
    late = [e for e in store.obligation(item['id'])['evidence'] if e['capture'].get('stage') == 'later']
    assert [parse_stamp(e['created_at']) - due for e in late] == [timedelta(minutes=m) for m in (1, 61, 122)]
    store.action(item['id'], 'dismiss', note='Not a dish')
    inventory._record_seen(tracked, jpeg('blue'), due + timedelta(minutes=190))
    assert len([e for e in store.obligation(item['id'])['evidence'] if e['capture'].get('stage') == 'later']) == 3
