// Train the detector: box what's in saved kitchen pictures so the detector can be retrained on this kitchen.
import { fill, h, icon, api, state, when, bytes, empty, toast, head, pill, placeholder } from "../core.js";

const GOAL = 300;

export function modelPage(main, route) {
  const frameId = route?.query?.frame || null;
  let saving = false, alive = true;
  const t = { item: null, boxes: [], selected: -1, kind: "plate", drag: null, kinds: state.data?.dataset?.kinds || [] };
  const progress = h("section", { class: "card train-progress" }, placeholder(2));
  const meta = h("p", {}, "");
  const metaTags = h("div", { class: "r" });
  const img = h("img", { alt: "Saved kitchen picture to label", draggable: "false", hidden: true });
  const layer = h("div");
  const stage = h("div", { class: "train-stage" }, img, layer);
  const stageWrap = h("div", { class: "card-body" }, stage);
  const classes = h("div", { class: "classes", role: "group", "aria-label": "What's in the box" });
  const boxBody = h("div", { class: "box-list" });
  const boxSub = h("p", {}, "");
  const error = h("p", { class: "form-error", role: "alert" });
  const set = state.data?.settings || {};
  fill(main, h("div", { class: "page" },
    h("header", { class: "page-head" }, h("div", {}, h("a", { class: "crumb", href: frameId ? "#/live" : "#/setup" }, icon("arrowLeft", 13), frameId ? "Live" : "Settings"), h("h1", { id: "page-title", tabindex: "-1" }, frameId ? "Correct this picture" : "Train the detector"),
      h("p", {}, "Draw a box around every dish, pot, pan and person in pictures from your kitchen. In a stack, box each plate whose edge shows.")),
      h("div", { class: "page-tools" }, pill(`Detector: ${set.detector_path ? set.detector_path.split(/[\\/]/).pop() : "built-in"}`, "muted"))),
    progress,
    h("div", { class: "train-grid" },
      h("section", { class: "card" }, h("header", { class: "card-head" }, h("div", { class: "card-title" }, h("h2", {}, "Picture"), meta), metaTags), stageWrap),
      h("div", { class: "col" },
        h("section", { class: "card" }, head("What's in the box", "keys 1–9"), h("div", { class: "card-body" }, classes)),
        h("section", { class: "card" }, head("Boxes", boxSub, h("button", { type: "button", class: "btn quiet sm", onclick: () => { t.boxes = []; t.selected = -1; draw(); } }, "Clear all")), boxBody),
        h("section", { class: "card card-body stack" },
          h("div", { class: "row-actions" },
            h("button", { type: "button", class: "btn primary", onclick: () => save("labeled") }, icon("check", 15), h("span", {}, frameId ? "Save correction" : "Save, next"), h("kbd", {}, "↵")),
            h("button", { type: "button", class: "btn", onclick: () => save("skipped") }, h("span", {}, "Skip"), h("kbd", {}, "S")),
            h("button", { type: "button", class: "btn quiet", onclick: removeBox }, icon("trash", 15), h("span", {}, "Delete box"), h("kbd", {}, "⌫"))),
          error,
          h("p", { class: "keys" }, [["drag", "draw a box"], ["click", "select"], ["esc", "deselect"]].map(([k, v]) => h("span", {}, h("kbd", {}, k), v)))))),
    h("section", { class: "card" }, head("What happens to your labels", "retrained every Sunday night"),
      h("div", { class: "card-body stack" }, h("p", { class: "tx2" }, "Every Sunday at 2am, once there are 20 or more new labelled pictures, the Jetson pauses watching, retrains the detector on your pictures and replays the hand-checked recordings through both detectors. It switches only if the new one finds at least as many dishes left out with fewer false alarms. Results are under People → How well it's working. Pictures of a full sink help most."),
        h("p", { class: "help" }, "Pictures from Review come first: each one is a dish someone confirmed or a false alarm someone dismissed, already marked. Check the rest of the picture and save.")))));

  async function summary() {
    try {
      const d = await api("/api/dataset");
      if (state.data) state.data.dataset = d;
      t.kinds = d.kinds || t.kinds;
      const done = d.counts?.labeled || 0, teacher = d.counts?.teacher ?? d.teacher ?? null;
      fill(progress, h("div", { class: "card-body train-progress-body" },
        h("p", { class: "train-count" }, h("b", {}, String(done)), ` of ${GOAL} pictures labelled`),
        h("div", { class: "bar" }, h("i", { style: { width: `${Math.min(1, done / GOAL) * 100}%` } })),
        h("p", { class: "tx3" }, [`${d.counts?.unlabeled ?? 0} waiting`, d.from_review ? `${d.from_review} from Review first` : null, `${d.counts?.skipped ?? 0} skipped`, done ? `${(d.labeled_boxes / done).toFixed(1)} boxes per picture` : null, teacher != null ? `${teacher} pre-boxed by the teacher model` : null,
          d.enabled ? `${bytes(d.used_bytes)} of ${bytes(d.limit_bytes)} used` : "saving pictures is off in Settings"].filter(Boolean).join(" · "))));
      renderClasses();
    } catch (e) { fill(progress, h("p", { class: "card-body form-error" }, e.message)); }
  }

  function renderClasses() {
    fill(classes, t.kinds.map((k, i) => h("button", { type: "button", "aria-pressed": String(t.kind === k), onclick: () => setKind(k) }, i < 9 ? h("kbd", {}, String(i + 1)) : h("span", { style: { width: "16px" } }), k)));
  }

  async function load() {
    error.textContent = "";
    try {
      const item = frameId ? await api(`/api/dataset/${frameId}`) : (await api("/api/dataset/next")).item;
      if (!alive) return;
      t.item = item; t.selected = -1; t.boxes = item ? item.boxes.map(b => ({ kind: b.kind, box: [...b.box] })) : [];
      if (!item) {
        img.hidden = true; meta.textContent = ""; fill(metaTags);
        fill(stageWrap, empty("Nothing to label", "A picture is saved whenever what's on the counters changes. Check back after the kitchen has been used."));
        draw(); return;
      }
      if (!stage.isConnected) fill(stageWrap, stage);
      meta.textContent = `${when(item.created_at)} · ${item.reason || "saved picture"}`;
      fill(metaTags, item.holdout ? pill("Reserved for testing", "ask") : pill("Training picture", "muted"));
      if (item.correction) meta.textContent += item.holdout ? " · The surrounding 30 minutes are excluded from training." : " · Add missed dishes and delete false outlines; check the whole picture.";
      if (item.holdout) metaTags.title = "Test pictures score new detectors; they're never trained on.";
      img.onload = () => { stage.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`; img.hidden = false; draw(); };
      img.src = `/api/dataset/${item.id}/image`;
    } catch (e) { error.textContent = e.message; }
  }

  function draw() {
    fill(layer, t.boxes.map((b, i) => {
      const el = h("div", { class: `tbox ${b.kind === "person" ? "person" : ""} ${i === t.selected ? "on" : ""}`, "data-i": i, style: { left: `${b.box[0] * 100}%`, top: `${b.box[1] * 100}%`, width: `${b.box[2] * 100}%`, height: `${b.box[3] * 100}%` } }, h("span", {}, `${i + 1} ${b.kind}`));
      return el;
    }));
    boxSub.textContent = t.item ? `${t.boxes.length} on this picture${t.item.prelabel ? ` · pre-drawn by the ${t.item.prelabel === "teacher" ? "teacher model" : "current detector"}` : ""}` : "";
    fill(boxBody, t.boxes.length ? t.boxes.map((b, i) => h("div", { class: `box-row ${i === t.selected ? "on" : ""}`, onclick: () => { t.selected = i; t.kind = b.kind; draw(); } },
      h("span", { class: "num tx3" }, String(i + 1)), h("span", { class: "grow" }, b.kind),
      h("button", { type: "button", class: "icon-btn", "aria-label": `Delete box ${i + 1}`, onclick: e => { e.stopPropagation(); t.boxes.splice(i, 1); t.selected = -1; draw(); } }, icon("close", 13))))
      : h("p", { class: "quiet-note" }, t.item ? "No boxes yet. Drag on the picture to add one." : "—"));
    renderClasses();
  }
  function setKind(k) { t.kind = k; if (t.selected >= 0) t.boxes[t.selected].kind = k; draw(); }
  function removeBox() { if (t.selected >= 0) { t.boxes.splice(t.selected, 1); t.selected = -1; draw(); } }
  async function save(statusName) {
    if (!t.item || saving) return;
    saving = true;
    stageWrap.setAttribute("aria-busy", "true");
    try { await api(`/api/dataset/${t.item.id}`, { method: "PUT", body: { status: statusName, boxes: t.boxes } }); toast(statusName === "labeled" ? "Correction saved." : "Skipped.", { timeout: 2500 }); if (frameId) { location.hash = "#/live"; return; } await load(); summary(); }
    catch (e) { error.textContent = e.message; }
    finally { saving = false; stageWrap.setAttribute("aria-busy", "false"); }
  }

  const point = e => { const r = stage.getBoundingClientRect(); return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))]; };
  stage.addEventListener("pointerdown", e => {
    if (!t.item || saving) return;
    const hit = e.target.closest("[data-i]");
    if (hit) { t.selected = Number(hit.dataset.i); t.kind = t.boxes[t.selected].kind; draw(); return; }
    e.preventDefault(); stage.setPointerCapture(e.pointerId);
    t.drag = point(e); t.boxes.push({ kind: t.kind, box: [...t.drag, 0, 0] }); t.selected = t.boxes.length - 1;
  });
  stage.addEventListener("pointermove", e => {
    if (!t.drag) return;
    const [x, y] = t.drag, p = point(e);
    t.boxes[t.selected].box = [Math.min(x, p[0]), Math.min(y, p[1]), Math.abs(p[0] - x), Math.abs(p[1] - y)]; draw();
  });
  const end = () => { if (!t.drag) return; t.drag = null; const b = t.boxes[t.selected]; if (b && (b.box[2] < 0.004 || b.box[3] < 0.004)) { t.boxes.splice(t.selected, 1); t.selected = -1; } draw(); };
  stage.addEventListener("pointerup", end); stage.addEventListener("pointercancel", end);

  const keys = e => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input,textarea,select") || document.querySelector("dialog[open]")) return;
    if (/^[1-9]$/.test(e.key) && t.kinds[Number(e.key) - 1]) { setKind(t.kinds[Number(e.key) - 1]); e.preventDefault(); }
    else if (e.key === "Delete" || e.key === "Backspace") { removeBox(); e.preventDefault(); }
    else if (e.key === "Enter" && !e.target.closest("button,a")) { save("labeled"); e.preventDefault(); }
    else if (e.key.toLowerCase() === "s") { save("skipped"); e.preventDefault(); }
    else if (e.key === "Escape") { t.selected = -1; draw(); }
  };
  document.addEventListener("keydown", keys);
  summary(); load();
  return { update() {}, destroy() { alive = false; document.removeEventListener("keydown", keys); } };
}
