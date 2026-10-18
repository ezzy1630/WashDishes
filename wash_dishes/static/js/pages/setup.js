// Settings: the camera link, the areas it watches, the rules, detection and storage.
import { fill, h, icon, api, state, emit, toast, field, form, bytes, busy, safeURL, head, ago, pct } from "../core.js";
import { ROLE_COLORS, viewState } from "../live.js";

const SECTIONS = [["camera", "Camera"], ["areas", "Counter areas"], ["rules", "Rules"], ["alerts", "Owners and alerts"], ["detection", "Detection"], ["storage", "Storage"]];
const ROLES = { dirty: "Counter · dishes get left here", sink: "Sink", clean: "Rack · clean dishes", storage: "Cabinet · put away" };
const ROLE_SHORT = { dirty: "counter", sink: "sink", clean: "rack", storage: "cabinet" };

export function setupPage(main) {
  const demo = state.data?.mode === "demo";
  const sections = demo ? [...SECTIONS, ["demo", "Demo"]] : SECTIONS;
  const nav = h("nav", { class: "subnav", "aria-label": "Settings sections" }, sections.map(([id, label]) => h("a", { href: `#/setup/${id}`, "data-id": id, onclick: e => { e.preventDefault(); document.getElementById(`s-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); history.replaceState(null, "", `#/setup/${id}`); } }, label)),
    h("a", { href: "#/model", class: "subnav-out" }, "Train the detector", icon("arrowRight", 13)));
  const camStatus = h("div");
  const storageBox = h("div");
  const content = h("div", { class: "col" },
    section("camera", "Camera", "The kitchen camera on your local network. Its password never leaves the Jetson.", camStatus, h("div", { class: "card-body" }, cameraForm())),
    section("areas", "Counter areas", "Where dishes get left, washed and put away, drawn on today's picture.", areaEditor()),
    section("rules", "Rules", "How long a dish can sit, and how long photos and footage are kept.", h("div", { class: "card-body" }, rulesForm())),
    section("alerts", "Owners and alerts", "When the camera may name owners, and where late dishes are announced.", h("div", { class: "card-body" }, alertsForm())),
    section("detection", "Detection", "Spotting dishes and people. It runs on the Jetson; nothing leaves the house.", h("div", { class: "card-body" }, detectionForm())),
    section("storage", "Storage", "Footage rotates out on its own. The record of who left what is kept.", storageBox),
    demo ? section("demo", "Demo", "Add a made-up event to see how the app reacts.", h("div", { class: "card-body" }, demoTools())) : null);
  fill(main, h("div", { class: "page" }, h("header", { class: "page-head" }, h("div", {}, h("h1", { id: "page-title", tabindex: "-1" }, "Settings"), h("p", {}, "The camera, what it watches, and the house rules."))), h("div", { class: "settings-grid" }, nav, content)));

  const spy = new IntersectionObserver(entries => { for (const e of entries) if (e.isIntersecting) for (const a of nav.children) a.classList.toggle("on", a.dataset.id === e.target.dataset.id); }, { rootMargin: "-20% 0px -70% 0px" });
  for (const s of content.querySelectorAll(".setup-sec")) spy.observe(s);

  function section(id, title, sub, ...children) {
    return h("section", { class: "setup-sec card", id: `s-${id}`, "data-id": id }, head(title, sub), ...children);
  }

  function renderStatus() {
    const cam = state.data?.camera || {}, v = cam.view, vs = viewState(cam), set = state.data?.settings || {};
    const pause = h("button", { type: "button", class: "btn sm", disabled: !cam.configured || demo, onclick: e => busy(e.currentTarget, async () => { await api("/api/camera/pause", { method: "POST", body: { paused: cam.enabled } }); toast(cam.enabled ? "Camera paused." : "Camera resumed."); emit("changed"); }) }, icon(cam.enabled ? "pause" : "play", 14), h("span", {}, cam.enabled ? "Pause" : "Resume"));
    const confirm = h("button", { type: "button", class: "btn sm", disabled: demo || !cam.connected, onclick: e => busy(e.currentTarget, async () => { await api("/api/view/confirm", { method: "POST", body: {} }); toast("This view is now the reference for the areas."); emit("changed"); }) }, icon("target", 14), h("span", {}, "Use this view as the reference"));
    const line = (k, value, tone = "") => h("div", {}, h("dt", {}, k), h("dd", { class: tone }, value));
    fill(camStatus, h("div", { class: "card-body cam-status" },
      h("dl", { class: "facts" },
        line("Connection", cam.connected ? (cam.enabled ? "Connected" : "Paused") : cam.configured ? "Offline" : "Not set up", cam.connected ? (cam.enabled ? "ok" : "out") : cam.configured ? "late" : ""),
        line("Last picture", cam.latest_frame_at ? ago(cam.latest_frame_at) : "—"),
        line("Position", v?.state === "moved" ? "Moved too far. New incidents are paused." : vs ? vs.text : v?.has_reference ? "Where it should be" : "No reference view yet", v?.state === "moved" ? "late" : vs ? "out" : ""),
        line("Analysis", set.vision_enabled ? `${set.analysis_fps ?? "—"} pictures a second` : "Off", set.vision_enabled ? "" : "out")),
      (cam.vision_status || cam.message) && v?.state !== "moved" ? h("p", { class: "help" }, cam.vision_status || cam.message) : null,
      v?.state === "moved" ? h("p", { class: "callout late" }, icon("alert", 15), h("span", {}, "Put the camera back, or confirm this view and redraw the counter areas.")) : null,
      h("div", { class: "row-actions" }, pause, confirm)));
  }

  function cameraForm() {
    const f = form(async fd => {
      await api("/api/camera", { method: "POST", body: { host: String(fd.get("host") || "").trim(), username: String(fd.get("username") || ""), password: String(fd.get("password") || ""), stream: fd.get("stream"), enabled: fd.has("enabled") } });
      f.querySelector('[name="password"]').value = ""; toast("Camera connection saved."); emit("changed");
    }, { submit: "Save connection" });
    f.append(h("div", { class: "form-grid three" },
      field("Address", h("input", { name: "host", required: true, maxlength: 253, placeholder: "192.168.1.80", autocomplete: "off" }), "Local IP or hostname."),
      field("Camera account", h("input", { name: "username", required: true, maxlength: 128, autocomplete: "off" }), "Tapo → Advanced → Camera Account."),
      field("Password", h("input", { name: "password", type: "password", required: true, maxlength: 256, autocomplete: "new-password" }))),
      h("div", { class: "form-grid" }, field("Stream", h("select", { name: "stream" }, h("option", { value: "stream1" }, "stream1 · main, full resolution"), h("option", { value: "stream2" }, "stream2 · sub, lower bandwidth"))),
        check("enabled", "Monitor the kitchen", state.data?.camera?.configured ? state.data.camera.enabled : true, "Pausing stops new observations. Past incidents stay.")));
    f.finish();
    if (demo) { for (const el of f.querySelectorAll("input,select,button")) el.disabled = true; f.prepend(h("p", { class: "callout" }, icon("info", 15), h("span", {}, "The demo can't connect a camera. Start it in live mode to set one up."))); }
    return f;
  }

  function rulesForm() {
    const s = state.data?.settings || {};
    const f = form(async fd => {
      const body = {}; for (const k of ["deadline_minutes", "buffer_hours", "evidence_days", "review_days", "photo_days", "storage_limit_gb"]) body[k] = Number(fd.get(k));
      await api("/api/settings", { method: "PATCH", body }); toast("Rules saved."); emit("changed");
    }, { submit: "Save rules" });
    const num = (label, key, min, max, help, step = "1") => field(label, h("input", { type: "number", name: key, value: s[key], min, max, step, required: true }), help);
    f.append(h("div", { class: "form-grid" }, num("Grace period (minutes)", "deadline_minutes", 1, 1440, "How long a dish can sit before it counts as late. Applies to new dishes."), num("Storage limit (GB)", "storage_limit_gb", 0.1, 2000, "Cap for camera footage and evidence.", "0.1")),
      h("div", { class: "form-grid four" }, num("Rolling footage (hours)", "buffer_hours", 0.1, 48, "", "0.1"), num("Keep clips (days)", "evidence_days", 1, 90), num("Keep photos (days)", "photo_days", 1, 730), num("Keep disputed (days)", "review_days", 1, 365)));
    f.finish(); return f;
  }

  function alertsForm() {
    const s = state.data?.settings || {};
    const f = form(async fd => {
      await api("/api/settings", { method: "PATCH", body: { auto_attribute: fd.has("auto_attribute"), notify_url: String(fd.get("notify_url") || "").trim(), dashboard_url: String(fd.get("dashboard_url") || "").trim() } });
      toast("Saved."); emit("changed");
    }, { submit: "Save" });
    const test = h("button", { type: "button", class: "btn", onclick: e => busy(e.currentTarget, async () => { await api("/api/notify/test", { method: "POST", body: {} }); toast("Test alert sent."); }) }, h("span", {}, "Send a test"));
    f.append(check("auto_attribute", "Let the camera name owners once it's accurate", s.auto_attribute, "Turns on by itself after 30 checked guesses at 90% or better, and off again if accuracy drops. Named owners can be disputed."),
      field("ntfy topic", h("input", { type: "url", name: "notify_url", value: s.notify_url || "", placeholder: "https://ntfy.sh/a-long-private-topic", autocomplete: "off" }),
        "Late dishes and a Sunday-evening summary go here, with names and times but no photos. Anyone who knows the topic can read it, so make it long and unguessable, or run your own ntfy server."));
    f.append(field("Household dashboard address", h("input", {type: "url", name: "dashboard_url", value: s.dashboard_url || "", placeholder: "https://your-household-server"}), "Alerts link directly to the private four-photo incident. Photos stay behind household sign-in."));
    f.finish(test); return f;
  }

  function detectionForm() {
    const s = state.data?.settings || {};
    const f = form(async fd => {
      await api("/api/settings", { method: "PATCH", body: { vision_enabled: fd.has("vision_enabled"), detector_path: String(fd.get("detector_path") || "").trim(), analysis_fps: Number(fd.get("analysis_fps")), dataset_enabled: fd.has("dataset_enabled"), dataset_limit_gb: Number(fd.get("dataset_limit_gb")) } });
      toast("Detection settings saved."); emit("changed");
    }, { submit: "Save detection" });
    f.append(check("vision_enabled", "Detect dishes and people", s.vision_enabled, "The camera spots dishes left out and suggests whose they are."),
      h("div", { class: "form-grid three" },
        field("Detector", h("input", { name: "detector_path", value: s.detector_path || "", placeholder: "built-in prepared model", autocomplete: "off" }), "Path to a trusted .pt or .engine file on the Jetson."),
        field("Pictures analysed per second", h("input", { type: "number", name: "analysis_fps", value: s.analysis_fps, min: 1, max: 10, required: true }), "Higher follows people better."),
        field("Training frames limit (GB)", h("input", { type: "number", name: "dataset_limit_gb", value: s.dataset_limit_gb, min: 0, max: 100, step: 0.5, required: true }), "Oldest unlabelled frames go first.")),
      check("dataset_enabled", "Save frames for training", s.dataset_enabled, "Keeps a picture whenever the counters change, for training."));
    f.finish(); return f;
  }

  function renderStorage() {
    const st = state.data?.storage || {}, used = Number(st.used_bytes || 0), limit = Number(st.limit_bytes || 1), ds = state.data?.dataset;
    const seg = (cls, v) => h("i", { class: cls, style: { width: `${Math.min(100, Number(v || 0) / limit * 100)}%` } });
    fill(storageBox, h("div", { class: "card-body stack" },
      h("p", { class: "storage-line" }, h("b", {}, bytes(used)), ` of ${bytes(limit)} used (${pct(used / limit)}) · ${bytes(st.free_bytes)} free on the disk`),
      h("div", { class: "storage-bar", role: "img", "aria-label": `${bytes(used)} of ${bytes(limit)} used` }, seg("buffer", st.buffer_bytes), seg("evidence", st.evidence_bytes)),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "sw buffer" }), `Rolling footage ${bytes(st.buffer_bytes)}`), h("span", {}, h("i", { class: "sw evidence" }), `Photos and clips ${bytes(st.evidence_bytes)}`), ds ? h("span", {}, `Training frames ${bytes(ds.used_bytes)} of ${bytes(ds.limit_bytes)}`) : null),
      h("div", { class: "row-actions" }, h("button", { type: "button", class: "btn sm", onclick: e => busy(e.currentTarget, async () => { await api("/api/storage/cleanup", { method: "POST", body: {} }); toast("Expired media removed."); emit("changed"); }) }, icon("trash", 14), h("span", {}, "Remove expired media now")))));
  }

  function demoTools() {
    return h("div", { class: "row-actions" }, [["deposit", "Someone leaves a dish"], ["overdue", "A dish goes past grace"], ["unknown", "Owner unknown"], ["cleanup", "A dish gets washed"]].map(([k, label]) =>
      h("button", { type: "button", class: "btn sm", onclick: e => busy(e.currentTarget, async () => { await api("/api/demo/scenario", { method: "POST", body: { scenario: k } }); toast("Added."); emit("changed"); }) }, icon("plus", 14), h("span", {}, label))));
  }

  // ---- Area editor: rectangles over today's snapshot, shown where they sit after any camera shift.
  function areaEditor() {
    const settings = state.data?.settings || {};
    const [ox, oy] = state.data?.camera?.view?.offset || [0, 0];
    const clamp = v => Math.min(1, Math.max(0, v));
    const follow = b => [Math.min(Math.max(0, b[0] + ox), 1 - b[2]), Math.min(Math.max(0, b[1] + oy), 1 - b[3]), b[2], b[3]];
    const saved = () => (settings.areas?.length ? structuredClone(settings.areas) : Object.entries(settings.zones || {}).map(([role, box]) => ({ id: role, name: role === "dirty" ? "Counter" : role === "sink" ? "Sink" : "Rack", role, box: [...box] }))).map(a => ({ ...a, box: follow(a.box), ...(a.outline ? { outline: a.outline.map(([x, y]) => [clamp(x + ox), clamp(y + oy)]) } : {}) }));
    const refs = new Set((state.live?.areas || []).filter(a => a.reference).map(a => a.id));
    let areas = saved(), current = areas[0]?.id, drag = null;
    const sel = () => areas.find(a => a.id === current);
    const img = h("img", { alt: "Latest kitchen snapshot for drawing areas", draggable: "false" });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 1000 1000", preserveAspectRatio: "none", class: "zone-svg" })) svg.setAttribute(k, v);
    const labels = h("div", { class: "zone-labels" });
    const placeholder = h("div", { class: "feed-empty" }, h("strong", {}, demo ? "No camera in the demo" : "No picture yet"), h("span", {}, demo ? "Areas can be drawn once a camera is connected." : "Connect the camera to draw on today's picture."));
    const stage = h("div", { class: "zone-stage" }, img, svg, labels, placeholder);
    const loadSnap = () => {
      const url = state.data?.camera?.connected && !demo && safeURL(state.data.camera.snapshot_url);
      placeholder.hidden = Boolean(url); img.hidden = !url;
      if (url) { img.src = `${url}?t=${Date.now()}`; img.onload = () => { stage.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`; }; img.onerror = () => { img.hidden = true; placeholder.hidden = false; }; }
    };
    const listEl = h("div", { class: "area-list", role: "listbox", "aria-label": "Areas" });
    const nameIn = h("input", { maxlength: 60, required: true, "aria-label": "Area name", oninput: () => { sel().name = nameIn.value; renderList(); draw(); } });
    const roleSel = h("select", { "aria-label": "Purpose", onchange: () => { sel().role = roleSel.value; renderList(); draw(); } }, Object.entries(ROLES).map(([k, v]) => h("option", { value: k }, v)));
    const coords = ["x", "y", "w", "h"].map((label, i) => h("input", { type: "number", min: 0, max: 1, step: 0.001, "aria-label": label, oninput: e => { const v = Number(e.target.value); if (e.target.value !== "" && Number.isFinite(v)) { sel().box[i] = v; delete sel().outline; draw(); renderList(); } } }));
    const error = h("p", { class: "form-error", role: "alert" });
    const moved = state.data?.camera?.view?.state === "moved";

    function draw() {
      fill(svg); fill(labels);
      for (const a of areas) {
        const [x, y, w, hh] = a.box.map(v => v * 1000), on = a.id === current, c = ROLE_COLORS[a.role];
        const r = document.createElementNS(svg.namespaceURI, "rect");
        for (const [k, v] of Object.entries({ x, y, width: w, height: hh, fill: c, "fill-opacity": on ? .18 : .05, stroke: on ? "#fff" : c, "stroke-opacity": on ? .95 : .7, "stroke-width": on ? 2 : 1, "stroke-dasharray": on ? "" : "4 3", "vector-effect": "non-scaling-stroke", "data-id": a.id })) r.setAttribute(k, v);
        svg.append(r);
        labels.append(h("span", { class: `zone-label ${on ? "on" : ""}`, style: { left: `${a.box[0] * 100}%`, top: `${a.box[1] * 100}%` } }, a.name));
      }
      const a = sel(); if (!a) return;
      coords.forEach((input, i) => { if (document.activeElement !== input) input.value = String(Math.round(a.box[i] * 1000) / 1000); });
      if (document.activeElement !== nameIn) nameIn.value = a.name; roleSel.value = a.role;
    }
    function renderList() {
      fill(listEl, areas.map(a => h("button", { type: "button", role: "option", "aria-selected": String(a.id === current), onclick: () => { current = a.id; renderList(); draw(); } },
        h("i", { class: "sw", style: { background: ROLE_COLORS[a.role] } }), h("span", { class: "area-name" }, a.name), h("span", { class: "tx3" }, ROLE_SHORT[a.role]),
        refs.has(a.id) ? h("span", { class: "ok", title: "Empty photo saved, so fullness can be measured" }, icon("check", 13)) : h("span"))));
    }
    const pos = e => { const r = svg.getBoundingClientRect(); return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))]; };
    svg.addEventListener("pointerdown", e => {
      if (img.hidden || !sel()) return;
      const hit = e.target.closest?.("[data-id]");
      if (hit && hit.dataset.id !== current) { current = hit.dataset.id; renderList(); draw(); return; }
      e.preventDefault(); drag = { start: pos(e), original: [...sel().box], outline: sel().outline }; svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener("pointermove", e => { if (!drag) return; const p = pos(e), [x, y] = drag.start; sel().box = [Math.min(x, p[0]), Math.min(y, p[1]), Math.abs(p[0] - x), Math.abs(p[1] - y)]; delete sel().outline; draw(); });
    const end = e => { if (!drag) return; const a = sel(); if (e.type === "pointercancel" || a.box[2] < 0.02 || a.box[3] < 0.02) { a.box = drag.original; if (drag.outline) a.outline = drag.outline; } drag = null; draw(); renderList(); };
    svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);

    const add = () => { if (areas.length >= 24) return toast("Up to 24 areas.", { error: true }); const id = `area_${Date.now().toString(36)}`; areas.push({ id, name: "New counter", role: "dirty", box: [0.1, 0.1, 0.2, 0.2] }); current = id; renderList(); draw(); nameIn.focus(); nameIn.select(); };
    const remove = () => { if (areas.length <= 1) return toast("Keep at least one area.", { error: true }); areas = areas.filter(a => a.id !== current); current = areas[0].id; renderList(); draw(); };
    const save = async e => {
      error.textContent = "";
      if (areas.some(a => !a.name.trim() || a.box.some(v => !Number.isFinite(v) || v < 0 || v > 1) || a.box[2] < 0.02 || a.box[3] < 0.02 || a.box[0] + a.box[2] > 1.000001 || a.box[1] + a.box[3] > 1.000001)) { error.textContent = "Name every area and keep each rectangle inside the picture (at least 0.02 wide and tall)."; return; }
      await busy(e.currentTarget, async () => { await api("/api/settings", { method: "PATCH", body: { areas } }); toast("Areas saved. This picture is now the reference view."); emit("changed"); });
    };
    const empty = e => {
      const a = sel();
      if (!settings.areas?.some(s => s.id === a.id)) { error.textContent = "Save the areas first, then capture the empty reference."; return; }
      busy(e.currentTarget, async () => { await api(`/api/areas/${encodeURIComponent(a.id)}/reference`, { method: "POST", body: {} }); refs.add(a.id); renderList(); toast(`Empty reference saved for ${a.name}.`); });
    };
    renderList(); draw(); loadSnap();
    // Only /api/live reports which areas have an empty reference captured.
    api("/api/live").then(d => { for (const x of d.areas || []) if (x.reference) refs.add(x.id); if (listEl.isConnected) renderList(); }).catch(() => {});
    return h("div", { class: "card-body" },
      moved ? h("p", { class: "callout late" }, icon("alert", 15), h("span", {}, "The camera moved too far to follow and new incidents are paused. Put it back, or redraw the areas on this picture and save. This picture then becomes the reference.")) : null,
      h("div", { class: "zone-grid" },
        h("div", { class: "stack" }, stage, h("div", { class: "zone-tools" }, h("span", { class: "help" }, "Pick an area, then drag on the picture to redraw it. Racks and cabinets win where they overlap a sink or counter."), h("button", { type: "button", class: "btn quiet sm", onclick: loadSnap }, icon("refresh", 14), h("span", {}, "New picture")))),
        h("div", { class: "stack" },
          listEl,
          h("div", { class: "row-actions" }, h("button", { type: "button", class: "btn sm", onclick: add }, icon("plus", 14), h("span", {}, "Add area")), h("button", { type: "button", class: "btn quiet sm", onclick: remove }, icon("trash", 14), h("span", {}, "Remove"))),
          h("div", { class: "area-edit" },
            field("Name", nameIn), field("What it is", roleSel),
            h("details", { class: "coords" }, h("summary", {}, "Exact position"), h("div", { class: "coord-grid" }, coords.map((c, i) => h("label", {}, ["left", "top", "width", "height"][i], c)))),
            h("p", { class: "help" }, "To measure how full the sink or a counter is, clear it and save an empty photo. Do it again if the camera moves."),
            h("button", { type: "button", class: "btn sm", disabled: demo, onclick: empty, style: { justifySelf: "start" } }, icon("camera", 14), h("span", {}, "It's empty now"))))),
      h("div", { class: "form-actions" }, error, h("button", { type: "button", class: "btn quiet", onclick: () => { areas = saved(); current = areas[0]?.id; renderList(); draw(); } }, "Reset"), h("button", { type: "button", class: "btn primary", disabled: demo, onclick: save }, "Save areas")));
  }

  renderStatus(); renderStorage();
  const initial = location.hash.split("/")[2];
  if (initial) requestAnimationFrame(() => document.getElementById(`s-${initial}`)?.scrollIntoView({ block: "start" }));
  return { update(kind) { if (kind === "data") { renderStatus(); renderStorage(); } }, destroy() { spy.disconnect(); } };
}

function check(name, label, checked, help) {
  return h("label", { class: "check" }, h("input", { type: "checkbox", name, checked }), h("span", { class: "switch", "aria-hidden": "true" }), h("span", {}, h("strong", {}, label), h("small", {}, help)));
}
