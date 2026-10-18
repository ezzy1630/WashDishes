// One dish incident in a side sheet: the photos, what happened, and what you can do about it.
import { fill, h, icon, api, act, state, emit, now, person, avatar, activeResidents, cap, dishText, incId, when, clock, duration, remaining, decision, isOpen, toast, field, form, openSheet, closeSheet, safeURL, button, pill, ago } from "./core.js";

// How an incident stands, in a few words: the pill used everywhere a dish is listed.
export function outcome(o) {
  const sat = o.resolved_at ? Date.parse(o.resolved_at) - Date.parse(o.created_at) : null;
  if (o.status === "dismissed") return { text: "Dismissed", tone: "muted" };
  if (o.status === "cleared") return { text: "Taken away in time", tone: "muted" };
  if (o.status === "resolved") {
    if (o.closed_by_camera) return { text: sat != null ? `Out ${duration(sat)}, then taken away` : "Taken away late", tone: "late" };
    if (o.overdue_verified) return { text: o.timing_known && sat != null ? `Washed after ${duration(sat)}` : "Washed late", tone: "late" };
    return { text: o.timing_known && sat != null ? `Washed in ${duration(sat)}` : "Washed", tone: "done" };
  }
  if (o.status === "disputed") return { text: "Disputed", tone: "ask" };
  const dec = decision(o), left = remaining(o);
  if (dec) return { text: dec.label, tone: "ask" };
  if (left === null) return { text: "Out · no timer", tone: "out" };
  return left <= 0 ? { text: `${duration(left)} late`, tone: "late" } : { text: `${duration(left)} left`, tone: "out" };
}
export const outcomePill = o => { const x = outcome(o); return pill(x.text, x.tone); };

// Some server event descriptions are written for a log; say them the way a housemate would.
const single = text => cap(String(text || "").replace(/\b1 (?=([a-z]))/g, (_, c) => "aeiou".includes(c) ? "an " : "a "));
export function describeEvent(e, o) {
  if (e.event === "camera_moved") return "The camera moved too far to follow. New dishes are paused until it's put back or the view is confirmed in Settings";
  if (e.event === "camera_restored") return "The camera is back in place";
  if (!o) return single(e.description);
  const owner = o.confidence === "confirmed" ? person(o.resident_id) : null, what = dishText(o).toLowerCase();
  const whose = owner ? `${owner.name}'s ${what}` : single(/^\d/.test(what) ? what : `1 ${what}`);
  if (e.event === "deadline_passed") return `${whose} went past the grace period`;
  if (e.event === "overdue_observed") return `${whose} ${/^\d+ |s$| and /.test(what) ? "were" : "was"} still out after the grace period`;
  if (e.event === "confirm" && owner) return `Confirmed as ${owner.name}'s`;
  if (e.event === "observation_gap" || e.event === "dish_continuity_uncertain") return `Lost sight of the ${what.replace(/^\d+ /, "")} for a while`;
  return single(e.description);
}

// Keep the camera's full scene first. Identity crops supplement the scene.
export function rankEvidence(list) {
  const rank = e => e.kind === "clip" ? 5 : /^Person handling|^Person near/.test(e.label) ? 0 : /^Dish left|^Already present/.test(e.label) ? 1 : /^Taken from/.test(e.label) ? 2 : /^Who handled/.test(e.label) ? 4 : 3;
  return [...(list || [])].filter(e => !e.expired && e.url).sort((a, b) => rank(a) - rank(b));
}
export function evidenceLabel(e) {
  if (e.kind === "clip") return "Clip";
  if (/^Who handled/.test(e.label)) return "Who handled it";
  if (/^Person handling/.test(e.label)) return "Person nearby";
  if (/^Dish left/.test(e.label)) return "Where it was left";
  return e.label || "Photo";
}
export const leadPhoto = o => rankEvidence(o.evidence).find(e => e.kind !== "clip") || null;
export function thumb(o, size = "") {
  const lead = leadPhoto(o);
  return h("span", { class: `thumb ${size}` }, lead ? h("img", { src: lead.url, alt: "", loading: "lazy" }) : icon(o.source === "manual" ? "plus" : "dish", 16));
}

