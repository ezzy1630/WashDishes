import { getFootage } from "../api.js";
import { recordedPlayer } from "../footage-player.js";
import { button, field, h, heading } from "../ui.js";

/** @typedef {Awaited<ReturnType<typeof getFootage>>} Index */
/** @type {(()=>void)|null} */
let dispose = null;
export function stopFootage() { dispose?.(); dispose = null; }

/** @param {import('../types.js').Visit} visit @param {import('../types.js').ScreenContext} context */
export function watchFootage(visit, context) {
  const at = Date.parse(visit.at) / 1000;
  return button("Watch the footage", () => context.navigate(`/footage?at=${encodeURIComponent(at)}`), "secondary watch-footage");
}

/** One shared recorded player, entered from navigation or any dish picture. */
export function footagePage() {
  stopFootage();
  let alive = true, timezone = "UTC";
  /** @type {Index|null} */ let index = null;
  const clock = h("p", { class: "footage-clock" }, "Choose a recording"),
    status = h("p", { class: "help footage-status", role: "status" }, "Loading recordings…"),
    panel = h("section", { class: "footage-controls", "aria-label": "Browse recordings" }),
    moments = h("div", { class: "footage-moments" }),
    timeline = h("div", { class: "footage-timeline" }),
    slider = h("input", { type: "range", step: 1, "aria-label": "Recording time" }),
    chosen = h("output", { class: "help footage-chosen", "aria-live": "off" }),
    jump = h("input", { type: "datetime-local", name: "footage-time", required: true, step: 1 }),
    previous = button("Previous chunk", () => { if (player.current?.previous != null) void load(player.current.previous, false, true); }, "secondary"),
    next = button("Next chunk", () => { if (player.current?.next != null) void load(player.current.next, false, true); }, "secondary");
  previous.disabled = next.disabled = true;
  const refresh = button("Refresh recordings", () => void refreshIndex(), "quiet");
  const player = recordedPlayer({
    status: (text) => { status.textContent = text; },
    clock: (at) => { clock.textContent = timeLabel(at); },
    changed: (chunk, at) => {
      slider.value = String(at); chosen.textContent = timeLabel(at);
      previous.disabled = chunk.previous === null; next.disabled = chunk.next === null;
      showMoments(at);
    },
  });
  const load = player.load;
  const root = h("div", { class: "footage-page" }, heading("Kitchen footage", "Browse the recordings kept by the kitchen camera."),
    player.element, clock, status,
    h("div", { class: "footage-chunks" }, previous, next), panel,
    h("p", { class: "help" }, "Recorded only · No audio · Clock times are approximate. New footage appears after a segment finishes. Older recordings roll off automatically."), refresh);
  dispose = () => { alive = false; player.stop(); };

  /** @param {number} at */
  function timeLabel(at) {
    return new Intl.DateTimeFormat("en-US", { timeZone: timezone, month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" }).format(new Date(at * 1000));
  }
  /** @param {number} at */
  function showMoments(at) {
    if (!index) return;
    const nearest = [...index.moments].sort((a, b) => Math.abs(a.at - at) - Math.abs(b.at - at)).slice(0, 12).sort((a, b) => a.at - b.at);
    moments.replaceChildren(h("h2", {}, "Nearby moments"),
      h("p", { class: "help" }, "Presence is sampled from detections, so some visits will be missing. It does not identify whose dishes these are."),
      ...nearest.map((m) => button(`${m.label} · ${timeLabel(m.at)}`, () => void load(m.at), "quiet")));
  }
  slider.addEventListener("input", () => { chosen.textContent = timeLabel(Number(slider.value)); });
  slider.addEventListener("change", () => void load(Number(slider.value)));
  const form = h("form", { class: "footage-jump", onsubmit: (/** @type {SubmitEvent} */ e) => {
    e.preventDefault();
    const at = new Date(jump.value).getTime() / 1000;
    if (Number.isFinite(at)) void load(at);
  } }, field("Jump to date and time (your device’s timezone)", jump), h("button", { type: "submit", class: "secondary" }, "Go to time"));
  async function refreshIndex() {
    refresh.disabled = true;
    try {
      const data = await getFootage();
      if (!alive) return;
      index = data; timezone = data.timezone;
      if (!data.ranges.length) {
        status.textContent = "No finished recordings are available yet. Try refreshing shortly.";
        panel.replaceChildren(); return;
      }
      const start = data.ranges[0].start, end = data.ranges[data.ranges.length - 1].end;
      slider.min = String(Math.ceil(start)); slider.max = String(Math.floor(end));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 1000 44");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("aria-hidden", "true");
      for (const range of data.ranges) {
        const rect = document.createElementNS(svg.namespaceURI, "rect");
        rect.setAttribute("x", String((range.start - start) / (end - start) * 1000));
        rect.setAttribute("width", String((range.end - range.start) / (end - start) * 1000));
        rect.setAttribute("height", "12"); rect.setAttribute("y", "24"); svg.append(rect);
      }
      // At phone width each lane has at most one one-pixel mark per ~5 px.
      // Density cannot cover the recording/gap band beneath it.
      const bins = new Set();
      for (const moment of data.moments) {
        const bin = Math.max(0, Math.min(71, Math.floor((moment.at - start) / (end - start) * 72)));
        const key = `${moment.kind}:${bin}`;
        if (bins.has(key)) continue;
        bins.add(key);
        const mark = document.createElementNS(svg.namespaceURI, "rect");
        for (const [k, v] of Object.entries({ x: String(bin / 72 * 1000 + 5), width: "3", height: "6",
          y: moment.kind === "dish" ? "2" : "12", class: moment.kind })) mark.setAttribute(k, v);
        svg.append(mark);
      }
      timeline.replaceChildren(svg, slider);
      panel.replaceChildren(h("h2", {}, "Retained recordings"),
        h("p", { class: "help" }, `${timeLabel(start)} – ${timeLabel(end)}`), timeline,
        h("p", { class: "help footage-key" }, "Bottom band: recorded · Empty: gap · Blue lane: dishes · Amber lane: people"),
        chosen, form, moments);
      if (!player.current) {
        const wanted = new URLSearchParams(location.search).get("at");
        const at = wanted === null ? end - 10 : Number(wanted);
        await load(at);
      } else if (player.current) {
        showMoments(player.current.start);
      }
    } catch (error) {
      if (alive) status.textContent = error instanceof Error ? error.message : "Could not load recordings.";
    } finally { if (alive) refresh.disabled = false; }
  }
  void refreshIndex();
  return root;
}
