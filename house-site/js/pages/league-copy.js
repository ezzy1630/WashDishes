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
      explanation: "The weekly pick is switched off.",
    };
  const duty = data.on_duty;
  if (!duty)
    return {
      title: "Nobody’s on kitchen yet",
      explanation: "The house hasn’t picked anyone yet.",
    };
  const title = `${duty.name}’s on kitchen this week`;
  const assigned = calendarDay(duty.since, data.week.timezone),
    since = date(assigned, data.week.timezone);
  const why = duty.why.toLowerCase();
  if (/house vote|operator|override|manual/.test(why))
    return {
      title,
      explanation: `${/house vote/.test(why) ? "Chosen by the house" : "Set by the house operator"}. On kitchen since ${since}.`,
    };
  if (/pick|most.*(?:points|dishes)/.test(why)) {
    // A weekly pick begins at the previous week's close, as recorded by the API.
    const origin = date(shiftDay(assigned, -7), data.week.timezone);
    const carry = assigned < data.week.start;
    return {
      title,
      explanation: `Most dish points the week of ${origin}.${carry ? ` Nobody else has been a clear pick since, so ${duty.name} stays on.` : ""}`,
    };
  }
  // Preserve an unfamiliar operator-provided reason instead of inventing its meaning.
  return { title, explanation: `${duty.why} On kitchen since ${since}.` };
}
/** @param {Week} data */
export function weekSubtitle(data) {
  const range = weekRange(data.week.start, data.week.end, data.week.timezone);
  if (data.week.closed) return `${range} · closed`;
  // Format the API instant; the browser never derives a close time from week dates.
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
  return `${range} · closes ${closes}`;
}

/** @param {string} start @param {string} end Exclusive local date @param {string} timezone */
export function weekRange(start, end, timezone) {
  const last = shiftDay(end, -1);
  const sameMonth = start.slice(0, 7) === last.slice(0, 7);
  return `${date(start, timezone)} – ${sameMonth ? Number(last.slice(8)) : date(last, timezone)}`;
}
/** @param {Week} data */
export function pickDetail(data) {
  if (!data.rules.enabled) return "Standings still count.";
  const rows = [...data.standings].sort((a, b) => b.points - a.points);
  const top = rows[0];
  if (!top?.points) return "No named points yet.";
  const lead = top.points - (rows[1]?.points || 0);
  return `${top.points} ${top.points === 1 ? "point" : "points"} · ${lead ? `${lead} ahead` : "tied for first"}`;
}