export function media(e, { controls = true } = {}) {
  const url = safeURL(e.url);
  const type = e.kind === "clip" ? "clip" : "photo";
  if (!url) return h("div", { class: "media-missing" }, `This ${type} expired under the retention settings`);
  const node = e.kind === "clip" ? h("video", { src: url, controls, preload: "metadata", playsInline: true }) : h("img", { src: url, alt: e.label || "Kitchen photo", loading: "lazy" });
  node.addEventListener("error", () => node.replaceWith(h("div", { class: "media-missing" }, `This ${type} couldn't be loaded`)));
  return node;
}

export function openIncident(id) { closeSheet(); location.hash = `#/incident/${id}`; }

export async function openLegacyIncident(id) {
  const sheet = openSheet("Loading…", h("div", { class: "sheet-loading" }, "Loading…"), { wide: true });
  try {
    const o = await api(`/api/obligations/${id}`);
    if (!sheet.open) return;
    renderFile(o);
  } catch (e) {
    fill(sheet.querySelector(".sheet-body"), h("p", { class: "form-error" }, e.message), button("Try again", () => openIncident(id), { kind: "primary" }));
  }
}

function renderFile(o) {
  const sheet = document.querySelector("#sheet");
  sheet.querySelector("#sheet-title").textContent = dishText(o);
  const ev = rankEvidence(o.evidence);
  let lead = ev.find(e => e.kind !== "clip") || ev[0];
  const owner = person(o.resident_id), cleaner = person(o.cleaner_id), suggested = person(o.suggested_resident_id);
  const confirmed = owner && o.confidence === "confirmed";
  const left = remaining(o), sat = (o.resolved_at ? Date.parse(o.resolved_at) : now()) - Date.parse(o.created_at);
  const late = o.overdue_verified || (isOpen(o) && left !== null && left <= 0);
  const stage = h("figure", { class: "file-photo" });
  const showLead = () => fill(stage, lead
    ? [media(lead), h("figcaption", {}, h("span", {}, evidenceLabel(lead)), h("time", {}, clock(lead.created_at, true)))]
    : h("div", { class: "media-missing" }, o.source === "manual" ? "Logged by hand, so there's no photo." : "No photo attached, or it expired."));
  showLead();
  const strip = ev.length > 1 ? h("div", { class: "file-thumbs" }, ev.map(e => h("button", { type: "button", "aria-label": evidenceLabel(e), "aria-pressed": String(e === lead), onclick: event => {
    lead = e; showLead();
    for (const b of event.currentTarget.parentElement.children) b.setAttribute("aria-pressed", String(b === event.currentTarget));
  } }, e.kind === "clip" ? h("span", { class: "clip" }, icon("play", 14)) : h("img", { src: e.url, alt: "", loading: "lazy" })))) : null;
  const facts = [
    ["Owner", owner ? h("span", { class: "who" }, avatar(owner.id, owner.name, "sm"), owner.name, !confirmed ? h("span", { class: "tx3" }, "not confirmed") : o.attribution === "camera" ? h("span", { class: "tx3" }, "named by the camera") : null) : h("span", { class: "tx3" }, "Not known yet")],
    suggested && !confirmed ? ["Camera's guess", suggested.name] : null,
    ["Where", o.location || (o.source === "manual" ? "Logged by hand" : "—")],
    [o.timing_known ? "Set down" : "First seen", when(o.created_at)],
    o.status === "resolved" ? ["Marked washed", `${when(o.resolved_at)}${cleaner ? ` by ${cleaner.name}` : ""}`] : o.timing_known && isOpen(o) ? ["Due", when(o.due_at)] : null,
    ["Time out", h("span", { class: late ? "late" : "" }, duration(sat), late ? " · past the grace period" : "")],
    o.source === "baseline" ? ["Note", "Already here when monitoring started"] : null,
  ].filter(Boolean);
  fill(sheet.querySelector(".sheet-body"), h("div", { class: "file" },
    h("div", { class: "file-top" }, outcomePill(o), h("span", { class: "tx2" }, `${confirmed ? owner.name : "Owner unknown"} · ${o.timing_known ? "set down" : "first seen"} ${ago(o.created_at)}`), h("span", { class: "tx3 num" }, incId(o.id))),
    reasonNote(o),
    h("div", { class: "file-grid" },
      h("div", { class: "file-main" }, evidenceTimeline(o),
        h("details", { class: "file-sec evidence-originals" }, h("summary", {}, "All attached photos and clips"), stage, strip),
        h("details", { class: "file-sec evidence-originals" }, h("summary", {}, "Activity log"), timeline(o))),
      h("aside", { class: "file-side" },
        h("dl", { class: "facts" }, facts.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v)))),
        actions(o, decision(o)),
        h("a", { class: "btn sm", href: `#/evidence?record=${o.id}`, onclick: closeSheet }, icon("camera", 14), "Four-stage image"),
        h("a", { class: "btn sm proof-link", href: `/api/obligations/${o.id}/proof`, download: "" }, icon("upload", 14), h("span", {}, "Download proof")),
        h("p", { class: "help" }, "A ZIP with the photos, clips, timeline and checksums, to show the person it concerns.")))));
}

