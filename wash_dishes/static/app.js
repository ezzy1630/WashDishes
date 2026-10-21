// Dish Watch dashboard entry: session, shell, routing and the shared refresh loop.
// Plain ES modules served straight from the Jetson; there is no build step.
import { fill, h, s, icon, api, state, subscribe, emit, needsYou, isOpen, dishText, incId, person, activeResidents, toast, field, form, closeSheet, sheetOpen, when } from "./js/core.js";
import { openIncident, logDishes } from "./js/incident.js";
import { kitchenPage } from "./js/pages/kitchen.js";
import { livePage } from "./js/pages/live.js";
import { feedPage } from "./js/pages/feed.js";
import { proofPage } from "./js/pages/proof.js";
import { incidentDetailPage } from "./js/pages/incident-detail.js";
import { reviewPage } from "./js/pages/review.js";
import { incidentsPage } from "./js/pages/incidents.js";
import { peoplePage } from "./js/pages/people.js";
import { modelPage } from "./js/pages/model.js";
import { setupPage } from "./js/pages/setup.js";


// `tab` names the nav item a page lives under; Train sits inside Settings.
// `standalone` pages load their own data and open without waiting for /api/dashboard.
const PAGES = {
  proof: { label: "Send", mount: proofPage, standalone: true },
  feed: { label: "Photos", mount: feedPage },
  incident: { label: "Incident", mount: incidentDetailPage, tab: "feed" },
  kitchen: { label: "Overview", mount: kitchenPage, tab: "feed" },
  live: { label: "Live", mount: livePage },
  evidence: { label: "Evidence", mount: feedPage, tab: "feed" },
  review: { label: "Checks", mount: reviewPage, tab: "setup" },
  incidents: { label: "History", mount: incidentsPage, tab: "feed" },
  people: { label: "People", mount: peoplePage, tab: "setup" },
  person: { label: "Person", mount: feedPage, tab: "feed" },
  setup: { label: "Settings", mount: setupPage },
  model: { label: "Train the detector", mount: modelPage, tab: "setup" },
};
const DASHBOARD_MS = 8000, HISTORY_MS = 60000;
const app = document.querySelector("#app");
let page = null, timers = [], refreshing = null, historyToken = 0;

// ---- Brand mark: a plate seen from above with one dish-coloured dot waiting on it.
function brandMark(size = 22) {
  return s("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", "aria-hidden": "true", class: "brand-mark" },
    s("circle", { cx: 12, cy: 12, r: 10, stroke: "currentColor", "stroke-width": 1.6 }),
    s("circle", { cx: 12, cy: 12, r: 5.6, stroke: "currentColor", "stroke-width": 1.3, opacity: .45 }),
    s("circle", { cx: 12, cy: 12, r: 2.1, fill: "var(--out)" }));
}

// ---- Session
async function boot() {
  app.setAttribute("aria-busy", "true");
  try {
    state.session = await api("/api/session");
    state.csrf = state.session.csrf_token || "";
    if (state.session.authenticated) await enter();
    else showLogin();
  } catch (e) {
    fill(app, h("main", { id: "main", class: "full-state" }, brandMark(32), h("h1", {}, "The household server isn't answering"), h("p", {}, e.message), h("button", { type: "button", class: "btn primary", onclick: boot }, icon("refresh", 15), h("span", {}, "Try again"))));
  } finally { app.setAttribute("aria-busy", "false"); }
}

