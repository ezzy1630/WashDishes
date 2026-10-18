// History: every dish that was left out, grouped by day, with whose it was and how it ended.
import { fill, h, icon, state, now, person, avatar, dishText, clock, duration, isOpen, isLate, decision, empty, placeholder, startOfDay, quantile, segmented } from "../core.js";
import { openIncident, outcomePill, thumb, logDishes } from "../incident.js";
import { allIncidents, sat } from "../metrics.js";

const FILTERS = [["all", "All"], ["open", "Still out"], ["late", "Late"], ["needs", "Needs review"], ["resolved", "Washed"], ["cleared", "Taken away"], ["dismissed", "Dismissed"]];
const LIMIT = 300;

export function incidentsPage(main, route) {
  const q = route.query || {};
  const fromQuery = q.status === "disputed" ? "needs" : q.status;
  let filter = FILTERS.some(([k]) => k === fromQuery) ? fromQuery : "all", who = q.person || "", day = q.date || "", text = "";
  const seg = segmented(FILTERS, filter, k => { filter = k; sync(); }, "Show");
  const personSel = h("select", { "aria-label": "Person", onchange: e => { who = e.target.value; sync(); } });
  const search = h("input", { type: "search", placeholder: "Search", "aria-label": "Search dishes", oninput: e => { text = e.target.value.trim().toLowerCase(); renderList(); } });
  const dayChip = h("span");
  const lede = h("p", {});
  const body = h("div", { class: "days" });
  fill(main, h("div", { class: "page narrow" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", { id: "page-title", tabindex: "-1" }, "History"), lede),
      h("button", { type: "button", class: "btn", onclick: logDishes }, icon("plus", 15), h("span", {}, "Log by hand"))),
    h("div", { class: "toolbar" }, seg, dayChip, h("div", { class: "toolbar-r" }, personSel, search)),
    body));

  function sync() {
    const params = new URLSearchParams(Object.entries({ status: filter === "all" ? "" : filter, person: who, date: day }).filter(([, v]) => v));
    history.replaceState(null, "", `#/incidents${params.size ? `?${params}` : ""}`);
    renderList();
  }

  const match = {
    all: () => true, open: isOpen, needs: o => Boolean(decision(o)),
    late: o => o.status !== "dismissed" && (o.overdue_verified || isLate(o)),
    resolved: o => o.status === "resolved", dismissed: o => o.status === "dismissed", cleared: o => o.status === "cleared",
  };

  // One sentence about the last 30 days instead of a row of number tiles.
  let ledeSig = "";
  function renderLede() {
    if (!state.history) { lede.textContent = "Loading…"; return; }
    const list = allIncidents().filter(o => Date.parse(o.created_at) >= now() - 30 * 864e5 && o.status !== "dismissed");
    const late = list.filter(o => o.overdue_verified).length;
    const typical = quantile(list.filter(o => o.status === "resolved" && o.resolved_at && o.timing_known).map(sat), .5);
    const sig = JSON.stringify([list.length, late, typical]);
    if (sig === ledeSig) return; ledeSig = sig;
    lede.textContent = list.length
      ? `Dishes were left out ${list.length} ${list.length === 1 ? "time" : "times"} in the last 30 days. ${late ? `${late} ${late === 1 ? "was" : "were"} washed late.` : "None were washed late."}${typical != null ? ` They're usually washed within ${duration(typical)}.` : ""}`
      : "Nothing has been left out in the last 30 days.";
  }

  let lastSig = "";
  function renderList() {
    const residentsSig = (state.data?.residents || []).map(r => `${r.id}:${r.name}:${r.active}`).join();
    if (residentsSig !== personSel.dataset.sig) {
      personSel.dataset.sig = residentsSig;
      fill(personSel, h("option", { value: "" }, "Everyone"), (state.data?.residents || []).map(r => h("option", { value: r.id }, `${r.name}${r.active ? "" : " (archived)"}`)), h("option", { value: "unknown" }, "No confirmed owner"));
    }
    personSel.value = who;
    fill(dayChip, day ? h("button", { type: "button", class: "chip-x", onclick: () => { day = ""; sync(); } }, new Date(`${day}T00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }), icon("close", 12)) : null);
    if (!state.history) { lastSig = ""; fill(body, h("div", { class: "card" }, placeholder(8))); return; }
    const rows = allIncidents().filter(o => {
      if (who === "unknown" ? o.resident_id && o.confidence === "confirmed" : who && String(o.resident_id) !== who) return false;
      if (day) { const s = startOfDay(new Date(`${day}T00:00`)), t = Date.parse(o.created_at); if (t < s || t >= s + 864e5) return false; }
      if (text && ![dishText(o), o.location, o.note, o.reason, String(o.id), person(o.resident_id)?.name].join(" ").toLowerCase().includes(text)) return false;
      return match[filter](o);
    });
    // The dashboard refreshes every 8 s; rebuilding unchanged rows would refetch every thumbnail.
    const sig = JSON.stringify([filter, who, day, text, rows.slice(0, LIMIT).map(o => [o.id, o.status, o.resident_id, o.confidence, o.resolved_at, o.overdue_verified, (o.evidence || []).length]), Math.floor(now() / 60000)]);
    if (sig === lastSig) return;
    lastSig = sig;
    if (!rows.length) { fill(body, h("div", { class: "card" }, empty("Nothing here", filter === "all" && !who && !day && !text ? "When the camera sees a dish left out, it shows up here." : "Try a different filter."))); return; }
    const groups = new Map();
    for (const o of rows.slice(0, LIMIT)) { const d = startOfDay(new Date(o.created_at)); if (!groups.has(d)) groups.set(d, []); groups.get(d).push(o); }
    fill(body, [...groups].map(([d, list]) => h("section", { class: "day" },
      h("h2", {}, dayName(d), h("span", {}, String(list.length))),
      h("div", { class: "card rows" }, list.map(row)))),
      rows.length > LIMIT ? h("p", { class: "quiet-note" }, `Showing the latest ${LIMIT} of ${rows.length}. Filter to narrow it down.`) : null);
  }

  function row(o) {
    const owner = person(o.resident_id), confirmed = owner && o.confidence === "confirmed";
    return h("button", { type: "button", class: "list-row", onclick: () => openIncident(o.id) },
      thumb(o),
      h("span", { class: "list-main" },
        h("span", { class: "list-title" }, dishText(o, { short: true })),
        h("span", { class: "list-meta" },
          confirmed ? h("span", { class: "who" }, avatar(owner.id, owner.name, "xs"), owner.name) : h("span", { class: "tx3" }, owner ? `${owner.name}?` : person(o.suggested_resident_id) ? `Maybe ${person(o.suggested_resident_id).name}` : "Owner unknown"),
          h("span", {}, clock(o.created_at)), o.location ? h("span", {}, o.location) : o.source === "manual" ? h("span", {}, "logged by hand") : null)),
      outcomePill(o));
  }

  const renderAll = () => { renderLede(); renderList(); };
  renderAll();
  if (q.id) openIncident(Number(q.id));
  return { update(kind) { if (kind === "data" || kind === "history") renderAll(); }, destroy() {} };
}

function dayName(t) {
  const today = startOfDay(now()), diff = Math.round((today - t) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return new Date(t).toLocaleDateString([], diff < 7 ? { weekday: "long" } : { weekday: "long", month: "short", day: "numeric" });
}