function originalPhoto(e, className = "") {
  const url = safeURL(e.url);
  return url ? h("a", { href: url, target: "_blank", rel: "noopener", class: className, "aria-label": `Open original photo: ${e.label}` }, media(e)) : media(e);
}

function evidenceTimeline(o) {
  const data = o.evidence_timeline;
  if (!data) return h("p", { class: "callout" }, "The evidence timeline is unavailable. Open the attached originals below.");
  const states = { captured: "Photo captured", missing: "Not captured", expired: "Photo expired", review: "Needs review", no_timer: "No timer", not_due: "Not due yet", human_confirmed: "Human confirmed", taken_late: "Taken away late", disputed: "Disputed", dismissed: "Dismissed" };
  const timestamp = at => new Date(at).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
  return h("section", { class: "evidence-timeline", "aria-label": "Incident evidence timeline" },
    h("div", { class: "evidence-heading" }, h("h3", {}, "Evidence timeline"), h("p", {}, "Full camera view at each step. Open a photo for the original.")),
    h("ol", { class: "evidence-stages" }, data.stages.map(s => {
      const picture = h("figure", { class: "evidence-scene" },
        s.photo ? originalPhoto(s.photo, "evidence-full") : h("div", { class: "evidence-empty" }, icon("camera", 20), h("span", {}, "No full-scene photo attached")),
        s.detail && !s.detail.expired && s.detail.url ? h("div", { class: `evidence-detail ${s.photo ? "" : "detail-only"}` }, originalPhoto(s.detail), h("span", {}, "Handler detail")) : null);
      return h("li", { class: "evidence-step" },
        h("div", { class: "evidence-step-heading" }, h("h4", {}, s.title), h("span", { class: `evidence-state ${s.state === "review" || s.state === "disputed" ? "ask" : ""}` }, states[s.state] || s.state)),
        picture,
        s.at ? h("time", { datetime: s.at, class: "evidence-time" },
          s.key === "deadline" && !s.photo ? "Deadline · " : s.state === "human_confirmed" && !s.photo ? "Marked washed · " : "",
          timestamp(s.at)) : null,
        s.decision_at && s.photo ? h("time", { datetime: s.decision_at, class: "evidence-time" }, "Reported wash time · ", timestamp(s.decision_at)) : null,
        h("p", { class: "evidence-summary" }, s.summary),
        s.clips.map(c => h("details", { class: "evidence-clip" }, h("summary", {}, icon("play", 12), c.expired ? "Original clip expired" : "Play original clip", h("time", { datetime: c.created_at }, clock(c.created_at, true))),
          h("p", {}, "Event window around this capture; clip boundaries are approximate."), media(c))));
    })),
    h("div", { class: "evidence-limits" }, h("h4", {}, "What this establishes"), data.limitations.map(line => h("p", {}, line))));
}