function showLogin(message = "") {
  stopLoops(); page?.destroy(); page = null; state.data = null; state.history = null;
  closeSheet(); document.querySelector("#palette")?.close();
  document.title = "Sign in · Dish Watch";
  const f = form(async fd => {
    const result = await api("/api/login", { method: "POST", body: { password: String(fd.get("password") || "") } });
    state.csrf = result.csrf_token || ""; state.session = { ...state.session, authenticated: true };
    await enter();
  }, { submit: "Sign in" });
  // There is no account name, but password managers (iCloud Passwords especially) file a key under a
  // username and ask for one when the form has none. A fixed hidden name gives them something to save.
  f.append(h("input", { name: "username", type: "text", autocomplete: "username", value: "Dish Watch", readonly: true, tabindex: "-1", "aria-hidden": "true", class: "sr-only" }));
  f.append(field("Access key", h("input", { name: "password", type: "password", required: true, autocomplete: "current-password", maxlength: 256, autofocus: true }), "It's printed on the Jetson. You'll stay signed in on this browser."));
  f.finish();
  fill(app, h("main", { id: "main", class: "login" },
    h("section", { class: "login-box", "aria-labelledby": "login-title" },
      h("div", { class: "login-brand" }, brandMark(26), h("span", {}, "Dish Watch")),
      h("h1", { id: "login-title" }, "Sign in"),
      message ? h("p", { class: "callout late" }, icon("alert", 15), h("span", {}, message)) : null,
      f),
    h("p", { class: "login-foot" }, "Runs on your own network. Footage never leaves the house.")));
  f.querySelector("input")?.focus();
}

async function enter() {
  buildShell();
  // The home page needs neither the dashboard summary nor the full history, so it opens at once;
  // other pages open when the summary arrives. History is never waited for.
  route();
  loadHistory();
  startLoops();
  await refresh();
  if (!page) route();
}

// ---- Shell
const shell = {};
function buildShell() {
  shell.nav = h("nav", { class: "nav", "aria-label": "Pages" }, Object.entries(PAGES).filter(([, p]) => !p.tab).map(([key, p]) =>
    h("a", { href: `#/${key}`, "data-page": key }, p.label, ["review", "feed"].includes(key) ? h("b", { class: "nav-count", hidden: true }) : null)));
  shell.status = h("span", { class: "status" });
  shell.find = h("button", { type: "button", class: "find", onclick: openPalette, "aria-label": "Search", title: "Search (⌘K)", "aria-keyshortcuts": "Meta+K Control+K" }, icon("search", 15), h("kbd", {}, navigator.platform.startsWith("Mac") ? "⌘K" : "Ctrl K"));
  shell.menu = h("div", { class: "menu", hidden: true, role: "menu" },
    h("button", { type: "button", role: "menuitem", onclick: () => { closeMenu(); logDishes(); } }, icon("plus", 15), h("span", {}, "Log dishes by hand")),
    h("a", {href:"#/people", role:"menuitem", onclick:closeMenu}, "People"),
    h("a", {href:"#/review", role:"menuitem", onclick:closeMenu}, "Detection checks"),
    h("a", {href:"#/feed?filter=archive", role:"menuitem", onclick:closeMenu}, "Archive"),
    h("div", { class: "menu-sep" }),
    h("button", { type: "button", role: "menuitem", onclick: signOut }, icon("logout", 15), h("span", {}, "Sign out")));
  shell.menuBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "More", "aria-haspopup": "menu", "aria-expanded": "false", onclick: e => { e.stopPropagation(); shell.menu.hidden ? openMenu() : closeMenu(); } }, icon("more", 18));
  shell.banner = h("div", { class: "banner", hidden: true });
  shell.main = h("main", { id: "main", tabindex: "-1" });
  fill(app, h("header", { class: "top" },
    h("div", { class: "top-in" },
      h("a", { class: "brand", href: "#/proof", "aria-label": "Dish Watch home" }, brandMark(22), h("span", {}, "Dish Watch")),
      shell.nav,
      h("div", { class: "top-r" }, shell.status, shell.find, h("div", { class: "menu-wrap" }, shell.menuBtn, shell.menu)))),
    shell.banner, shell.main);
}

