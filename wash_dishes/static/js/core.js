// Shared plumbing: DOM helpers, API client, app state, formatting, people, icons, toasts, sheet.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || (value === false && !key.startsWith("aria-"))) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "style" && typeof value === "object") for (const [k, v] of Object.entries(value)) { if (k.startsWith("--")) el.style.setProperty(k, v); else el.style[k] = v; }
    else if (["checked", "disabled", "required", "multiple", "hidden", "selected", "muted", "autoplay", "playsInline"].includes(key)) el[key] = Boolean(value);
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

// replaceChildren for h()-style children: arrays flatten, null/false/"" are skipped.
export function fill(el, ...children) { el.replaceChildren(); return append(el, children); }

export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false && child !== "") el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) el.setAttribute(key, String(value));
  for (const child of children.flat(Infinity)) if (child) el.append(child);
  return el;
}

// One authored icon set: 24px grid, 1.75 stroke, round joins.
const ICONS = {
  kitchen: ["M3 10.5 12 4l9 6.5", "M5 9.5V20h14V9.5", "M10 20v-5h4v5"],
  review: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7", "M12 16.5h.01"],
  incidents: ["M4 6h16", "M4 12h16", "M4 18h10"],
  people: ["M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8", "M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1", "M16 3.5a4 4 0 0 1 0 7", "M22 21v-1a6 6 0 0 0-4-5.6"],
  model: ["M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z", "M14 17h6", "M17 14v6"],
  setup: ["M4 7h10", "M18 7h2", "M4 17h4", "M12 17h8", "M16 5v4", "M10 15v4"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14", "M20 20l-4-4"],
  plus: ["M12 5v14", "M5 12h14"], close: ["M6 6l12 12", "M18 6 6 18"], check: ["M5 12.5 10 17 19 7"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 7.5V12l3 2"],
  camera: ["M4 7.5h3l1.5-2.5h7L17 7.5h3V19H4z", "M12 10a3.2 3.2 0 1 0 0 6.4 3.2 3.2 0 0 0 0-6.4"],
  arrowRight: ["M5 12h14", "M13 6l6 6-6 6"], arrowLeft: ["M19 12H5", "M11 6l-6 6 6 6"],
  chevronDown: ["M6 9l6 6 6-6"], chevronRight: ["M9 6l6 6-6 6"], chevronLeft: ["M15 6l-6 6 6 6"],
  more: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  play: ["M8 5.5v13l10.5-6.5z"], expand: ["M4 9V4h5", "M20 9V4h-5", "M4 15v5h5", "M20 15v5h-5"],
  logout: ["M15 4h4v16h-4", "M10 8l-4 4 4 4", "M6 12h10"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"], refresh: ["M20 11a8 8 0 1 0-2.3 5.7", "M20 5v6h-6"],
  alert: ["M12 3 2.5 20h19z", "M12 10v4.5", "M12 17.5h.01"], info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 11v5.5", "M12 7.5h.01"],
  undo: ["M9 14 4 9l5-5", "M4 9h10a6 6 0 0 1 0 12h-3"], pause: ["M8 5v14", "M16 5v14"], target: ["M12 3v4", "M12 17v4", "M3 12h4", "M17 12h4", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8"],
  layers: ["M12 3 2.5 8 12 13l9.5-5z", "M2.5 12.5 12 17.5l9.5-5", "M2.5 16.5 12 21.5l9.5-5"],
  upload: ["M12 15V4", "M7 9l5-5 5 5", "M4 15v5h16v-5"], download: ["M12 4v11", "M7 10l5 5 5-5", "M4 15v5h16v-5"], key: ["M14.5 3.5a6 6 0 1 0 0 12 6 6 0 0 0 0-12", "M10.3 13.7 3 21", "M6 18l2.5 2.5", "M8.5 15.5 11 18"],
  sink: ["M3 11h18", "M5 11v3a7 7 0 0 0 14 0v-3", "M12 11V5a2 2 0 0 1 4 0"], dish: ["M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16", "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7"],
};
export function icon(name, size = 16) {
  const svg = s("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon" });
  for (const d of ICONS[name] || ICONS.dish) svg.append(s("path", { d }));
  if (name === "play") svg.firstChild.setAttribute("fill", "currentColor");
  return svg;
}

// ---- App state. Pages read from `state`; `emit` tells mounted views something changed.
export const state = {
  session: null, csrf: "", data: null, history: null, historyAt: 0, live: null,
  serverOffset: 0, lastSync: 0, error: "", route: { page: "kitchen", id: null, query: {} },
};
const listeners = new Set();
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function emit(kind) { for (const fn of [...listeners]) { try { fn(kind); } catch (e) { console.error(e); } } }
export const now = () => Date.now() + state.serverOffset;

export class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
export async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  let body = options.body;
  if (body !== undefined && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  let response;
  try { response = await fetch(path, { method, headers, body, credentials: "same-origin", cache: "no-store", signal: options.signal }); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError("The household server is not responding. Check that the Jetson is on and reachable.", 0); }
  let data = null;
  if (response.status !== 204) { try { data = await response.json(); } catch { /* proxies can answer with HTML */ } }
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") emit("unauthorized");
    throw new ApiError(typeof data?.detail === "string" ? data.detail : `The server answered ${response.status}. Try again.`, response.status);
  }
  return data;
}
export const act = (id, body) => api(`/api/obligations/${id}/action`, { method: "POST", body });

// ---- Formatting
const plural = (n, word) => `${n} ${word}${Number(n) === 1 || ["cutlery", "other"].includes(word) ? "" : word.endsWith("sh") || word.endsWith("ss") ? "es" : "s"}`;
export function dishText(o, { short = false } = {}) {
  const list = (o.dishes || []).map(d => Number(d.quantity) === 1 ? (short ? cap(d.kind) : d.kind) : plural(d.quantity, d.kind));
  if (!list.length) return "Dishes";
  return short ? list.join(", ").replace(/^./, c => c.toUpperCase()) : cap(list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list.at(-1)}` : list[0]);
}
export const dishCount = o => (o.dishes || []).reduce((n, d) => n + Number(d.quantity || 0), 0);
export const cap = text => text ? text[0].toUpperCase() + text.slice(1) : "";
export const incId = id => `#${id}`;
const valid = v => v && !Number.isNaN(Date.parse(v));
export const clock = (v, seconds = false) => valid(v) ? new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: seconds ? "2-digit" : undefined, hourCycle: "h23" }) : "—";
export function when(v) {
  if (!valid(v)) return "—";
  const d = new Date(v), today = new Date(now());
  const days = Math.round((startOfDay(today) - startOfDay(d)) / 864e5);
  const t = clock(v);
  if (days === 0) return t;
  if (days === 1) return `Yesterday ${t}`;
  if (days < 7) return `${d.toLocaleDateString([], { weekday: "short" })} ${t}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${t}`;
}
export const startOfDay = d => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x.getTime(); };
export function duration(ms, { compact = false } = {}) {
  if (!Number.isFinite(ms)) return "—";
  const m = Math.max(0, Math.round(Math.abs(ms) / 60000));
  if (m < 60) return `${m}m`;
  const hrs = Math.floor(m / 60), rest = m % 60;
  if (hrs < 24) return compact ? `${hrs}h${String(rest).padStart(2, "0")}` : `${hrs}h ${rest}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}
export const bytes = n => { n = Number(n || 0); return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(0)} MB` : `${Math.round(n / 1e3)} KB`; };

