// Saved before/use/departure/later drafts; all four observations need a person's review.
import { api, fill, h, head, state, when, button, empty, toast } from "../core.js";
import { openIncident } from "../incident.js";

const LABELS = { review: "Needs review", incomplete: "Missing stages", ready: "Reviewed sequence", rejected: "Not supported" };
const QUESTIONS = [
  ["before", "Before: the target dish is absent from that position, or its clean starting state is visible."],
  ["using", "Use: this person actually uses or places this dish; they are not just standing nearby."],
  ["leaving", "Departure: the used dish is left behind when the person leaves."],
  ["later", "Later: the same dish is visibly left out in the later image; identity and condition are clear."]
];

export function evidencePage(main, route) {
  let alive = true, items = [], selected = Number(route?.query?.record) || null, saving = false, filter = selected ? "all" : "attention", timer;
  const stats = h("p", { class: "tx2", "aria-live": "polite" }, "Loading saved sequences…");
  const list = h("div", { class: "sequence-list" });
  const detail = h("section", { class: "sequence-detail", "aria-label": "Selected evidence sequence" });
  const error = h("p", { class: "form-error", role: "alert" });
  const filters = h("select", { "aria-label": "Show saved sequences", onchange: e => { filter = e.target.value; renderList(); renderDetail(); } },
    [["attention", "Later photo needs review"], ["ready", "Reviewed sequences"], ["all", "Recent saved drafts"]].map(([value, text]) => h("option", { value, selected: value === filter }, text)));
  fill(main, h("div", { class: "page" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", { id: "page-title", tabindex: "-1" }, "Evidence"),
      h("p", {}, "Automatically saved before, use, departure and later views. Review the whole sequence before sharing.")),
      h("div", { class: "page-tools" }, filters, button("Refresh", () => load(true)))),
    stats, error,
    h("div", { class: "sequence-workspace" }, h("aside", { "aria-label": "Saved evidence collection" }, list), detail)));

  function attention(i) { return i.past_deadline && ["review", "incomplete"].includes(i.status) && i.outcome === "open"; }
  function renderList() {
    const visible = items.filter(i => filter === "all" || (filter === "ready" ? i.status === "ready" : attention(i)));
    if (!visible.some(i => i.obligation_id === selected)) selected = visible[0]?.obligation_id || null;
    fill(list, visible.length ? visible.map(i => h("button", { type: "button", class: "sequence-row", "aria-pressed": String(i.obligation_id === selected), onclick: () => {
      selected = i.obligation_id; renderList(); renderDetail();
    } }, h("span", { class: "row-actions" }, h("b", {}, i.dishes), h("span", { class: "tx3" }, `#${i.obligation_id}`)),
    h("span", {}, i.location || "Camera view"), h("small", {}, `${LABELS[i.status]} · ${i.captured}/4 photos${i.stale ? " · updating" : ""}`)))
      : h("p", { class: "quiet-note" }, filter === "attention" ? "No open records have a later photo waiting for review. Drafts appear automatically as stages are captured." : "No sequences in this view."));
  }
  function renderDetail() {
    const item = items.find(i => i.obligation_id === selected);
    if (!item) { fill(detail, empty("Choose a saved sequence", "Each draft is one image with four stages. Missing images remain visibly missing.")); return; }
    const errors = h("p", { class: "form-error", role: "alert" });
    const review = h("form", { class: "sequence-review" });
    const controls = new Map();
    for (const [key, question] of QUESTIONS) {
      const choice = item.checks[key] === true ? "yes" : item.checks[key] === false ? "no" : "unknown";
      const select = h("select", { "aria-label": `Review ${key}` }, [["unknown", "Cannot tell"], ["yes", "Yes, shown"], ["no", "No, not shown"]].map(([value, label]) => h("option", { value, selected: value === choice }, label)));
      controls.set(key, select);
      review.append(h("label", { class: "sequence-question" }, h("span", {}, question), select));
    }
    const save = h("button", { type: "submit", class: "btn primary", disabled: saving || !item.image_url }, "Save sequence review");
    review.addEventListener("submit", async event => {
      event.preventDefault(); if (saving) return;
      saving = true; save.disabled = true; errors.textContent = "";
      const checks = Object.fromEntries([...controls].map(([key, select]) => [key, select.value === "unknown" ? null : select.value === "yes"]));
      try {
        await api(`/api/sequences/${item.obligation_id}/review`, { method: "PUT", body: { fingerprint: item.fingerprint, checks } });
        toast("Sequence review saved. Ownership is unchanged."); await load(true);
      } catch (e) { errors.textContent = e.message; }
      finally {
        saving = false;
        const current = alive && detail.querySelector('button[type="submit"]');
        if (current) current.disabled = !items.find(i => i.obligation_id === selected)?.image_url;
      }
    });
    review.append(h("p", { class: "help" }, "Four photos alone do not establish the action. Keep Cannot tell when the item or action is unclear. This review does not assign an owner, mark washing, or label training data."), errors, save);
    const originals = h("div", { class: "sequence-originals" }, item.stages.map(s => h("div", {}, h("b", {}, s.title),
      s.sha256 && s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener", class: "link" }, "Open original") : h("span", { class: "tx3" }, "Missing or expired"),
      s.at ? h("time", { datetime: s.at }, s.time_basis === "legacy_record" ? "Record time (approx.) · " : "Captured · ", when(s.at)) : null)));
    fill(detail, h("header", { class: "page-head" }, h("div", {}, h("h2", {}, `Record #${item.obligation_id} · ${item.dishes}`),
      h("p", {}, `${LABELS[item.status]} · ${item.captured}/4 captured${item.owner ? ` · recorded owner: ${item.owner}` : " · owner unconfirmed"}`)),
      h("div", { class: "row-actions" }, button("Open record", () => openIncident(item.obligation_id)),
        item.image_url ? h("a", { class: "btn primary", href: item.image_url, download: "" }, "Download image") : null,
        h("a", { class: "btn", href: `/api/obligations/${item.obligation_id}/proof`, download: "" }, "Originals + clips"))),
      item.image_url ? h("a", { href: `${item.image_url}?v=${encodeURIComponent(item.reviewed_at || item.updated_at)}`, target: "_blank", rel: "noopener", class: "sequence-image-link", "aria-label": "Open full-resolution four-stage image" },
        h("img", { class: "sequence-image", src: `${item.image_url}?v=${encodeURIComponent(item.reviewed_at || item.updated_at)}`, alt: `Four-stage camera evidence for record ${item.obligation_id}; ${LABELS[item.status]}` }))
        : h("p", { class: "callout" }, item.stale ? "This record changed. Its image is being updated; check again shortly." : "The saved image expired or is unavailable. The record history remains."),
      h("p", { class: "help" }, item.limitation), item.warnings.map(w => h("p", { class: "callout" }, w)), originals,
      h("section", { class: "card card-body" }, h("h3", {}, "Does the sequence show the action?"), review));
  }
  async function load(force = false) {
    if (saving && !force) return;
    try {
      const response = await api("/api/sequences"); if (!alive) return;
      const requested = Number(route?.query?.record) || null;
      if (requested && !response.items.some(i => i.obligation_id === requested)) {
        try { response.items.push(await api(`/api/sequences/${requested}`)); }
        catch (e) { error.textContent = e.message; selected = null; }
        if (!alive) return;
      }
      const signature = JSON.stringify(response.items);
      const changed = signature !== JSON.stringify(items); items = response.items;
      stats.textContent = `${response.summary.attention} later-photo candidates to review · ${response.summary.ready} reviewed sequences · ${response.summary.saved} saved drafts. New captures update automatically.`;
      error.textContent = response.summary.error || "";
      if (!selected && items.length) selected = (items.find(attention) || items[0]).obligation_id;
      renderList(); if (changed || force || !detail.childNodes.length) renderDetail();
    } catch (e) { if (alive) error.textContent = `${e.message} Use Refresh to retry.`; }
  }
  load(); timer = setInterval(() => { if (!detail.contains(document.activeElement)) load(); }, 30000);
  return { update() {}, destroy() { alive = false; clearInterval(timer); } };
}
