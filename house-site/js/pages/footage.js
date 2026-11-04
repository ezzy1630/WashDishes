import { getFootage, getFootageChunk } from "../api.js";
import { button, field, h, heading, imageURL } from "../ui.js";

/** @typedef {Awaited<ReturnType<typeof getFootage>>} Index */
/** @typedef {Awaited<ReturnType<typeof getFootageChunk>>} Chunk */
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
  let alive = true, sequence = 0, timezone = "UTC", busy = false;
  /** @type {Index|null} */ let index = null;
  /** @type {Chunk|null} */ let current = null;
  const video = h("video", { controls: true, playsinline: true, preload: "metadata",
    "aria-label": "Recorded kitchen footage", crossorigin: "anonymous" });
  video.muted = true;
  const clock = h("p", { class: "footage-clock" }, "Choose a recording"),
    status = h("p", { class: "help footage-status", role: "status" }, "Loading recordings…"),
    panel = h("section", { class: "footage-controls", "aria-label": "Browse recordings" }),
    moments = h("div", { class: "footage-moments" }),
    timeline = h("div", { class: "footage-timeline" }),
    slider = h("input", { type: "range", step: 1, "aria-label": "Recording time" }),
    chosen = h("p", { class: "help" }),
    jump = h("input", { type: "datetime-local", name: "footage-time", required: true, step: 1 }),
    previous = button("Previous chunk", () => { if (current?.previous != null) void load(current.previous); }, "secondary"),
    next = button("Next chunk", () => { if (current?.next != null) void load(current.next); }, "secondary");
  previous.disabled = next.disabled = true;
  const refresh = button("Refresh recordings", () => void refreshIndex(), "quiet");
  const root = h("div", { class: "footage-page" }, heading("Kitchen footage", "Browse the recordings kept by the kitchen camera."),
    video, clock, status, h("div", { class: "footage-chunks" }, previous, next), panel,
    h("p", { class: "help" }, "Recorded only · No audio · Clock times are approximate. New footage appears after a segment finishes. Older recordings roll off automatically."), refresh);
  dispose = () => { alive = false; sequence++; video.pause(); video.removeAttribute("src"); video.load(); };

  /** @param {number} at */
  function timeLabel(at) {
    return new Intl.DateTimeFormat("en-US", { timeZone: timezone, month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" }).format(new Date(at * 1000));
  }
  function clockUpdate() {
    if (current) clock.textContent = timeLabel(current.start + video.currentTime);
  }
  video.addEventListener("timeupdate", clockUpdate);
  video.addEventListener("seeked", clockUpdate);
  video.addEventListener("ended", () => { if (current?.next != null) void load(current.next, true); });
  video.addEventListener("error", () => {
    if (alive && current) status.textContent = "Playback stopped. Open the moment again to refresh its link, or try the next chunk.";
  });
  video.addEventListener("play", () => {
    if (current && current.expires <= Date.now() / 1000 + 5) void load(current.start + video.currentTime, true);
  });
  /** @param {number} at @param {boolean} [play] */
  async function load(at, play = false) {
    const request = ++sequence;
    busy = true;
    video.pause();
    status.textContent = "Opening recording…";
    previous.disabled = next.disabled = true;
    try {
      const chunk = await getFootageChunk(at);
      if (!alive || request !== sequence) return;
      const src = imageURL(chunk.url);
      if (!src) throw new Error("This recording address is unavailable.");
      current = chunk;
      video.onloadedmetadata = () => {
        if (request !== sequence) return;
        video.currentTime = Math.min(chunk.offset, Math.max(0, video.duration - 0.1));
        clockUpdate();
        if (play) void video.play().catch(() => { status.textContent = "Tap play to continue."; });
      };
      video.src = src;
      video.load();
      slider.value = String(at);
      chosen.textContent = timeLabel(at);
      clock.textContent = timeLabel(chunk.start + chunk.offset);
      status.textContent = "Scrub the player, or choose another moment below.";
      previous.disabled = chunk.previous === null;
      next.disabled = chunk.next === null;
      showMoments(at);
    } catch (error) {
      if (alive && request === sequence) {
        status.textContent = error instanceof Error ? error.message : "Could not open this recording.";
        previous.disabled = current?.previous == null;
        next.disabled = current?.next == null;
      }
    } finally { if (request === sequence) busy = false; }
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
      svg.setAttribute("viewBox", "0 0 1000 34");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("aria-hidden", "true");
      for (const range of data.ranges) {
        const rect = document.createElementNS(svg.namespaceURI, "rect");
        rect.setAttribute("x", String((range.start - start) / (end - start) * 1000));
        rect.setAttribute("width", String((range.end - range.start) / (end - start) * 1000));
        rect.setAttribute("height", "26"); rect.setAttribute("y", "4"); svg.append(rect);
      }
      for (const moment of data.moments) {
        const line = document.createElementNS(svg.namespaceURI, "line");
        const x = String((moment.at - start) / (end - start) * 1000);
        for (const [k, v] of Object.entries({ x1: x, x2: x, y1: "0", y2: "34", class: moment.kind })) line.setAttribute(k, v);
        svg.append(line);
      }
      timeline.replaceChildren(svg, slider);
      panel.replaceChildren(h("h2", {}, "Retained recordings"),
        h("p", { class: "help" }, `${timeLabel(start)} – ${timeLabel(end)}`), timeline,
        h("p", { class: "help footage-key" }, "Filled: recorded · Empty: gap · Blue: dish · Amber: person"),
        chosen, form, moments);
      if (!current && !busy) {
        const wanted = new URLSearchParams(location.search).get("at");
        const at = wanted === null ? end - 1 : Number(wanted);
        await load(at);
      } else if (current) {
        showMoments(current.start);
      }
    } catch (error) {
      if (alive) status.textContent = error instanceof Error ? error.message : "Could not load recordings.";
    } finally { if (alive) refresh.disabled = false; }
  }
  void refreshIndex();
  return root;
}