function timeline(o) {
  const list = [...(o.events || [])].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
  if (!list.length) return h("p", { class: "tx3" }, "Nothing recorded.");
  return h("ol", { class: "timeline" }, list.map(e => h("li", {}, h("time", { datetime: e.created_at }, when(e.created_at)), h("span", {}, describeEvent(e, o)))));
}

function reasonNote(o) {
  const r = o.reason || o.note;
  if (!r) return null;
  const text = /^Dish reappeared after an observation gap/.test(r) ? "The dish was out of view for a while. Check it's the same one before its timer counts." : r;
  return h("p", { class: "callout" }, icon("info", 15), h("span", {}, text));
}

function actions(o, dec) {
  const wrap = h("section", { class: "decide", id: "file-actions" });
  const people = activeResidents();
  const done = async (body, message) => { await act(o.id, body); toast(message); emit("changed"); const fresh = await api(`/api/obligations/${o.id}`); if (document.querySelector("#sheet").open) renderFile(fresh); };
  if (isOpen(o)) {
    if (o.confidence !== "confirmed" || dec) {
      wrap.append(h("h3", {}, o.resident_id && o.confidence === "confirmed" ? "Owner" : "Whose was it?"),
        people.length ? h("div", { class: "pick" }, people.map(r => h("button", { type: "button", class: `pick-btn ${r.id === o.suggested_resident_id ? "suggested" : ""}`, onclick: e => busyBtn(e, () => done({ action: "confirm", resident_id: r.id }, `Marked as ${r.name}'s.`)) },
          avatar(r.id, r.name, "sm"), h("span", {}, r.name), r.id === o.suggested_resident_id ? h("small", {}, "camera's guess") : null)))
          : h("p", { class: "help" }, "Add people on the People page first."));
    }
    const row = h("div", { class: "decide-row" }, button("Mark washed", () => inline(wrap, o, "resolve", done), { kind: "primary", iconName: "check" }));
    if (!o.timing_known) row.append(button("Start timer", () => inline(wrap, o, "start_timer", done), { iconName: "clock" }));
    if (o.resident_id && o.confidence === "confirmed") row.append(button("Change owner", () => inline(wrap, o, "reassign", done)));
    const more = h("div", { class: "decide-row" });
    if (o.status !== "disputed") more.append(button("Dispute", () => inline(wrap, o, "dispute", done), { kind: "quiet sm" }));
    more.append(button("Not a dish", () => inline(wrap, o, "dismiss", done), { kind: "quiet sm" }));
    wrap.append(row, more);
  } else {
    const row = h("div", { class: "decide-row" }, button("Reopen", () => inline(wrap, o, "reopen", done), { iconName: "undo" }));
    if (o.status === "resolved") row.append(button("Change owner", () => inline(wrap, o, "reassign", done)), button("Dispute", () => inline(wrap, o, "dispute", done), { kind: "quiet" }));
    wrap.append(row);
  }
  return wrap;
}

async function busyBtn(event, fn) {
  const el = event.currentTarget; el.disabled = true;
  try { await fn(); } catch (e) { toast(e.message, { error: true }); } finally { if (el.isConnected) el.disabled = false; }
}

