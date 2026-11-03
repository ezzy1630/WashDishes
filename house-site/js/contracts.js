// Parse the untrusted API boundary once; views receive complete, typed records.
/** @param {unknown} value @returns {Record<string, unknown>} */
function object(value) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid house response");
  return /** @type {Record<string, unknown>} */ (value);
}
/** @param {unknown} value */
function string(value) {
  if (typeof value !== "string") throw new Error("Invalid house text");
  return value;
}
/** @param {unknown} value */
function number(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    throw new Error("Invalid house number");
  return value;
}
/** @param {unknown} value */
function boolean(value) {
  if (typeof value !== "boolean") throw new Error("Invalid house flag");
  return value;
}
/** @param {unknown} value */
function id(value) {
  if (
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value))
  )
    return value;
  throw new Error("Invalid house identity");
}
/** @template T @param {unknown} value @param {(item: unknown) => T} parse */
function array(value, parse) {
  if (!Array.isArray(value)) throw new Error("Invalid house list");
  return value.map(parse);
}
/** @template T @param {unknown} value @param {(item: unknown) => T} parse */
function nullable(value, parse) {
  return value == null ? null : parse(value);
}
/** @param {unknown} value */
export function resident(value) {
  const r = object(value);
  return { id: id(r.id), name: string(r.name) };
}
/** @param {unknown} value */
export function me(value) {
  const m = object(value);
  return { resident: resident(m.resident), house: string(m.house) };
}
/** @param {unknown} value */
export function join(value) {
  const j = object(value),
    login = string(j.login);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(login))
    throw new Error("Invalid login name");
  return { login };
}
/** @param {unknown} value */
function dispute(value) {
  const d = object(value),
    state = string(d.state);
  if (state !== "open" && state !== "upheld" && state !== "overturned")
    throw new Error("Invalid dispute state");
  return {
    state,
    by: resident(d.by),
    note: string(d.note),
    at: string(d.at),
    settled_by: nullable(d.settled_by, resident),
  };
}
/** @param {unknown} value */
export function dish(value) {
  const d = object(value);
  return {
    id: id(d.id),
    kind: string(d.kind),
    title: string(d.title),
    location: string(d.location),
    out_since: string(d.out_since),
    due_at: string(d.due_at),
    gone_at: nullable(d.gone_at, string),
    points: number(d.points),
    resident: nullable(d.resident, resident),
    named_by: nullable(d.named_by, resident),
    named_at: nullable(d.named_at, string),
    dispute: nullable(d.dispute, dispute),
    filmstrip_url: nullable(d.filmstrip_url, string),
    photo_url: nullable(d.photo_url, string),
    ...mediaSize(d),
    can_name: boolean(d.can_name),
    can_dispute: boolean(d.can_dispute),
    can_settle: boolean(d.can_settle),
  };
}
/** @param {unknown} value */
export function week(value) {
  const x = object(value),
    w = object(x.week),
    r = object(x.rules),
    weights = object(r.weights);
  const timezone = string(w.timezone);
  new Intl.DateTimeFormat("en", { timeZone: timezone });
  return {
    week: {
      start: string(w.start),
      end: string(w.end),
      closes_at: string(w.closes_at),
      closed: boolean(w.closed),
      timezone,
    },
    rules: {
      enabled: boolean(r.enabled),
      weights: {
        pan: number(weights.pan),
        pot: number(weights.pot),
        default: number(weights.default),
      },
      min_points: number(r.min_points),
      min_lead: number(r.min_lead),
    },
    standings: array(x.standings, (item) => {
      const s = object(item);
      return {
        resident_id: id(s.resident_id),
        name: string(s.name),
        points: number(s.points),
        dishes: number(s.dishes),
      };
    }),
    pick: pick(x.pick),
    on_duty: nullable(x.on_duty, (item) => {
      const d = object(item);
      return {
        resident_id: id(d.resident_id),
        name: string(d.name),
        since: string(d.since),
        why: string(d.why),
      };
    }),
    dishes: array(x.dishes, dish),
    visits: array(x.visits, visit),
    unnamed_count: number(x.unnamed_count),
    unreviewed_count: number(x.unreviewed_count),
    clear_count: number(x.clear_count),
    weak_count: number(x.weak_count),
    verdicts: array(x.verdicts, (item) => {
      const v = object(item);
      return {
        ...verdict(v),
        visit_id: id(v.visit_id),
        title: string(v.title),
        of_visit_id: nullable(v.of_visit_id, string),
      };
    }),
    weeks: array(x.weeks, (item) => {
      const h = object(item);
      return {
        start: string(h.start),
        pick_name: nullable(h.pick_name, string),
        closed: boolean(h.closed),
      };
    }),
  };
}
/** @param {unknown} value */
function pick(value) {
  const p = object(value),
    state = string(p.state);
  const fields = {
    points: points(p.points),
    lead: points(p.lead),
    needed_points: points(p.needed_points),
    needed_lead: points(p.needed_lead),
    reason: string(p.reason),
  };
  if (state === "pick")
    return {
      ...fields,
      state,
      resident_id: id(p.resident_id),
      name: string(p.name),
    };
  if (
    state !== "too_close" &&
    state !== "below_minimum" &&
    state !== "disabled" &&
    state !== "tie"
  )
    throw new Error("Invalid pick state");
  if (p.resident_id !== null || p.name !== null)
    throw new Error("A non-pick cannot name a person");
  return { ...fields, state, resident_id: null, name: null };
}
/** @param {unknown} value */
function points(value) {
  const n = number(value);
  if (!Number.isInteger(n)) throw new Error("Invalid pick points");
  return n;
}
/** @param {unknown} value */
function guess(value) {
  const g = object(value);
  return {
    resident_id: id(g.resident_id),
    name: string(g.name),
    basis: string(g.basis),
  };
}
/** @param {unknown} value */
export function visit(value) {
  const v = object(value);
  return {
    id: id(v.id),
    dish_ids: array(v.dish_ids, id),
    title: string(v.title),
    area: string(v.area),
    at: string(v.at),
    points: number(v.points),
    filmstrip_url: nullable(v.filmstrip_url, string),
    photo_url: nullable(v.photo_url, string),
    ...mediaSize(v),
    arrival_seen: boolean(v.arrival_seen),
    evidence: evidence(v.evidence),
    reason: nullable(v.reason, string),
    resident: nullable(v.resident, resident),
    named_by: nullable(v.named_by, resident),
    named_at: nullable(v.named_at, string),
    guess: nullable(v.guess, guess),
    verdict: nullable(v.verdict, verdict),
    dispute: nullable(v.dispute, dispute),
    can_name: boolean(v.can_name),
    can_dispute: boolean(v.can_dispute),
    can_settle: boolean(v.can_settle),
  };
}

