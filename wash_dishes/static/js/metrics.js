// Statistics derived from incident history. Only confirmed owners count toward a person;
// disputed and dismissed incidents and backlog items without a timer are left out.
import { state, now, startOfDay, dishCount, activeResidents, isOpen, person, quantile } from "./core.js";

export const RANGES = { "7d": "Week", "30d": "Month" };
export const rangeStart = range => now() - (range === "7d" ? 7 : 30) * 864e5;
export const counted = o => o.resident_id && o.confidence === "confirmed" && o.timing_known && !["disputed", "dismissed"].includes(o.status);
const unattributed = o => (!o.resident_id || o.confidence !== "confirmed") && !["dismissed", "cleared"].includes(o.status);
export const sat = o => Date.parse(o.resolved_at) - Date.parse(o.created_at);
const washed = o => o.status === "resolved" && o.resolved_at && o.timing_known && !o.closed_by_camera;

export function kitchenNow(snapshot, obligations = []) {
  const available = Boolean(snapshot?.fresh && snapshot.live);
  const visible = available ? (snapshot.live.detections || []).filter(d => d.counted && ["dirty", "sink"].includes(d.role)) : [];
  const ids = new Set(visible.map(d => d.obligation_id).filter(id => id != null));
  const open = obligations.filter(isOpen);
  return { available, dishes: visible.length, present: open.filter(o => ids.has(o.id)),
    unresolved: open.filter(o => !ids.has(o.id)).length };
}

// History plus anything open that the history request hasn't returned yet, newest first.
export function allIncidents() {
  const list = [...(state.history || [])];
  for (const o of state.data?.obligations || []) { const i = list.findIndex(x => x.id === o.id); if (i >= 0) list[i] = o; else list.push(o); }
  return list.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

function days(list, n, fn) {
  const today = startOfDay(now());
  return Array.from({ length: n }, (_, i) => {
    const from = today - (n - 1 - i) * 864e5, to = from + 864e5;
    return fn(list.filter(o => { const t = Date.parse(o.created_at); return t >= from && t < to; }), from);
  });
}

export function accountability(range = "7d") {
  const all = allIncidents(), start = rangeStart(range);
  const stats = new Map((state.data?.statistics || []).map(s => [s.resident_id, s]));
  const inRange = o => Date.parse(o.created_at) >= start;
  const rows = activeResidents().map(r => {
    const mine = all.filter(o => o.resident_id === r.id && counted(o));
    const list = mine.filter(inRange);
    const decided = list.filter(o => o.status === "resolved" || o.overdue_verified);
    const late = decided.filter(o => o.overdue_verified).length;
    const sits = list.filter(washed).map(sat);
    const s = stats.get(r.id) || {};
    return {
      r, incidents: list.length, dishes: list.reduce((n, o) => n + dishCount(o), 0),
      out: s.dishes_unwashed ?? 0, lateNow: s.dishes_late_now ?? 0,
      latePct: decided.length ? late / decided.length : null, lateCount: late, decided: decided.length,
      median: quantile(sits, .5), p90: quantile(sits, .9), washedCount: sits.length,
      helped: all.filter(o => inRange(o) && o.status === "resolved" && o.cleaner_id === r.id && o.resident_id !== r.id).reduce((n, o) => n + dishCount(o), 0),
      samples: s.appearance_samples ?? 0,
      days: days(mine, 14, l => ({ n: l.reduce((n, o) => n + dishCount(o), 0), late: l.some(o => o.overdue_verified) })),
    };
  });
  rows.sort((a, b) => b.lateNow - a.lateNow || (b.latePct ?? -1) - (a.latePct ?? -1) || b.dishes - a.dishes || a.r.name.localeCompare(b.r.name));
  const unknownList = all.filter(o => unattributed(o) && inRange(o));
  const unknown = { incidents: unknownList.length, dishes: unknownList.reduce((n, o) => n + dishCount(o), 0), out: (state.data?.obligations || []).filter(o => isOpen(o) && unattributed(o)).reduce((n, o) => n + dishCount(o), 0) };
  const total = all.filter(o => inRange(o) && !["dismissed", "cleared"].includes(o.status)).length;
  return { rows, unknown, total };
}

// Who's leaving dishes: each person's confirmed dishes, and apart from them the camera's guesses,
// which never count until someone confirms them. Taken away in time, dismissed and disputed
// dishes are no one's fault and are left out.
export const leftOut = o => !["dismissed", "cleared", "disputed"].includes(o.status);
export const wentLate = o => Boolean(o.overdue_verified);
export function ledger(range = "7d") {
  const start = rangeStart(range);
  const all = allIncidents().filter(o => leftOut(o) && Date.parse(o.created_at) >= start);
  const rows = activeResidents().map(r => {
    const mine = all.filter(o => o.resident_id === r.id && o.confidence === "confirmed");
    const guesses = all.filter(o => o.confidence !== "confirmed" && o.suggested_resident_id === r.id);
    return { r, mine, guesses, late: mine.filter(wentLate).length, out: mine.filter(isOpen).length,
      dishes: mine.reduce((n, o) => n + dishCount(o), 0), guessLate: guesses.filter(wentLate).length };
  });
  rows.sort((a, b) => b.late - a.late || b.mine.length - a.mine.length || b.guesses.length - a.guesses.length || a.r.name.localeCompare(b.r.name));
  const nobody = all.filter(o => o.timing_known && o.confidence !== "confirmed" && !person(o.suggested_resident_id));
  return { rows, nobody };
}
