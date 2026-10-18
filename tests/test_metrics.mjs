import assert from "node:assert/strict";
import test from "node:test";
import { state } from "../wash_dishes/static/js/core.js";
import { kitchenNow, ledger, wentLate } from "../wash_dishes/static/js/metrics.js";

test("an elapsed timer without a sighting is not recorded as a late dish", () => {
  const item = {
    id: 1, resident_id: 1, confidence: "confirmed", lifecycle: "open", status: "overdue",
    timing_known: true, created_at: new Date(Date.now() - 10800000).toISOString(),
    due_at: new Date(Date.now() - 3600000).toISOString(), overdue_verified: false,
    dishes: [{ kind: "pan", quantity: 1 }],
  };
  assert.equal(wentLate(item), false);
  state.data = { residents: [{ id: 1, name: "Alex", active: true }], obligations: [item] };
  state.history = [];
  assert.equal(ledger().rows[0].late, 0);
  item.overdue_verified = true;
  assert.equal(ledger().rows[0].late, 1);
});

test("current counts include unassigned visible dishes and exclude old unresolved incidents", () => {
  const obligations = [{ id: 1, status: "overdue" }, { id: 2, status: "review" }];
  const snapshot = { fresh: true, live: { detections: [
    { kind: "pan", role: "dirty", counted: true, obligation_id: 2 },
    { kind: "bowl", role: "sink", counted: true, obligation_id: null },
    { kind: "plate", role: "clean", counted: true, obligation_id: null },
  ] } };
  const current = kitchenNow(snapshot, obligations);
  assert.equal(current.dishes, 2);
  assert.deepEqual(current.present.map(o => o.id), [2]);
  assert.equal(current.unresolved, 1);
  assert.equal(kitchenNow({ ...snapshot, fresh: false }, obligations).available, false);
});