// Time left before the grace period ends; negative when late.
export function remaining(o) {
  if (!o.timing_known || !valid(o.due_at)) return null;
  return Date.parse(o.due_at) - now();
}
export const isOpen = o => ["pending", "overdue", "review", "disputed"].includes(o.status);
export const isLate = o => o.lifecycle === "open" && o.timing_known && remaining(o) <= 0;
// What, if anything, a person needs to decide about this incident.
export function decision(o) {
  if (!isOpen(o)) return null;
  const reason = o.reason || "";
  if (o.status === "disputed") return { kind: "disputed", label: "Disputed" };
  if (/^Movement through the sink/.test(reason)) return { kind: "washed", label: "Washed?" };
  // Already there when watching began: no clip shows who left it, so nobody is asked. It closes
  // itself once it's gone; the full record can still start its timer.
  if (!o.timing_known) return null;
  // Which of two similar dishes this is only matters once someone answers for it.
  if (/^Dish reappeared/.test(reason) && o.confidence === "confirmed") return { kind: "same", label: "Same dish?" };
  if (o.confidence !== "confirmed" || o.status === "review") return { kind: "owner", label: "Whose dish?" };
  return null;
}
export const needsYou = () => (state.data?.obligations || []).filter(o => decision(o));

// ---- People. Each housemate keeps one quiet tint so a name is recognisable at a glance; the hues
// stay clear of amber, red and green, which only ever mean waiting, late and washed.
export function person(id) { return (state.data?.residents || []).find(r => r.id === id) || null; }
export function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] || "?").slice(0, 2)).toUpperCase();
}
export const activeResidents = () => (state.data?.residents || []).filter(r => r.active);
const HUES = [215, 265, 300, 330, 190, 245];
export function avatar(id, name, size = "") {
  const known = id !== null && id !== undefined && name;
  return h("span", { class: `avatar ${size} ${known ? "" : "unknown"}`, style: known ? { "--hue": HUES[Math.abs(Number(id)) % HUES.length] } : {}, "aria-hidden": "true" }, known ? initials(name) : "?");
}
export function who(id, { fallback = "Unknown" } = {}) {
  const r = person(id);
  return h("span", { class: "who" }, avatar(r?.id, r?.name, "sm"), r ? r.name : h("span", { class: "tx3" }, fallback));
}

