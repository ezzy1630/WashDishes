// Periodic detection checks are separate from incident responsibility reviews.
import { fill, h, api, emit, now, state, clock, toast } from '../core.js';
export function reviewPage(main) {
  const checkEl = h('section', {class: 'card kitchen-check', hidden: true, 'aria-label': 'Kitchen check'});
  const empty = h('p', {class: 'tx2'}, 'No recent kitchen check is waiting. Incident evidence is reviewed from the Incidents page.');
  let checkId = null, rejected = new Set(), missed = 0;
  fill(main, h('div', {class: 'page'}, h('header', {class: 'page-head'}, h('div', {}, h('h1', {id: 'page-title', tabindex: '-1'}, 'Kitchen checks'), h('p', {}, 'Help measure missed dishes and false detections.'))), checkEl, empty, h('a', {class: 'btn', href: '#/feed'}, 'Review incidents')));
  function renderCheck() {
    const c = (state.data?.checks || []).find(x => x.status === "pending" && now() - Date.parse(x.at) < 36 * 3600e3);
    checkEl.hidden = !c;
    if (!c) { checkId = null; empty.hidden = false; return; }
    empty.hidden = true;
    if (c.id !== checkId) { checkId = c.id; rejected = new Set(); missed = 0; }
    const out = c.dishes.length - rejected.size + missed;
    const pct = v => `${(v * 100).toFixed(2)}%`, pad = .008;
    const marks = c.dishes.map((d, i) => h("button", { type: "button", class: `dish-mark numbered ${rejected.has(i) ? "rejected" : ""}`, "aria-pressed": String(!rejected.has(i)),
      "aria-label": `${i + 1}: ${d.kind} on the ${d.area.toLowerCase()}${rejected.has(i) ? ", marked not a dish" : ""}`,
      style: { left: pct(d.box[0] - pad), top: pct(d.box[1] - pad), width: pct(d.box[2] + 2 * pad), height: pct(d.box[3] + 2 * pad) },
      onclick: () => { rejected.has(i) ? rejected.delete(i) : rejected.add(i); renderCheck(); } }, h("span", {}, String(i + 1))));
    fill(checkEl,
      h("div", { class: "check-head" },
        h("div", {}, h("h2", {}, `${c.label} kitchen check: ${out} ${out === 1 ? "dish" : "dishes"} out`),
          h("p", { class: "help" }, c.dishes.length ? "Tap any outline that isn't a dish. Mark only what you can see. A missing dish does not establish washing." : "The camera found nothing out. Add any it missed.")),
        h("time", {}, clock(c.at, true))),
      c.photo_url ? h("span", { class: "marked check-photo" }, h("img", { src: c.photo_url, alt: "The kitchen at the check" }), ...marks) : null,
      h("div", { class: "check-actions" },
        h("span", { class: "stepper" }, h("span", {}, "Missed any?"),
          h("button", { type: "button", class: "btn quiet", "aria-label": "One fewer missed", disabled: !missed, onclick: () => { missed--; renderCheck(); } }, "−"),
          h("output", {}, String(missed)),
          h("button", { type: "button", class: "btn quiet", "aria-label": "One more missed", onclick: () => { missed = Math.min(50, missed + 1); renderCheck(); } }, "+")),
        h("button", { type: "button", class: "btn primary", onclick: async e => {
          e.currentTarget.disabled = true;
          try {
            await api(`/api/checks/${c.id}`, { method: "POST", body: { verdicts: c.dishes.map((_, i) => !rejected.has(i)), missed } });
            toast("Kitchen check saved.");
            emit("changed");
          } catch (error) { toast(error.message, { error: true }); e.currentTarget.disabled = false; }
        } }, rejected.size || missed ? "Save" : "Looks right")));
  }

  renderCheck();
  return {update(kind) { if (kind === 'data') renderCheck(); }, destroy() {}};
}