function openMenu() { shell.menu.hidden = false; shell.menuBtn.setAttribute("aria-expanded", "true"); shell.menu.querySelector("button,a")?.focus(); }
function closeMenu() { if (!shell.menu) return; shell.menu.hidden = true; shell.menuBtn.setAttribute("aria-expanded", "false"); }
document.addEventListener("click", e => { if (shell.menu && !shell.menu.hidden && !e.target.closest(".menu-wrap")) closeMenu(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && shell.menu && !shell.menu.hidden) { closeMenu(); shell.menuBtn.focus(); } });

async function signOut() {
  closeMenu();
  try { await api("/api/logout", { method: "POST", body: {} }); } catch (e) { toast(e.message, { error: true }); return; }
  state.session = { ...state.session, authenticated: false };
  showLogin();
}

function renderHeader() {
  if (!shell.nav || !state.data) return;
  const needs = (state.data.checks || []).filter(c => c.status === 'pending' && Date.now() - Date.parse(c.at) < 36 * 3600e3).length;
  const count = shell.nav.querySelector('[data-page="review"] .nav-count');
  if (count) { count.hidden = !needs; count.textContent = String(needs); }
  const evidence = shell.nav.querySelector('[data-page="feed"] .nav-count');
  evidence.hidden = true;
  document.title = `${PAGES[state.route.page]?.label || "Now"} · Dish Watch`;
  shell.banner.hidden = !state.error;
  fill(shell.banner, ...(state.error ? [icon("alert", 14), h("span", {}, `Lost contact with the household server. Showing data from ${when(new Date(state.lastSync).toISOString())}.`), h("button", { type: "button", class: "link", onclick: () => refresh() }, "Retry")] : []));
  renderStatus();
}

// One plain status in the top bar: is the kitchen being watched right now?
function renderStatus() {
  if (!shell.status || !state.data) return;
  const cam = state.data.camera || {}, set = state.data.settings || {}, lv = state.live?.live;
  const [text, tone, title] =
    state.data.mode === "snapshot" ? ["Saved snapshot", "demo", "Isolated review preview; camera and sends are disabled."]
    : state.data.mode === "demo" ? ["Demo data", "demo", "A simulated household. Nothing here is recorded."]
    : state.error ? ["No connection", "late", state.error]
    : cam.view?.state === "moved" ? ["Camera moved", "late", "It moved too far to follow, so new incidents are paused. See Settings → Camera."]
    : cam.configured && !cam.enabled ? ["Paused", "out", "Monitoring is paused in Settings."]
    : lv || cam.connected ? [set.vision_enabled ? "Watching" : "Detection off", set.vision_enabled ? "ok" : "out", cam.vision_status || cam.message || ""]
    : [cam.configured ? "Camera offline" : "No camera", cam.configured ? "late" : "", cam.message || "Connect the camera in Settings."];
  const key = `${text}|${tone}|${title}`;
  if (shell.status.dataset.key === key) return;
  shell.status.dataset.key = key; shell.status.title = title; shell.status.className = `status ${tone}`;
  fill(shell.status, h("i"), text);
}

// ---- Data
async function refresh() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const data = await api("/api/dashboard");
      state.data = data; state.serverOffset = Date.parse(data.server_time) - Date.now(); state.lastSync = Date.now(); state.error = "";
      emit("data");
    } catch (e) {
      if (e.status === 401) return;
      state.error = e.message;
      if (!state.data && !PAGES[state.route.page]?.standalone) showFatal(e.message); else renderHeader();
    } finally { refreshing = null; }
  })();
  return refreshing;
}

async function loadHistory() {
  const token = ++historyToken;
  try {
    const { obligations } = await api("/api/history");
    if (token !== historyToken) return;
    state.history = obligations; state.historyAt = Date.now();
    emit("history");
  } catch (e) { if (e.status !== 401 && !state.history) state.history = []; }
}

function showFatal(message) {
  if (!shell.main) return;
  page?.destroy(); page = null;
  fill(shell.main, h("div", { class: "full-state" }, icon("alert", 28), h("h1", {}, "Couldn't open the dashboard"), h("p", {}, message), h("button", { type: "button", class: "btn primary", onclick: async () => { await refresh(); if (state.data) route(); } }, icon("refresh", 15), h("span", {}, "Try again"))));
}

