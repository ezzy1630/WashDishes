// Dedicated live workspace: current inventory, earlier activity, and exact-frame corrections.
import { api, emit, fill, h, head, icon, state, when, clock, empty, toast } from "../core.js";
import { LiveView, detKey } from "../live.js";
import { openIncident } from "../incident.js";

export function livePage(main, route) {
  let alive = true, selected = null, job = null, jobs = [], pending = false, capture = null, signature = "", initialDish = route.query?.dish || null;
  const status = h("p", { class: "cam-state" }, "Connecting");
  const list = h("div", { class: "live-dishes" });
  const tools = h("div", { class: "live-tools" });
  const results = h("div", { class: "trace-results" });
  const searches = h("div", { class: "trace-searches" });
  const errors = h("p", { class: "form-error", role: "alert" });
  const progress = h('p', {class: 'help', role: 'status', 'aria-live': 'polite'});
  const area = h("select", { "aria-label": "Area to investigate" });
  const hours = h("select", { "aria-label": "Search period" }, [[2, "Last 2 hours"], [6, "Last 6 hours"], [12, "Last 12 hours"], [24, "Last 24 hours"]].map(([v, t]) => h("option", { value: v, selected: v === 12 }, t)));
  const purpose = h("select", { "aria-label": "Correction use" }, h("option", { value: "training" }, "Use for training"), h("option", { value: "evaluation" }, "Reserve for testing"));
  const live = new LiveView({ onSelect: (key, d) => { if (d && d.kind !== "person") choose(d); }, onUpdate: next => updateLive(next) });
  const layers = h("div", { class: "chips", "aria-label": "Camera layers" }, [["zones", "Areas"], ["dishes", "Dishes"], ["people", "People"]].map(([key, label]) => h("button", {
    type: "button", "aria-pressed": String(live.layers[key]), onclick: e => { const on = e.currentTarget.getAttribute("aria-pressed") !== "true"; e.currentTarget.setAttribute("aria-pressed", String(on)); live.setLayer(key, on); }
  }, label)));
  const quality = h("select", { "aria-label": "Video quality", onchange: e => live.setQuality(e.target.value) }, h("option", { value: "hd", selected: live.quality === "hd" }, "HD"), h("option", { value: "sd", selected: live.quality === "sd" }, "SD"));
  const freeze = h("button", { type: "button", class: "btn primary", disabled: true, onclick: correct }, icon("camera", 15), "Freeze & correct");
  const find = h("button", { type: "button", class: "btn", disabled: true, onclick: search }, icon("search", 15), "Find earlier use");
  fill(tools, h("div", { class: "row-actions" }, purpose, freeze), h("p", { class: "help" }, "Freeze one exact picture. Add missed dishes, remove false outlines, and check every box. Corrections do not change anyone’s dish record."));
  fill(main, h("div", { class: "page live-workspace" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", { id: "page-title", tabindex: "-1" }, "Dishes out now"), h("p", {}, "Choose a dish or area, then search the saved video to see who used it."))),
    h("div", { class: "live-work-grid" },
      h("section", { class: "card cam" }, head("Kitchen", status, h("div", { class: "row-actions" }, layers, quality)), live.el, h('details', {class: 'live-corrections'}, h('summary', {}, 'Correct a missed dish or a wrong box'), tools)),
      h("section", { class: "card" }, head("Dishes out now", "Tracked on counters, stove, or in the sink"), list,
        h("div", { class: "card-body stack" }, h("h3", {}, "Find who used it"), h("div", { class: "trace-controls" }, area, hours), find, progress,
          h("p", { class: "help" }, "Search shows earlier photos and clips. Compare the same dish before assigning responsibility."), errors))),
    h("section", { class: "trace-section" }, h("header", { class: "page-head" }, h("div", {}, h("h2", {}, "Earlier activity"), h("p", {}, "Search results stay here. Open a photo or play a clip to check the dish and the person handling it."))), searches, results),
    h("section", { class: "card card-body" }, h("div", { class: "evaluation-status" }))));
  area.addEventListener("change", () => { selected = null; live.focus(null); renderDishes(); });
  const pct = n => n == null ? "—" : `${Math.round(n * 100)}%`;

  function updateLive(next) {
    state.live = next ? { ...next, video: live.video.ok, mode: live.mode } : null; emit("live");
    const usable = Boolean(next?.fresh && next.live && !live.error);
    status.textContent = usable ? `Live${next.live?.fps ? ` · ${next.live.fps.toFixed(0)} fps` : ""}` : live.mode === "demo" ? "Demo · no live camera" : live.mode === "lost" ? "Reconnecting" : "Waiting for camera";
    freeze.disabled = find.disabled = pending || !usable;
    const areas = next?.areas || [];
    const sig = JSON.stringify(areas.map(a => [a.id, a.name, a.role]));
    if (area.dataset.signature !== sig) {
      const old = area.value; fill(area, areas.filter(a => ["dirty", "sink"].includes(a.role)).map(a => h("option", { value: a.id }, a.name))); area.dataset.signature = sig;
      const wanted = old || route.query?.area;
      if ([...area.options].some(o => o.value === wanted)) area.value = wanted;
    }
    if (initialDish) {
      const dish = next?.live?.detections.find(d => d.scene_key === initialDish);
      if (dish) { initialDish = null; choose(dish); }
    }
    renderDishes();
  }

  function choose(d) {
    selected = d;
    if (d.area_id) area.value = d.area_id;
    live.focus(detKey(d)); signature = ""; renderDishes();
  }

  function renderDishes() {
    const dishes = (live.data?.live?.detections || []).filter(d => d.kind !== "person" && d.counted && ["dirty", "sink"].includes(d.role));
    if (selected) selected = dishes.find(d => detKey(d) === detKey(selected)) || null;
    const sig = JSON.stringify([dishes.map(d => [detKey(d), d.kind, d.area_id, d.obligation_id]), selected && detKey(selected), area.value]);
    if (signature === sig) return; signature = sig;
    fill(list, dishes.length ? dishes.map(d => {
      const name = live.data.areas.find(a => a.id === d.area_id)?.name || d.area_id;
      return h("button", { type: "button", class: "live-dish", "aria-pressed": String(Boolean(selected && detKey(selected) === detKey(d))), onclick: () => choose(d) },
        h("span", {}, h("b", {}, d.kind[0].toUpperCase() + d.kind.slice(1)), h("small", {}, name)), h("span", { class: "tx3" }, d.obligation_id ? `Record #${d.obligation_id}` : "Owner unknown · search video"));
    }) : h("p", { class: "quiet-note" }, "No dishes currently tracked. If the picture shows a missed dish, use Freeze & correct."));
    find.textContent = pending ? 'Starting search…' : selected ? `Search video for this ${selected.kind}` : `Search ${area.selectedOptions[0]?.textContent || 'selected area'}`;
  }

  async function freezeFrame(use) {
    const id = live.data?.live?.frame_id;
    if (!id) throw new Error("Wait for a fresh camera frame before capturing.");
    return api("/api/dataset/capture", { method: "POST", body: { live_frame_id: id, purpose: use } });
  }

  async function correct() {
    if (pending) return;
    pending = true; freeze.disabled = find.disabled = true; errors.textContent = "";
    try { const item = await freezeFrame(purpose.value); if (alive) location.hash = `#/model?frame=${item.id}`; }
    catch (e) { errors.textContent = e.message; }
    finally { pending = false; if (alive) updateLive(live.data); }
  }

  async function search() {
    if (pending) return;
    pending = true; freeze.disabled = find.disabled = true; errors.textContent = "";
    // Keep the selection across the await: live detections continue updating.
    const target = { area_id: area.value, obligation_id: selected?.obligation_id || null };
    find.textContent = 'Starting search…';
    progress.textContent = 'Saving the current view and starting the footage search…';
    results.setAttribute('aria-busy', 'true');
    try {
      capture = await freezeFrame("reference");
      const next = await api("/api/traces", { method: "POST", body: { frame_id: capture.id, ...target, hours: Number(hours.value) } });
      if (!alive) return;
      job = next; history.replaceState(null, "", `#/live?search=${job.id}`); await loadSearches(); renderSearch();
      results.scrollIntoView({ behavior: "auto", block: "start" });
    } catch (e) { errors.textContent = e.message; progress.textContent = 'Search did not start. Check the message below and try again.'; }
    finally { pending = false; results.setAttribute('aria-busy', 'false'); signature = ''; if (alive) updateLive(live.data); }
  }

  async function loadSearches() {
    try {
      const data = await api("/api/traces"); if (!alive) return; jobs = data.searches;
      const previous = job;
      if (job) job = jobs.find(j => j.id === job.id) || job;
      else job = jobs.find(j => j.id === route.query?.search)
        || jobs.find(j => !j.automatic && (!route.query?.area || j.area.id === route.query.area)) || null;
      if (!previous && job && !route.query?.area && !selected) { area.value = job.area.id; renderDishes(); }
      fill(searches, jobs.filter(j => !j.automatic || j.id === job?.id).map(j => h("button", { type: "button", class: "btn sm", "aria-pressed": String(j.id === job?.id), onclick: () => { job = j; selected = null; area.value = j.area.id; live.focus(null); renderDishes(); history.replaceState(null, "", `#/live?search=${j.id}`); loadSearches().then(renderSearch); renderSearch(); } }, `${j.area.name} · ${clock(j.created_at)} · ${j.status === 'complete' ? `${j.candidates.length} results` : j.status}`)));
    } catch (e) { errors.textContent = e.message; }
  }

  function renderSearch() {
    if (!job) { fill(results, empty("No search selected", "Choose a current dish or area above, then search the retained recordings.")); return; }
    const j = job, cov = j.coverage, base = `/api/traces/${j.id}`;
    fill(progress, j.status === 'complete' ? `${j.area.name}: ${j.candidates.length} activity windows found. ` : `${j.area.name}: ${j.message} `,
      h('button', {type: 'button', class: 'link', onclick: () => results.scrollIntoView({behavior: 'auto', block: 'start'})}, 'View results ↓'));
    fill(results,
      h("div", { class: "trace-intro" }, h("div", {}, h("h3", {}, j.area.name), h("p", { role: "status" }, j.message),
        cov.from ? h("p", { class: "help" }, `${cov.sampled} samples · recordings ${when(cov.from)} to ${when(cov.to)}${cov.unreadable ? ` · ${cov.unreadable} unreadable` : ""}. Recording times are estimates; coverage has gaps. Camera motion, lighting and people can also cause changes.`) : null,
        j.obligation_id ? h("button", { type: "button", class: "link", onclick: () => openIncident(j.obligation_id) }, `Open dish record #${j.obligation_id}`) : null),
        h("figure", { class: "trace-anchor" }, h("img", { src: `${base}/pictures/0/anchor`, alt: "Frozen current kitchen view used for this search" }), h("figcaption", {}, "Current view · search anchor"))),
      h("div", { class: "trace-candidates" }, j.candidates.map(c => {
        const video = h("div", { class: "trace-video" });
        const preview = h("button", { type: "button", class: "btn sm", onclick: () => {
          fill(video, h("video", { src: `${base}/clips/${c.index}`, controls: true, playsInline: true, preload: "metadata", onerror: () => fill(video, h("p", { class: "form-error" }, "The clip could not be recovered; its rolling footage may have expired. The before/after snapshots remain.")) }));
        } }, icon("play", 14), "Review clip");
        const keep = h("button", { type: "button", class: "btn sm", disabled: c.kept || !j.obligation_id, onclick: async () => {
          keep.disabled = true;
          try { const next = await api(`${base}/keep`, { method: "POST", body: { candidate: c.index } }); if (!alive) return; if (job?.id === j.id) job = next; emit("changed"); toast("Candidate evidence saved. Owner and timer unchanged."); renderSearch(); }
          catch (e) { fill(video, h("p", { class: "form-error" }, e.message)); keep.disabled = false; }
        } }, c.kept ? "Saved to record" : "Keep candidate");
        return h("article", { class: "trace-candidate" },
          h("header", {}, h("h3", {}, `${clock(c.from)}–${clock(c.to)} · possible activity`), h("p", { class: "help" }, "Approximate recording times. This is not a confirmed match to the current dish.")),
          h("div", { class: "trace-pair" }, ["before", "after"].map(side => h("figure", {}, h("a", { href: `${base}/pictures/${c.index}/${side}`, target: "_blank", rel: "noopener" }, h("img", { src: `${base}/pictures/${c.index}/${side}`, alt: `${side} candidate activity in ${j.area.name}`, loading: "lazy" })), h("figcaption", {}, side === "before" ? "Before sampled change" : "After sampled change")))),
          h("div", { class: "row-actions" }, preview, j.obligation_id ? keep : null), video);
      })));
  }

  async function evaluation() {
    try {
      const d = await api("/api/dataset"); if (!alive) return; const e = d.evaluation;
      fill(main.querySelector(".evaluation-status"), h("h2", {}, "Detection checks"), h("p", {}, `${d.corrections || 0} saved ${d.corrections === 1 ? "correction" : "corrections"} · ${e.frames} completed test ${e.frames === 1 ? "picture" : "pictures"}`),
        e.frames ? h("p", { class: "tx2" }, `${e.missed} missed ${e.missed === 1 ? "dish" : "dishes"} · ${e.extra} extra ${e.extra === 1 ? "outline" : "outlines"} · precision ${pct(e.precision)} · recall ${pct(e.recall)}`) : null,
        h("p", { class: "help" }, "Test pictures score the original frozen detector predictions against your corrected boxes. Their surrounding 30-minute windows stay out of training. These checks do not measure ownership or washing."),
        h("a", { class: "link", href: "#/model" }, "Open saved pictures", icon("arrowRight", 14)));
    } catch (e) { errors.textContent = e.message; }
  }

  live.start(); loadSearches().then(renderSearch); evaluation();
  const poll = setInterval(async () => {
    if (!alive || document.hidden) return;
    if (!job) { await loadSearches(); if (alive) renderSearch(); return; }
    if (!["queued", "running"].includes(job.status)) return;
    const id = job.id;
    try { const next = await api(`/api/traces/${id}`); if (alive && job?.id === id) { job = next; renderSearch(); if (!["queued", "running"].includes(next.status)) loadSearches(); } }
    catch (e) { errors.textContent = e.message; }
  }, 2000);
  return { update(kind) { if (kind === "data") evaluation(); }, destroy() { alive = false; clearInterval(poll); live.stop(); state.live = null; emit("live"); } };
}
