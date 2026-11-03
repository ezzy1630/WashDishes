from urllib.parse import urlsplit

from test_house_api import as_person, house_client, operator  # noqa: F401

from wash_dishes import learning
from wash_dishes.store import utcnow


def test_house_help_auth_consensus_undo_and_signed_media(house_client):  # noqa: F811
    c=house_client
    store=c.app.state.store
    item=store.create_obligation(None,[{'kind':'pan','quantity':1}])
    store.add_evidence(item['id'],'teach','teach.jpg','snapshot','Dish left unattended',at=utcnow())
    (store.root/'media'/'teach.jpg').write_bytes(b'jpeg')
    with store.conn:
        qid=learning.add_question(store,source='api-test',task='present',evidence_id='teach',obligation_id=item['id'])
    data=c.get('/house/api/learning').json()
    q=data['questions'][0]
    assert q['id']==qid
    u=urlsplit(q['image_url'])
    assert c.get(u.path+'?'+u.query).status_code==200
    assert c.get(u.path+'?'+u.query.replace('actor=1','actor=2')).status_code==403
    saved=c.post(f'/house/api/learning/{qid}/answer',json={'answer':'no'}).json()
    assert saved['state']=='pending'
    as_person(c,1)
    c.get('/house/api/learning')
    assert c.post(f'/house/api/learning/{qid}/answer',json={'answer':'no'}).json()['state']=='trusted'
    assert c.post(f"/house/api/learning/labels/{saved['id']}/undo",json={}).status_code==400
    as_person(c,0)
    assert c.post(f"/house/api/learning/labels/{saved['id']}/undo",json={}).status_code==200
    assert not learning.truth(store)
    assert c.get('/api/learning').status_code==401
    operator(c)
    assert c.get('/api/learning').status_code==200
    assert c.post(f'/api/learning/{qid}/resolve',json={'answer':'no'}).json()['state']=='trusted'