// ---- Small visual primitives
export const pill = (text, tone = "") => h("span", { class: `pill ${tone}` }, text);
// Card header: a title, an optional one-line description, then right-aligned tools.
export function head(title, sub = null, ...right) {
  const r = right.flat().filter(Boolean);
  return h("header", { class: "card-head" },
    h("div", { class: "card-title" }, h("h2", {}, title), sub ? (sub instanceof Node ? sub : h("p", {}, sub)) : null),
    r.length ? h("div", { class: "r" }, r) : null);
}
export function quantile(list, q) {
  const v = list.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return null;
  const i = (v.length - 1) * q, lo = Math.floor(i);
  return v[lo] + (v[Math.min(v.length - 1, lo + 1)] - v[lo]) * (i - lo);
}
export const pct = v => Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—";
// "just now", "12 min ago", "3 h ago", then the day and time.
export function ago(v) {
  const t = typeof v === "number" ? v : Date.parse(v);
  if (!Number.isFinite(t)) return "—";
  const m = Math.floor((now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.floor(m / 60)} h ago`;
  return when(new Date(t).toISOString());
}

// ---- Feedback
export function toast(message, { error = false, action = null, timeout = 6000 } = {}) {
  const root = document.querySelector("#toasts");
  const item = h("div", { class: `toast ${error ? "error" : ""}`, role: error ? "alert" : "status" }, icon(error ? "alert" : "check", 15), h("p", {}, message),
    action && h("button", { type: "button", class: "toast-action", onclick: () => { action.run(); item.remove(); } }, action.label),
    h("button", { type: "button", class: "icon-btn", "aria-label": "Dismiss", onclick: () => item.remove() }, icon("close", 14)));
  root.append(item);
  setTimeout(() => item.remove(), timeout);
}

export function button(label, onclick, { kind = "", iconName = null, type = "button", title } = {}) {
  return h("button", { type, class: `btn ${kind}`, onclick, title }, iconName && icon(iconName, 15), label && h("span", {}, label));
}

// Run an async action from a button: disables it while pending and reports errors.
export async function busy(el, fn) {
  if (el) el.disabled = true;
  try { return await fn(); }
  catch (e) { toast(e.message || "Something went wrong.", { error: true }); }
  finally { if (el?.isConnected) el.disabled = false; }
}

export function field(label, input, help = "") {
  input.id ||= `f-${Math.random().toString(36).slice(2, 9)}`;
  const wrap = h("div", { class: "field" }, h("label", { for: input.id }, label), input);
  if (help) { const p = h("p", { class: "help", id: `${input.id}-help` }, help); input.setAttribute("aria-describedby", p.id); wrap.append(p); }
  return wrap;
}

export function form(onSubmit, { submit = "Save", kind = "primary" } = {}) {
  const error = h("p", { class: "form-error", role: "alert" });
  const go = h("button", { class: `btn ${kind}`, type: "submit" }, submit);
  const node = h("form", { class: "form", onsubmit: async event => {
    event.preventDefault();
    if (go.disabled) return;
    error.textContent = ""; go.disabled = true;
    try { await onSubmit(new FormData(node), node); } catch (e) { error.textContent = e.message || "Could not save. Try again."; }
    finally { go.disabled = false; }
  } });
  node.finish = (...extra) => node.append(error, h("div", { class: "form-actions" }, ...extra, go));
  return node;
}

// ---- Side sheet (one at a time). Used for incident files and short forms.
let sheetReturn = null;
export function openSheet(title, body, { wide = false, onClose = null } = {}) {
  const sheet = document.querySelector("#sheet");
  if (!sheet.open) sheetReturn = document.activeElement;
  sheet.classList.toggle("wide", wide);
  sheet.onclose = null;
  sheet.replaceChildren(h("header", { class: "sheet-head" }, h("h2", { id: "sheet-title" }, title), h("button", { type: "button", class: "icon-btn", "aria-label": "Close", onclick: () => sheet.close() }, icon("close", 18))), h("div", { class: "sheet-body" }, body));
  if (!sheet.open) sheet.showModal();
  sheet.onclose = () => { onClose?.(); if (sheetReturn?.isConnected) sheetReturn.focus({ preventScroll: true }); };
  return sheet;
}
export const closeSheet = () => document.querySelector("#sheet")?.close();
export const sheetOpen = () => Boolean(document.querySelector("#sheet")?.open);

export function safeURL(value) {
  if (typeof value !== "string" || !value) return null;
  try { const url = new URL(value, location.origin); return url.origin === location.origin && !url.username ? url.href : null; } catch { return null; }
}

export function empty(title, text, action = null) {
  return h("div", { class: "empty" }, h("h3", {}, title), text && h("p", {}, text), action);
}
export const placeholder = (n = 4) => h("div", { class: "placeholder-rows", "aria-hidden": "true" }, Array.from({ length: n }, () => h("i")));

export function segmented(options, value, onchange, label) {
  const group = h("div", { class: "seg", role: "group", "aria-label": label });
  for (const [key, text] of options) group.append(h("button", { type: "button", "aria-pressed": String(key === value), onclick: event => { for (const b of group.children) b.setAttribute("aria-pressed", String(b === event.currentTarget)); onchange(key); } }, text));
  return group;
}