// Secondary decisions open in place under the buttons; no stacked dialogs.
function inline(wrap, o, action, done) {
  wrap.querySelector(".inline-form")?.remove();
  const titles = { resolve: "Mark washed", start_timer: "Start the timer", reassign: "Change owner", dispute: "Dispute", dismiss: "Dismiss", reopen: "Reopen" };
  const messages = { resolve: "Marked washed.", start_timer: "Timer started.", reassign: "Owner changed.", dispute: "Disputed. It won't count against anyone until it's settled.", dismiss: "Dismissed.", reopen: "Reopened." };
  const f = form(async fd => {
    const body = { action, note: String(fd.get("note") || "").trim() };
    if (action === "reassign") body.resident_id = Number(fd.get("resident_id")) || null;
    if (action === "resolve") {
      body.cleaner_id = Number(fd.get("cleaner_id")) || null;
      const t = fd.get("resolved_at");
      if (t) { const at = new Date(t); if (at.getTime() > now() + 60000) throw new Error("The wash time can't be in the future."); body.resolved_at = at.toISOString(); }
    }
    await done(body, messages[action]);
  }, { submit: titles[action], kind: action === "dismiss" ? "danger" : "primary" });
  f.classList.add("inline-form");
  const select = (name, value, empty) => { const s = h("select", { name }, h("option", { value: "" }, empty), activeResidents().map(r => h("option", { value: r.id }, r.name))); s.value = value == null ? "" : String(value); return s; };
  if (action === "resolve") {
    const d = new Date(now()), local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    f.append(h("div", { class: "form-grid" }, field("Washed by", select("cleaner_id", o.resident_id, "Not sure")), field("When", h("input", { type: "datetime-local", name: "resolved_at", value: local, required: true }))));
    f.append(h("p", { class: "help" }, "Use the real time it was washed. That decides whether it was late."));
  }
  if (action === "reassign") { const s = select("resident_id", o.resident_id, "Choose someone"); s.required = true; f.append(field("Whose was it?", s)); }
  if (action === "start_timer") f.append(h("p", { class: "help" }, `A ${state.data?.settings?.deadline_minutes ?? 120}-minute grace period starts now. It doesn't say who used the dish or when.`));
  if (action === "dispute") f.append(h("p", { class: "help" }, "It keeps its photos but stops counting against anyone until it's settled."));
  if (action === "dismiss") f.append(h("p", { class: "help" }, "For a false detection or a mistake. It stays in History and can be reopened."));
  f.append(field(action === "dispute" ? "What's wrong?" : "Note", h("textarea", { name: "note", rows: 2, maxlength: 1000, required: action === "dispute", placeholder: action === "dispute" ? "Say what needs another look" : "Optional" })));
  f.finish(button("Cancel", () => f.remove(), { kind: "quiet" }));
  wrap.append(f);
  f.querySelector("select,input,textarea")?.focus();
}

// Logging dishes by hand, for things the camera missed.
export function logDishes() {
  const kinds = ["plate", "bowl", "cup", "mug", "glass", "pot", "pan", "fork", "knife", "spoon", "cutlery", "other"];
  const rows = h("div", { class: "dish-rows" });
  const addRow = () => {
    if (rows.childElementCount >= 12) return;
    const row = h("div", { class: "dish-row" }, h("select", { "aria-label": "Dish" }, kinds.map(k => h("option", { value: k }, k[0].toUpperCase() + k.slice(1)))), h("input", { type: "number", min: 1, max: 100, value: 1, "aria-label": "How many", required: true }),
      h("button", { type: "button", class: "icon-btn", "aria-label": "Remove", onclick: () => { if (rows.childElementCount > 1) row.remove(); } }, icon("close", 14)));
    rows.append(row);
  };
  addRow();
  const who = h("select", { name: "resident_id" }, h("option", { value: "" }, "Not sure"), activeResidents().map(r => h("option", { value: r.id }, r.name)));
  const f = form(async fd => {
    const dishes = [...rows.children].map(r => ({ kind: r.querySelector("select").value, quantity: Number(r.querySelector("input").value) }));
    const o = await api("/api/obligations", { method: "POST", body: { resident_id: Number(fd.get("resident_id")) || null, dishes, note: String(fd.get("note") || "").trim() } });
    closeSheet(); toast(`Logged ${dishText(o).toLowerCase()}.`); emit("changed");
  }, { submit: "Log dishes" });
  f.append(h("p", { class: "help" }, `The ${state.data?.settings?.deadline_minutes ?? 120}-minute grace period starts now. Leave the owner as "Not sure" unless you know.`),
    field("Whose?", who), h("div", { class: "field" }, h("label", {}, "Dishes"), rows, h("button", { type: "button", class: "link", onclick: addRow }, "+ Add another")),
    field("Note", h("textarea", { name: "note", rows: 2, maxlength: 1000, placeholder: "Optional" })));
  f.finish();
  openSheet("Log dishes by hand", f);
}