function startLoops() {
  stopLoops();
  timers.push(setInterval(() => { if (!document.hidden) refresh(); }, DASHBOARD_MS));
  timers.push(setInterval(() => { if (!document.hidden) loadHistory(); }, HISTORY_MS));
}
function stopLoops() { for (const t of timers) clearInterval(t); timers = []; }

document.addEventListener("visibilitychange", () => {
  if (document.hidden || !state.session?.authenticated || !shell.main?.isConnected) return;
  if (Date.now() - state.lastSync > DASHBOARD_MS) refresh();
  if (Date.now() - state.historyAt > HISTORY_MS) loadHistory();
});

subscribe(kind => {
  if (kind === "data") { renderHeader(); page?.update("data"); }
  else if (kind === "history") page?.update("history");
  else if (kind === "live") renderStatus();
  else if (kind === "changed") { refresh(); loadHistory(); }
  else if (kind === "unauthorized" && state.session?.authenticated) { state.session.authenticated = false; showLogin("Your session ended. Sign in again to continue."); }
});

// ---- Routing: #/page/sub?query
function parseHash() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [name, sub] = path.split("/");
  return { page: PAGES[name] ? name : "proof", sub: sub || null, query: Object.fromEntries(new URLSearchParams(query)) };
}

function route() {
  if (!state.session?.authenticated || !shell.main) return;
  const next = parseHash(), first = !page;
  if (!state.data && !PAGES[next.page].standalone) { page?.destroy(); page = null; fill(shell.main, h("div", { class: "full-state boot" }, h("p", {}, "Loading…"))); return; }
  if (page?.beforeLeave && !page.beforeLeave()) {
    const old = state.route;
    history.replaceState(null, '', `#/${old.page}${old.sub ? '/' + old.sub : ''}?${new URLSearchParams(old.query)}`);
    return;
  }
  page?.destroy(); page = null;
  if (sheetOpen()) closeSheet();
  state.route = next;
  const tab = PAGES[next.page].tab || next.page;
  for (const a of shell.nav.children) { if (a.dataset.page === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
  shell.main.dataset.page = next.page;
  fill(shell.main);
  try { page = PAGES[next.page].mount(shell.main, next); }
  catch (e) { console.error(e); fill(shell.main, h("div", { class: "full-state" }, icon("alert", 28), h("h1", {}, "This page hit an error"), h("p", {}, e.message), h("button", { type: "button", class: "btn", onclick: route }, "Reload page"))); }
  renderHeader();
  if (!first) { scrollTo(0, 0); document.querySelector("#page-title")?.focus({ preventScroll: true }); }
}
addEventListener("hashchange", route);

// ---- Command palette (⌘K): pages, actions, people and incidents in one list.
const palette = document.querySelector("#palette");
let paletteItems = [], paletteIndex = 0;
function commands(q) {
  const items = [];
  const add = (group, label, run, { hint = "", iconName = "arrowRight", keywords = "" } = {}) => items.push({ group, label, run, hint, iconName, text: `${label} ${hint} ${keywords}`.toLowerCase() });
  for (const [key, p] of Object.entries(PAGES)) add("Go to", p.label, () => { location.hash = `#/${key}`; }, { iconName: key });
  for (const [id, label] of [["camera", "Camera"], ["areas", "Counter areas"], ["rules", "Rules"], ["alerts", "Owners and alerts"], ["detection", "Detection"], ["storage", "Storage"]]) add("Go to", `Settings · ${label}`, () => { location.hash = `#/setup/${id}`; }, { iconName: "setup" });
  add("Do", "Log dishes by hand", logDishes, { iconName: "plus", keywords: "add manual" });
  add("Do", "Sign out", signOut, { iconName: "logout" });
  for (const r of activeResidents()) add("People", r.name, () => { location.hash = `#/person/${r.id}`; }, { hint: "history", iconName: "people" });
  const seen = new Set(), incidents = [...(state.data?.obligations || []), ...(state.history || [])].filter(o => !seen.has(o.id) && seen.add(o.id));
  for (const o of incidents.slice(0, 400)) add("Dishes", dishText(o, { short: true }), () => openIncident(o.id), { hint: [person(o.resident_id)?.name || "unknown", when(o.created_at), isOpen(o) ? "still out" : o.status === "resolved" ? "washed" : o.status, incId(o.id)].join(" · "), iconName: "dish", keywords: `${o.id} ${o.location || ""} ${o.note || ""}` });
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hits = terms.length ? items.filter(i => terms.every(t => i.text.includes(t))) : items.filter(i => i.group !== "Dishes" || i.hint.includes("still out"));
  return hits.slice(0, 40);
}

function openPalette() {
  if (!state.session?.authenticated || palette.open) return;
  const input = h("input", { type: "search", placeholder: "Find a page, a person or a dish…", "aria-label": "Search", autocomplete: "off", spellcheck: "false", role: "combobox", "aria-controls": "palette-list", "aria-expanded": "true" });
  const list = h("ul", { id: "palette-list", role: "listbox" });
  const render = () => {
    paletteItems = commands(input.value); paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
    let group = "";
    fill(list, ...(paletteItems.length ? paletteItems.flatMap((item, i) => {
      const head = item.group !== group ? h("li", { class: "pal-group", role: "presentation" }, (group = item.group)) : null;
      return [head, h("li", { role: "option", id: `pal-${i}`, "aria-selected": String(i === paletteIndex), onpointermove: () => { if (paletteIndex !== i) { paletteIndex = i; mark(); } }, onclick: () => run(i) }, icon(item.iconName, 15), h("span", {}, item.label), item.hint && h("small", {}, item.hint))];
    }) : [h("li", { class: "pal-empty" }, "Nothing matches.")]));
    input.setAttribute("aria-activedescendant", paletteItems.length ? `pal-${paletteIndex}` : "");
  };
  const mark = () => { for (const li of list.querySelectorAll("[role=option]")) li.setAttribute("aria-selected", String(li.id === `pal-${paletteIndex}`)); list.querySelector(`#pal-${paletteIndex}`)?.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", `pal-${paletteIndex}`); };
  const run = i => { const item = paletteItems[i]; if (!item) return; palette.close(); item.run(); };
  input.addEventListener("input", () => { paletteIndex = 0; render(); });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = paletteItems.length; if (n) { paletteIndex = (paletteIndex + (e.key === "ArrowDown" ? 1 : -1) + n) % n; mark(); } }
    else if (e.key === "Enter") { e.preventDefault(); run(paletteIndex); }
    else if (e.key === "Escape") { e.preventDefault(); palette.close(); }
  });
  paletteIndex = 0;
  fill(palette, h("div", { class: "pal-input" }, icon("search", 16), input, h("kbd", {}, "esc")), list);
  palette.showModal(); render(); input.focus();
}
palette.addEventListener("click", e => { if (e.target === palette) palette.close(); });
document.querySelector(".skip-link")?.addEventListener("click", e => { e.preventDefault(); document.querySelector("#main")?.focus(); });
document.querySelector("#sheet").addEventListener("click", e => { if (e.target.id === "sheet") closeSheet(); });
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (palette.open) palette.close(); else if (!sheetOpen()) openPalette(); }
});

// ---- An open tab keeps old code after a deploy; reload once the server serves a different build.
const build = document.querySelector('script[src*="app.js"]')?.src.match(/v=([\w-]+)/)?.[1];
setInterval(async () => {
  if (document.hidden || sheetOpen() || palette.open || !build) return;
  try { const served = (await (await fetch("/", { cache: "no-store" })).text()).match(/app\.js\?v=([\w-]+)/)?.[1]; if (served && served !== build) location.reload(); } catch { /* offline; try later */ }
}, 60000);

boot();
