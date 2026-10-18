// A housemate's own page, opened from their personal link: the dishes put down to them, the
// photos behind each one, and a way to dispute it. The token stays after '#', so it is sent only
// as a header and never appears in a request path.
import { fill, h, icon, dishText, when, toast, field, form } from "./core.js";
import { outcomePill, rankEvidence, evidenceLabel } from "./incident.js";
import { scoreboard } from "./scoreboard.js";

const main = document.querySelector("#main");
const token = location.hash.slice(1);

async function call(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "X-Personal-Token": token, ...(options.body ? { "Content-Type": "application/json" } : {}) }, cache: "no-store", credentials: "omit" });
  if (!response.ok) {
    let detail = `The server answered ${response.status}.`;
    try { detail = (await response.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response;
}

// Photos and clips need the header too, so they load as blobs.
function media(e) {
  const holder = h("div", { class: "me-media" }, h("div", { class: "media-missing" }, "Loading…"));
  call(`/api/me/evidence/${encodeURIComponent(e.id)}`).then(r => r.blob()).then(blob => {
    const url = URL.createObjectURL(blob);
    fill(holder, e.kind === "clip" ? h("video", { src: url, controls: true, preload: "metadata", playsInline: true }) : h("img", { src: url, alt: e.label }));
  }).catch(() => fill(holder, h("div", { class: "media-missing" }, `This ${e.kind === "clip" ? "clip" : "photo"} has expired or can't be shown`)));
  return holder;
}

function record(o) {
  const ev = rankEvidence(o.evidence);
  const lead = ev.find(e => e.kind !== "clip");
  const shown = h("div", {}, lead ? media(lead) : h("p", { class: "tx3" }, o.source === "manual" ? "Logged by hand, so there's no photo." : "No photo for this one."));
  const others = ev.length > 1 ? h("details", { class: "me-more" }, h("summary", {}, `All ${ev.length} photos and clips`),
    ev.filter(e => e !== lead).map(e => h("figure", {}, media(e), h("figcaption", { class: "tx3" }, `${evidenceLabel(e)} · ${when(e.created_at)}`)))) : null;
  const act = h("div", { class: "me-act" });
  if (o.status === "disputed") act.append(h("p", { class: "tx2" }, "You disputed this. It doesn't count against you until someone reviews it."));
  else act.append(h("button", { type: "button", class: "btn", onclick: () => disputeForm(o, act) }, "This isn't right"));
  return h("article", { class: "card me-card" },
    h("header", { class: "me-head" }, h("div", {}, h("h2", {}, dishText(o)), h("p", { class: "tx3" }, [when(o.created_at), o.location].filter(Boolean).join(" · "))), outcomePill(o)),
    o.attribution === "camera" ? h("p", { class: "callout" }, icon("info", 15), h("span", {}, "The camera named you from your appearance; no person has checked it yet. If it wasn't you, say so below.")) : null,
    shown, others, act);
}

function disputeForm(o, act) {
  const f = form(async fd => {
    await call(`/api/me/dispute/${o.id}`, { method: "POST", body: JSON.stringify({ note: String(fd.get("note") || "").trim() }) });
    toast("Disputed. It stops counting until someone reviews it."); load();
  }, { submit: "Dispute" });
  f.append(field("What's wrong?", h("textarea", { name: "note", rows: 3, maxlength: 1000, required: true, placeholder: "Not mine, already washed, not a dish…" })));
  f.finish();
  fill(act, f);
  f.querySelector("textarea").focus();
}

async function load() {
  if (!token) { fill(main, h("h1", {}, "This link is incomplete"), h("p", { class: "tx2" }, "Ask for your personal link again.")); return; }
  try {
    const data = await (await call("/api/me")).json();
    document.title = `${data.name} · Dish Watch`;
    const open = data.records.filter(o => ["pending", "overdue", "review"].includes(o.status));
    const late = data.records.filter(o => o.overdue_verified).length;
    const grace = data.settings.deadline_minutes;
    fill(main,
      h("header", { class: "page-head" }, h("div", {},
        h("h1", {}, data.demo ? `${data.name} (demo)` : data.name),
        h("p", { class: "tx2" }, `Dishes put down to you in the last 60 days: ${data.records.length}, ${late} still out ${grace} minutes after being set down. `,
          open.length ? `${open.length} ${open.length === 1 ? "is" : "are"} out now.` : "Nothing of yours is out now."))),
      h("p", { class: "callout" }, icon("info", 15), h("span", {}, "Every record shows the camera's photos. If one isn't yours, or it's wrong, dispute it: it stops counting until someone reviews it.")),
      data.records.length ? h("div", { class: "me-list" }, data.records.map(record)) : h("p", { class: "quiet-note" }, "Nothing has been put down to you."),
      h("section", { class: "card" }, h("header", { class: "card-head" }, h("div", { class: "card-title" }, h("h2", {}, "How well the camera is doing"))),
        h("div", { class: "card-body" }, scoreboard(data.scoreboard))));
  } catch (e) {
    fill(main, h("h1", {}, "Can't open your page"), h("p", { class: "tx2" }, e.message));
  } finally { main.setAttribute("aria-busy", "false"); }
}

load();
