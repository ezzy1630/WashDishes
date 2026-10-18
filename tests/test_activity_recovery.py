from datetime import timedelta
from types import SimpleNamespace

from wash_dishes.inventory import pair
from wash_dishes.recovery import EvidenceRecovery
from wash_dishes.store import utcnow


def test_neighboring_dishes_and_a_box_covering_both_are_not_a_unique_match():
    items = [{'box':[.3,.5,.1,.1]}, {'box':[.38,.5,.1,.1]}]
    assert pair(items, [{'box':[.34,.5,.1,.1]}])[0] == []
    assert pair(items, [{'box':[.28,.48,.24,.15]}])[0] == []
    assert len(pair(items, [{'box':[.301,.5,.1,.1]}, {'box':[.381,.5,.1,.1]}])[0]) == 2


def test_new_activity_retries_an_unsuccessful_search_and_keeps_clock_and_identity(store, media, tmp_path):
    from test_sequences import jpeg

    at = utcnow()-timedelta(minutes=5)
    area = {'id':'counter','name':'Counter','role':'dirty','box':[0,0,1,1]}
    store.set_settings({'areas':[area]})
    item = store.create_obligation(None,[{'kind':'pan','quantity':1}],source='baseline',candidate=True,
                                   at=at,box=[.3,.5,.1,.1],location='Counter',timing_known=False)
    media.save_snapshot(item['id'],jpeg((70,80,90)),at=at,capture={'stage':'leaving','box':item['box'],'box_kind':'candidate_item'})
    labels = tmp_path/'labels'
    labels.mkdir()
    recovery = EvidenceRecovery(store,media,SimpleNamespace(labels=labels))
    assert recovery.refresh() == 0
    assert any(e['event'] == 'saved_capture_search' for e in store.obligation(item['id'])['events'])
    media.activity.save(area,'camera',{'frame':jpeg((40,50,60)),'before':jpeg((10,20,30)),
        'before_at':at-timedelta(seconds=12),'at':at-timedelta(seconds=5),'box':[.2,.2,.4,.6],'track':7,
        'dishes':[{'kind':'pan','score':.9,'box':item['box']}]})
    # Simulate the bounded retry interval without waiting in the test.
    version, _ = recovery.attempts[item['id']]
    recovery.attempts[item['id']] = (version, float('-inf'))
    assert recovery.refresh() == 1
    saved = store.obligation(item['id'])
    assert {'before','using','leaving'} <= {e['capture'].get('stage') for e in saved['evidence']}
    assert saved['followup_at'] == item['followup_at']
    assert saved['resident_id'] is None
    assert saved['evidence_timeline']['stages'][0]['state'] == 'captured'
    assert recovery.refresh() == 0


def test_recovery_does_not_attach_one_activity_to_two_neighboring_records():
    items = [{'id':1,'location':'Stove','box':[.3,.5,.1,.1]},
             {'id':2,'location':'Stove','box':[.38,.5,.1,.1]}]
    dish = {'kind':'pan','score':.9,'box':[.34,.5,.1,.1]}
    assert not any(EvidenceRecovery._unique_target(i,dish,items) for i in items)
