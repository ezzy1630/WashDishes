import assert from "node:assert/strict";
import { test } from "node:test";
import { week } from "../house-site/js/contracts.js";
import { pickDetail } from "../house-site/js/pages/league-copy.js";

const payload = (pick) => ({
  start_week: "2026-11-02",
  week: {
    start: "2026-11-02",
    end: "2026-11-09",
    closes_at: "2026-11-09T20:00:00Z",
    closed: false,
    timezone: "America/Los_Angeles",
  },
  rules: {
    enabled: true,
    weights: { pan: 2, pot: 2, default: 1 },
    min_points: 4,
    min_lead: 2,
  },
  // Deliberately unrelated totals: a view must render the authoritative decision, not derive it.
  standings: [{ resident_id: 1, name: "Theo", points: 99, dishes: 99 }],
  pick,
  on_duty: null,
  dishes: [],
  visits: [],
  unnamed_count: 0,
  unreviewed_count: 0,
  clear_count: 0,
  weak_count: 0,
  verdicts: [],
  weeks: [],
});
const chosen = {
  resident_id: 1,
  name: "Theo",
  state: "pick",
  points: 6,
  lead: 4,
  needed_points: 0,
  needed_lead: 0,
  reason: "6 points · 4 ahead",
};

test("site reads the server pick breakdown without rebuilding or stripping its reason", () => {
  const data = week(payload(chosen));
  assert.deepEqual(data.pick, chosen);
  assert.equal(pickDetail(data), "6 points · 4 ahead");
  const close = {
    ...chosen,
    resident_id: null,
    name: null,
    state: "too_close",
    points: 4,
    lead: 1,
    needed_lead: 1,
    reason: "Too close — 1 point ahead, needs 2",
  };
  assert.deepEqual(week(payload(close)).pick, close);
  assert.equal(pickDetail(week(payload(close))), close.reason);
});

test("site refuses missing/unknown pick states and contradictory identity fields", () => {
  for (const patch of [
    { state: undefined },
    { state: "unknown" },
    { name: null },
    { resident_id: null },
    { state: "tie" },
    { points: -1 },
    { lead: 1.5 },
    { needed_points: undefined },
    { needed_lead: -1 },
  ]) {
    assert.throws(() => week(payload({ ...chosen, ...patch })));
  }
  for (const state of ["too_close", "below_minimum", "disabled", "tie"]) {
    const data = week(
      payload({ ...chosen, state, resident_id: null, name: null }),
    );
    assert.equal(data.pick.state, state);
  }
});

test("site consumes excluded verdict history and allows operator attribution", () => {
  const input = payload(chosen);
  input.verdicts = [
    {
      visit_id: "trip",
      title: "Pan",
      kind: "not_dish",
      by: null,
      at: "2026-11-03T00:00:00Z",
      of_visit_id: null,
    },
  ];
  assert.deepEqual(week(input).verdicts, input.verdicts);
  assert.throws(() =>
    week({ ...input, verdicts: [{ ...input.verdicts[0], kind: "unknown" }] }),
  );
});

import { personalCopy } from "../house-site/js/pages/league-copy.js";
import { pendingAnswers } from "../house-site/js/answers.js";

test("personal status follows the authoritative pick and current duty, never rank order", () => {
  const data = week(payload(chosen));
  assert.match(personalCopy(data, 1).title, /in line/);
  assert.equal(personalCopy(data, 1).points, 99);
  assert.match(
    personalCopy(
      {
        ...data,
        pick: { ...chosen, state: "tie", resident_id: null, name: null },
      },
      1,
    ).title,
    /not the pick/,
  );
  assert.match(
    personalCopy(
      {
        ...data,
        pick: { ...chosen, state: "tie", resident_id: null, name: null },
        on_duty: { resident_id: 1, name: "Theo" },
      },
      1,
    ).title,
    /on kitchen/,
  );
  assert.match(personalCopy(data, 2).title, /No points/);
});

test("Undo cancels an unsent name without changing the server; flush saves once", async () => {
  const sent = [],
    failures = [];
  const answers = pendingAnswers({
    send: async (entry) => {
      sent.push(entry.visit.id);
    },
    changed: () => {},
    saved: async () => {},
    failed: (_, error) => failures.push(error),
    delay: 60000,
  });
  answers.add({ id: "first" }, "name", { resident_id: 2 }, "Named as Wren.");
  answers.undo("first");
  await answers.flush();
  assert.deepEqual(sent, []);
  answers.add({ id: "second" }, "name", { resident_id: 2 }, "Named as Wren.");
  answers.add({ id: "second" }, "name", { resident_id: 3 }, "Duplicate tap.");
  await answers.flush();
  await answers.flush();
  assert.deepEqual(sent, ["second"]);
  assert.equal(answers.entries.size, 0);
  assert.deepEqual(failures, []);
});

test("pending answers serialize writes and a rejected answer returns for review", async () => {
  const sent = [],
    failures = [];
  const answers = pendingAnswers({
    send: async (entry) => {
      sent.push(entry.visit.id);
      if (entry.visit.id === "first") throw new Error("Already named");
    },
    changed: () => {},
    saved: async () => {},
    failed: (entry, error) => failures.push([entry.visit.id, error.message]),
    delay: 60000,
  });
  answers.add({ id: "first" }, "name", {}, "First");
  answers.add({ id: "second" }, "verdict", { verdict: "cant_tell" }, "Skipped");
  await answers.flush();
  assert.deepEqual(sent, ["first", "second"]);
  assert.deepEqual(failures, [["first", "Already named"]]);
  assert.equal(answers.entries.size, 0);
});

test("clearing a session cancels queued answers before another write starts", async () => {
  const sent = [];
  const answers = pendingAnswers({
    send: async (entry) => {
      sent.push(entry.visit.id);
    },
    changed: () => {},
    saved: async () => {},
    failed: () => {},
    delay: 60000,
  });
  answers.add({ id: "old-session" }, "name", {}, "Named");
  const flushing = answers.flush();
  answers.cancel();
  await flushing;
  assert.deepEqual(sent, []);
});

test('learning cards reject unsafe images, unknown tasks and invalid geometry', async () => {
  const {learningFeed}=await import('../house-site/js/learning-contracts.js');
  const feed={personal_labels:0,house_truth:0,runs:[],last_learned:null,questions:[{id:1,task:'present',reason:'Uncertain',captured_at:'2026-11-03T00:00:00Z',image_url:'https://example.com/house/learning-media/1',other_image_url:null,box:[0,0,.3,.3]}]};
  assert.equal(learningFeed(feed),feed);
  for(const patch of [{task:'unknown'},{image_url:'javascript:alert(1)'},{image_url:'https://user:password@example.com/photo'},{box:[0,0,NaN,.3]}])assert.throws(()=>learningFeed({...feed,questions:[{...feed.questions[0],...patch}]}));
});
