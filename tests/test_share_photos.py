from test_sequences import captured


def test_share_feed_excludes_partial_and_endpoint_rejects_it(authenticated):
    c=authenticated
    partial=captured(c.app.state.store,c.app.state.media,complete=False)
    complete=captured(c.app.state.store,c.app.state.media)
    c.app.state.runtime.sequences.refresh()
    page=c.get('/api/incidents?filter=photos')
    assert page.status_code == 200
    assert [r['id'] for r in page.json()['items']] == [complete['id']]
    assert c.get(f"/api/sequences/{partial['id']}/share").status_code == 409
    response=c.get(f"/api/sequences/{complete['id']}/share")
    assert response.status_code == 200 and response.headers['content-type']=='image/jpeg'


def test_rejected_or_changed_sequence_cannot_be_shared(authenticated):
    c=authenticated
    item=captured(c.app.state.store,c.app.state.media)
    seq=c.app.state.runtime.sequences
    seq.refresh()
    saved=seq.get(item['id'])
    seq.review(item['id'],saved['fingerprint'],{'using':False})
    assert c.get('/api/incidents?filter=photos').json()['total']==0
    assert c.get(f"/api/sequences/{item['id']}/share").status_code==409


def test_clean_and_missing_original_are_not_shareable(authenticated):
    from test_incidents import payload
    c=authenticated
    item=captured(c.app.state.store,c.app.state.media)
    rt=c.app.state.runtime
    rt.sequences.refresh()
    url=f"/api/incidents/{item['id']}"
    body=payload(c.get(url).json())
    body['condition']='clean'
    assert c.put(url+'/review',json=body).status_code==200
    assert c.get(f"/api/sequences/{item['id']}/share").status_code==409
    other=captured(c.app.state.store,c.app.state.media)
    rt.sequences.refresh()
    original=c.app.state.store.obligation(other['id'])['evidence'][0]
    c.app.state.media.safe_path(c.app.state.store.evidence(original['id'])['filename']).unlink()
    assert c.get(f"/api/sequences/{other['id']}/share").status_code==409
    assert c.get('/api/incidents?filter=photos').json()['total']==0


def test_share_requires_actual_elapsed_time_and_matching_position(authenticated):
    from datetime import timedelta

    from wash_dishes.sharing import readiness
    from wash_dishes.store import parse_stamp
    c=authenticated
    item=captured(c.app.state.store,c.app.state.media)
    seq=c.app.state.runtime.sequences
    seq.refresh()
    record=c.app.state.store.obligation(item['id'])
    saved=seq.get(item['id'])
    assert readiness(record,saved,{})[0]
    saved['stages'][-1]['box']=[.7,.1,.1,.1]
    assert not readiness(record,saved,{})[0]
    saved['stages'][-1]['box']=saved['stages'][-2]['box']
    saved['stages'][-1]['at']=(parse_stamp(saved['stages'][-2]['at'])+timedelta(minutes=119)).isoformat()
    assert not readiness(record,saved,{})[0]
