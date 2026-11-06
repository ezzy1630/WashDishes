import { getFootageChunk } from "./api.js";
import { button, h, imageURL } from "./ui.js";

/** @typedef {Awaited<ReturnType<typeof getFootageChunk>>} Chunk */
/** @typedef {{chunk:Chunk,video:HTMLVideoElement,ready:Promise<void>,buffered:Promise<void>,expires:number,check:Promise<void>|null,controller:AbortController,release:()=>void}} Slot */
/** @param {{changed:(chunk:Chunk,at:number)=>void,clock:(at:number)=>void,status:(text:string)=>void}} callbacks */
export function recordedPlayer(callbacks) {
  let alive = true, generation = 0;
  /** @type {Slot|null} */ let active = null;
  /** @type {Slot|null} */ let previous = null;
  /** @type {{at:number,promise:Promise<Slot>}|null} */ let ahead = null;
  /** @type {AbortController|null} */ let selecting = null;
  /** @type {AbortController|null} */ let preparing = null;
  /** @type {Set<Slot>} */ const slots = new Set();
  /** @type {number|null} */ let selectedAt = null;
  const stage = h("div", { class: "footage-stage" });
  const play = button("Play", () => {
    if (active && !active.video.paused) active.video.pause();
    else void resume();
  }, "secondary");
  const seek = h("input", { type: "range", min: 0, max: 1, step: 0.1, value: 0,
    "aria-label": "Seek within this recording" });
  const elapsed = h("span", { class: "help footage-elapsed" }, "0:00 / 0:00");
  const retry = button("Retry recording", () => {
    if (selectedAt !== null) void load(selectedAt);
  }, "secondary");
  retry.hidden = true;
  play.disabled = seek.disabled = true;
  seek.addEventListener("input", () => { if (active) active.video.currentTime = Number(seek.value); });
  const element = h("div", {}, stage, h("div", { class: "footage-transport" }, play, seek, elapsed, retry));
  const now = () => Date.now() / 1000;
  /** @param {Chunk} chunk */
  const key = (chunk) => new URL(chunk.url).pathname;
  /** @param {number} seconds */
  const short = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  function update() {
    if (!active) return;
    const v = active.video;
    callbacks.clock(active.chunk.start + v.currentTime);
    seek.value = String(v.currentTime);
    elapsed.textContent = `${short(v.currentTime)} / ${short(Number.isFinite(v.duration) ? v.duration : 0)}`;
  }
  /** @param {unknown} error */
  function failed(error) {
    if (!alive) return;
    callbacks.status(error instanceof Error ? error.message : "Could not open this recording. Try another moment.");
    retry.hidden = false; retry.disabled = false;
    play.disabled = !active;
    seek.disabled = !active;
  }
  function cancelAhead() {
    preparing?.abort(); preparing = null; ahead = null;
    for (const slot of slots) if (slot !== active && slot !== previous) {
      slot.release(); slots.delete(slot);
    }
  }
  /** @param {Slot} slot */
  async function validate(slot) {
    // HEAD is deliberately no-store: even a fully buffered recording must not
    // bypass the server's account/retention checks on a new selection.
    const response = await fetch(slot.chunk.url, { method: "HEAD", cache: "no-store",
      credentials: "omit", referrerPolicy: "no-referrer", signal: slot.controller.signal });
    if (!response.ok) throw new Error(response.status === 410
      ? "This recording is no longer kept. Choose another moment."
      : "This footage link expired or is unavailable. Open the moment again.");
  }
  /** @param {Chunk} chunk @returns {Slot} */
  function obtain(chunk) {
    const existing = [...slots].find((s) => !s.video.error && key(s.chunk) === key(chunk) && s.expires > now() + 20);
    if (existing) { existing.chunk = chunk; existing.check = null; return existing; }
    for (const slot of slots) if (slot !== active && slot !== previous) {
      slot.release(); slots.delete(slot);
    }
    const src = imageURL(chunk.url);
    if (!src) throw new Error("This recording address is unavailable.");
    const video = h("video", { playsinline: true, preload: "auto", crossorigin: "anonymous",
      "aria-label": "Recorded kitchen footage", "aria-hidden": "true" });
    video.muted = true;
    const controller = new AbortController();
    let released = false;
    /** @type {()=>void} */ let cancelReady = () => {};
    /** @type {Promise<void>} */
    const ready = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("This recording is taking too long. Retry it or choose another time.")), 15000);
      const finish = () => { clearTimeout(timeout); resolve(); };
      video.addEventListener("loadeddata", finish, { once: true });
      video.addEventListener("error", () => { clearTimeout(timeout); reject(new Error("Could not load this recording. Try another moment.")); }, { once: true });
      cancelReady = () => { clearTimeout(timeout); reject(new Error("Recording selection changed.")); };
    });
    /** @type {()=>void} */ let cancelBuffer = () => {};
    /** @type {Promise<void>} */
    const buffered = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("The next recording is still buffering. Try again.")), 20000);
      video.addEventListener("canplaythrough", () => { clearTimeout(timeout); resolve(); }, { once: true });
      video.addEventListener("error", () => { clearTimeout(timeout); reject(new Error("Could not prepare the next recording.")); }, { once: true });
      cancelBuffer = () => { clearTimeout(timeout); reject(new Error("Recording selection changed.")); };
    });
    void buffered.catch(() => {});
    // A cancelled preload can finish after navigation; it must not create an
    // unhandled rejection or replace the currently selected recording.
    void ready.catch(() => {});
    const slot = { chunk, video, ready, buffered, expires: chunk.expires, check: null, controller, release: () => {
      if (released) return;
      released = true; controller.abort(); cancelReady(); cancelBuffer(); video.pause();
      video.removeAttribute("src"); video.load(); video.remove();
    } };
    slots.add(slot); stage.append(video);
    video.addEventListener("timeupdate", () => {
      if (active !== slot) return;
      update();
      if (video.duration - video.currentTime < 3 && ahead) {
        void ahead.promise.then((next) => {
          if (!next.check) next.check = validate(next);
          return next.check;
        }).catch(() => {});
      }
    });
    video.addEventListener("seeked", () => { if (active === slot) update(); });
    video.addEventListener("play", () => {
      if (active !== slot) return;
      if (slot.expires <= now() + 15) { video.pause(); void resume(); return; }
      play.textContent = "Pause";
    });
    video.addEventListener("playing", () => {
      if (active === slot && !ahead) prepareNext(slot);
    });
    video.addEventListener("pause", () => { if (active === slot) play.textContent = "Play"; });
    video.addEventListener("error", () => {
      if (active === slot) {
        selectedAt = slot.chunk.start + video.currentTime;
        failed(new Error("This recording stopped loading. Retry it or choose another time."));
      }
    });
    video.addEventListener("ended", () => { if (active === slot) void advance(slot); });
    video.src = src; video.load();
    return slot;
  }
  /** @param {Slot} slot @param {number} offset @param {boolean} autoplay @param {boolean} [adjacent] */
  function activate(slot, offset, autoplay, adjacent = false) {
    const old = active;
    if (old !== slot) {
      if (old) { old.video.removeAttribute("data-active"); old.video.setAttribute("aria-hidden", "true"); old.video.pause(); }
      previous = old; active = slot;
    }
    slot.video.setAttribute("data-active", ""); slot.video.removeAttribute("aria-hidden");
    if (Math.abs(slot.video.currentTime - offset) > 0.05) slot.video.currentTime = offset;
    seek.max = String(slot.video.duration); play.disabled = seek.disabled = false;
    retry.hidden = true;
    play.textContent = "Play"; update();
    const gap = adjacent && old && old !== slot ? slot.chunk.start - old.chunk.end : 0;
    const message = gap > 2 ? `Recording gap · skipped ${short(gap)}. Resuming at the next recording.`
      : "Scrub the player, or choose another moment below.";
    callbacks.status(message);
    callbacks.changed(slot.chunk, slot.chunk.start + offset);
    cancelAhead();
    if (autoplay) {
      const selected = generation;
      void start(slot, selected, message).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (alive && active === slot && generation === selected) failed(error);
      });
    }
  }
  /** @param {Slot} slot @param {number} selected @param {string} message */
  async function start(slot, selected, message) {
    const nearEnd = slot.video.duration - slot.video.currentTime < 4;
    if (nearEnd && !ahead && slot.chunk.next !== null) prepareNext(slot);
    if (nearEnd && ahead) {
      // Exact time jumps can land just before a boundary. Buffer before motion
      // starts rather than playing a fraction of a second and then freezing.
      callbacks.status("Preparing the next recording…");
      const next = await ahead.promise;
      if (!next.check) next.check = validate(next);
      await next.check;
    }
    if (!alive || selected !== generation || active !== slot) return;
    callbacks.status(message);
    await slot.video.play();
  }

  /** @param {Slot} slot */
  function prepareNext(slot) {
    preparing?.abort(); preparing = null; ahead = null;
    for (const s of slots) if (s !== active && s !== previous) { s.release(); slots.delete(s); }
    if (slot.chunk.next === null) return;
    const controller = preparing = new AbortController();
    const at = slot.chunk.next;
    const promise = (async () => {
      const chunk = await getFootageChunk(at, controller.signal);
      if (!alive || active !== slot || controller.signal.aborted) throw new Error("Recording selection changed.");
      const next = obtain(chunk);
      await next.buffered;
      return next;
    })();
    ahead = { at, promise }; void promise.catch(() => {});
  }
  /** @param {Slot} ended */
  async function advance(ended) {
    if (ended.chunk.next === null) { callbacks.status("End of the finished recordings. Refresh for newer footage."); return; }
    selectedAt = ended.chunk.next;
    const selected = generation;
    try {
      if (!ahead) prepareNext(ended);
      const prepared = ahead;
      if (!prepared) return;
      const slot = await prepared.promise;
      if (slot.expires <= now() + 15) { await load(ended.chunk.next, true, true); return; }
      if (!slot.check) slot.check = validate(slot);
      await slot.check;
      if (!alive || generation !== selected || active !== ended) return;
      activate(slot, 0, true, true);
    } catch (error) { if (alive && generation === selected) failed(error); }
  }
  /** @param {number} at @param {boolean} [autoplay] @param {boolean} [fromStart] */
  async function load(at, autoplay = false, fromStart = false) {
    selectedAt = at; retry.disabled = true;
    const selected = ++generation;
    selecting?.abort(); selecting = new AbortController(); cancelAhead();
    active?.video.pause(); play.disabled = seek.disabled = true;
    callbacks.status("Opening recording…");
    try {
      const chunk = await getFootageChunk(at, selecting.signal);
      if (!alive || selected !== generation) return;
      const slot = obtain(chunk);
      await slot.ready;
      if (!alive || selected !== generation) return;
      activate(slot, fromStart ? 0 : chunk.offset, autoplay, fromStart);
    } catch (error) { if (alive && selected === generation) failed(error); }
  }
  async function resume() {
    if (!active) return;
    if (active.video.ended && active.chunk.next !== null) {
      await load(active.chunk.next, true, true); return;
    }
    const slot = active, at = slot.chunk.start + slot.video.currentTime;
    selectedAt = at;
    const selected = ++generation;
    selecting?.abort(); selecting = new AbortController(); play.disabled = true;
    try {
      // Revalidate without discarding a useful preload on an ordinary pause.
      // Expired URLs reload through load(); buffered bytes alone cannot resume.
      const fresh = await getFootageChunk(at, selecting.signal);
      if (!alive || selected !== generation || active !== slot) return;
      if (key(fresh) !== key(slot.chunk) || slot.expires <= now() + 20) {
        await load(at, true); return;
      }
      slot.chunk = fresh;
      if (ahead) void ahead.promise.then((next) => { next.check = null; }).catch(() => {});
      callbacks.changed(fresh, at);
      play.disabled = false;
      await start(slot, selected, "Scrub the player, or choose another moment below.");
    } catch (error) { if (alive && selected === generation) failed(error); }
  }
  function stop() {
    alive = false; generation++; selecting?.abort(); preparing?.abort();
    for (const slot of slots) slot.release();
    slots.clear();
  }
  return { element, load, stop,
    get current() { return active?.chunk || null; },
    get playing() { return Boolean(active && !active.video.paused); },
  };
}