/** @param {unknown} value @returns {{kind:'not_dish'|'clean'|'duplicate'|'cant_tell',by:ReturnType<typeof resident>|null,at:string}} */
function verdict(value) {
  const v = object(value),
    kind = string(v.kind);
  if (
    kind !== "not_dish" &&
    kind !== "clean" &&
    kind !== "duplicate" &&
    kind !== "cant_tell"
  )
    throw new Error("Invalid visit verdict");
  return { kind, by: nullable(v.by, resident), at: string(v.at) };
}

/** @param {unknown} value */
function dimension(value) {
  const n = number(value);
  if (!Number.isInteger(n) || n <= 0)
    throw new Error("Invalid image dimension");
  return n;
}
/** @param {Record<string, unknown>} value */
function mediaSize(value) {
  return {
    filmstrip_width: nullable(value.filmstrip_width, dimension),
    filmstrip_height: nullable(value.filmstrip_height, dimension),
    photo_width: nullable(value.photo_width, dimension),
    photo_height: nullable(value.photo_height, dimension),
  };
}

/** @param {unknown} value @returns {'strong'|'fair'|'weak'} */
function evidence(value) {
  if (value !== "strong" && value !== "fair" && value !== "weak")
    throw new Error("Invalid evidence rating");
  return value;
}
