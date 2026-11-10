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
  return button("Watch the video", () => context.navigate(`/footage?at=${encodeURIComponent(at)}`, "tab"), "secondary watch-footage");
}

/** One shared recorded player, entered from navigation or any dish picture. */
export function footagePage() {
  stopFootage();
  let alive = true, timezone = "UTC", dragging = false;
  /** @type {Index|null} */ let index = null;
  const clock = h("p", { class: "footage-clock" }, ""),
    status = h("p", { class: "help footage-status", role: "status" }, "Loading…"),
    panel = h("section", { class: "footage-controls", "aria-label": "Browse recordings" }),
    moments = h("div", { class: "footage-moments" }),
    timeline = h("div", { class: "footage-timeline" }),
    slider = h("input", { type: "range", step: 1, "aria-label": "Recording time" }),
    chosen = h("output", { class: "help footage-chosen", "aria-live": "off" }),
    jump = h("input", { type: "datetime-local", name: "footage-time", required: true, step: 1 }),
    previous = button("Earlier", () => { if (player.current?.previous != null) void load(player.current.previous, false, true); }, "secondary"),
    next = button("Later", () => { if (player.current?.next != null) void load(player.current.next, false, true); }, "secondary");
  previous.disabled = next.disabled = true;
  const refresh = button("Check for new video", () => void refreshIndex(), "quiet");
  const player = recordedPlayer({
    status: (text) => { status.textContent = text; },
    clock: (at) => { clock.textContent = timeLabel(at); },
    changed: (chunk, at) => {
      if (!dragging) { slider.value = String(at); chosen.textContent = ""; }
      previous.disabled = chunk.previous === null; next.disabled = chunk.next === null;
      showMoments(at);
    },
  });
  const load = player.load;
  const root = h("div", { class: "footage-page" }, heading("Footage", "Video from the kitchen camera. No sound."),
    player.element, clock, status,
    h("div", { class: "footage-chunks" }, previous, next), panel,
    h("p", { class: "help" }, "Clock times are approximate. Older video is deleted automatically. The newest few minutes show up once they finish saving."), refresh);
  dispose = () => { alive = false; player.stop(); };

  /** @param {number} at */
  function timeLabel(at) {
    return new Intl.DateTimeFormat("en-US", { timeZone: timezone, weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(at * 1000));
  }
  /** @param {number} at */
  function shortLabel(at) {
    return new Intl.DateTimeFormat("en-US", { timeZone: timezone, weekday: "short", hour: "numeric", minute: "2-digit" }).format(new Date(at * 1000));
  }
  /** @param {number} at */
  function showMoments(at) {
    if (!index) return;
    const nearest = [...index.moments].sort((a, b) => Math.abs(a.at - at) - Math.abs(b.at - at)).slice(0, 12).sort((a, b) => a.at - b.at);
    moments.replaceChildren(h("h2", { class: "group-title" }, "Around this time"),
      h("p", { class: "group-note" }, "When the camera saw dishes or people. It can miss some, and it doesn’t show whose dishes they are."),
      ...nearest.map((m) => button(`${m.label} · ${shortLabel(m.at)}`, () => void load(m.at), "quiet")));
  }
  slider.addEventListener("input", () => {
    dragging = true;
    chosen.textContent = `Go to ${timeLabel(Number(slider.value))}`;
  });
  slider.addEventListener("change", () => {
    dragging = false;
    void load(Number(slider.value), player.playing);
  });
  slider.addEventListener("blur", () => { dragging = false; });
  const form = h("form", { class: "footage-jump", onsubmit: (/** @type {SubmitEvent} */ e) => {
    e.preventDefault();
    const at = new Date(jump.value).getTime() / 1000;
    if (Number.isFinite(at)) void load(at);
  } }, field("Go to a date and time", jump), h("button", { type: "submit", class: "secondary" }, "Go"));
  async function refreshIndex() {
    refresh.disabled = true;
    try {
      const data = await getFootage();
      if (!alive) return;
      index = data; timezone = data.timezone;
      if (!data.ranges.length) {
        status.textContent = "No video saved yet. Check back in a few minutes.";
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
      panel.replaceChildren(h("h2", { class: "group-title" }, "Everything saved"),
        timeline,
        h("p", { class: "help footage-range" }, h("span", {}, shortLabel(start)), h("span", {}, shortLabel(end))),
        h("p", { class: "footage-key" }, h("span", { class: "key dish" }, "Dishes seen"), h("span", { class: "key person" }, "People seen"), h("span", { class: "key gap" }, "Recording")),
        chosen, form, moments);
      if (!player.current) {
        const wanted = new URLSearchParams(location.search).get("at");
        const at = wanted === null ? end - 10 : Number(wanted);
        await load(at);
      } else if (player.current) {
        showMoments(player.current.start);
      }
    } catch (error) {
      if (alive) status.textContent = "Couldn’t load the video list. Try again in a minute.";
    } finally { if (alive) refresh.disabled = false; }
  }
  void refreshIndex();
  return root;
}
