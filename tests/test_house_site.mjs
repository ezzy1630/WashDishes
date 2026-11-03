import assert from 'node:assert/strict';
import { test } from 'node:test';
import { week } from '../house-site/js/contracts.js';
import { pickDetail } from '../house-site/js/pages/league-copy.js';

const payload = pick => ({
  week: {start:'2026-11-02', end:'2026-11-09', closes_at:'2026-11-09T20:00:00Z', closed:false, timezone:'America/Los_Angeles'},
  rules: {enabled:true,weights:{pan:2,pot:2,default:1},min_points:4,min_lead:2},
  // Deliberately unrelated totals: a view must render the authoritative decision, not derive it.
  standings:[{resident_id:1,name:'Andrew',points:99,dishes:99}],
  pick, on_duty:null, dishes:[], visits:[], unnamed_count:0, unreviewed_count:0, verdicts:[], weeks:[],
});
const chosen = {resident_id:1,name:'Andrew',state:'pick',points:6,lead:4,needed_points:0,needed_lead:0,reason:'6 points · 4 ahead'};

test('site reads the server pick breakdown without rebuilding or stripping its reason', () => {
  const data = week(payload(chosen));
  assert.deepEqual(data.pick, chosen);
  assert.equal(pickDetail(data), '6 points · 4 ahead');
  const close = {...chosen,resident_id:null,name:null,state:'too_close',points:4,lead:1,needed_lead:1,reason:'Too close — 1 point ahead, needs 2'};
  assert.deepEqual(week(payload(close)).pick, close);
  assert.equal(pickDetail(week(payload(close))), close.reason);
});

test('site refuses missing/unknown pick states and contradictory identity fields', () => {
  for(const patch of [{state:undefined},{state:'unknown'},{name:null},{resident_id:null},
    {state:'tie'},{points:-1},{lead:1.5},{needed_points:undefined},{needed_lead:-1}]) {
    assert.throws(()=>week(payload({...chosen,...patch})));
  }
  for(const state of ['too_close','below_minimum','disabled','tie']) {
    const data=week(payload({...chosen,state,resident_id:null,name:null}));
    assert.equal(data.pick.state,state);
  }
});

 test('site consumes excluded verdict history and allows operator attribution', () => {
  const input=payload(chosen);
  input.verdicts=[{visit_id:'trip',title:'Pan',kind:'not_dish',by:null,at:'2026-11-03T00:00:00Z',of_visit_id:null}];
  assert.deepEqual(week(input).verdicts,input.verdicts);
  assert.throws(()=>week({...input,verdicts:[{...input.verdicts[0],kind:'unknown'}]}));
});
