import { date } from "../ui.js";
/** @typedef {import('../types.js').Week} Week */
/** @param {string} stamp @param {string} timezone */
function calendarDay(stamp, timezone) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(stamp)) return stamp;
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone,
  }).formatToParts(new Date(stamp));
  const part = (/** @type {string} */ kind) =>
    parts.find((p) => p.type === kind)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
/** @param {string} day @param {number} days */
export function shiftDay(day, days) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
/** @param {Week} data */
export function dutyCopy(data) {
  if (!data.rules.enabled)
    return {
      title: "Kitchen duty is paused",
      explanation: "No one gets picked while it’s off.",
    };
  const duty = data.on_duty;
  if (!duty)
    return {
      title: "Nobody’s on kitchen duty yet",
      explanation: "The first pick comes at the end of a week, once someone has enough points.",
    };
  const title = `${duty.name}’s on kitchen duty`;
  const assigned = calendarDay(duty.since, data.week.timezone),
    since = date(assigned, data.week.timezone);
  const why = duty.why.toLowerCase();
  if (/house vote|operator|override|manual/.test(why))
    return {
      title,
      explanation: `${/house vote/.test(why) ? "Chosen by the house" : "Set by Ezzy"} on ${since}.`,
    };
  if (/pick|most.*(?:points|dishes)/.test(why)) {
    // A weekly pick begins at the previous week's close, as recorded by the API.
    const origin = date(shiftDay(assigned, -7), data.week.timezone);
    const carry = assigned < data.week.start;
    return {
      title,
      explanation: `Most points the week of ${origin}.${carry ? ` No one’s had a clear lead since, so ${duty.name} stays on.` : ""}`,
    };
  }
  // Preserve an unfamiliar operator-provided reason instead of inventing its meaning.
  return { title, explanation: `${duty.why} On duty since ${since}.` };
}
/** The API decides the pick; this only puts its numbers into a sentence. @param {Week} data */
export function pickCopy(data) {
  const { pick, rules, week } = data;
  const closed = week.closed;
  const points = (/** @type {number} */ n) => `${n} ${n === 1 ? "point" : "points"}`;
  const bar = `It takes ${points(rules.min_points)} and a ${rules.min_lead}-point lead.`;
  switch (pick.state) {
    case "disabled":
      return { title: "No pick while duty is paused", detail: "Points still count." };
    case "pick":
      return {
        title: closed ? `${pick.name} was picked` : `${pick.name}’s on track for next week`,
        detail: `${points(pick.points)}, ${pick.lead} ahead of second place.`,
      };
    case "tie":
      return {
        title: closed ? "It ended in a tie" : "It’s a tie at the top",
        detail: `Tied at ${points(pick.points)}. ${closed ? "No one was picked." : `Someone needs a ${rules.min_lead}-point lead.`}`,
      };
    case "too_close":
      return {
        title: closed ? "Too close to pick anyone" : "Too close to call",
        detail: `${points(pick.points)}, but only ${pick.lead} ahead. It takes a ${rules.min_lead}-point lead.`,
      };
    default:
      return {
        title: closed ? "No one was picked" : "No one’s on track for next week",
        detail: `${pick.points ? `Top score is ${pick.points}.` : "No one has points yet."} ${bar}`,
      };
  }
}
/** @param {Week} data */
export function weekSubtitle(data) {
  const range = weekRange(data.week.start, data.week.end, data.week.timezone);
  if (data.week.closed) return `${range} · final`;
  return `${range} · ends ${closeTime(data)}`;
}
/** Format the API instant; the browser never derives a close time from week dates. @param {Week} data */
export function closeTime(data) {
  const stamp = new Date(data.week.closes_at);
  const minute = new Intl.DateTimeFormat("en-US", {
    minute: "numeric",
    timeZone: data.week.timezone,
  }).format(stamp);
  const closes = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    hour: "numeric",
    ...(Number(minute) ? { minute: "2-digit" } : {}),
    timeZone: data.week.timezone,
  })
    .format(stamp)
    .replace(",", "");
  return closes;
}

/** @param {string} start @param {string} end Exclusive local date @param {string} timezone */
export function weekRange(start, end, timezone) {
  const last = shiftDay(end, -1);
  const sameMonth = start.slice(0, 7) === last.slice(0, 7);
  return `${date(start, timezone)} – ${sameMonth ? Number(last.slice(8)) : date(last, timezone)}`;
}
/** @param {Week} data @param {string|number|undefined} viewerId */
export function personalCopy(data, viewerId) {
  const points =
    data.standings.find((p) => String(p.resident_id) === String(viewerId))
      ?.points ?? 0;
  const picked =
    data.pick.state === "pick" &&
    String(data.pick.resident_id) === String(viewerId);
  const onDuty =
    data.rules.enabled &&
    String(data.on_duty?.resident_id) === String(viewerId);
  return {
    title: picked
      ? "You’re on track for next week"
      : onDuty
        ? "You’re on kitchen duty"
        : points
          ? `You have ${points} ${points === 1 ? "point" : "points"}`
          : "You have no points",
    points,
  };
}
