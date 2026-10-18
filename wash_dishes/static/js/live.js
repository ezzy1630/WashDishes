// Live kitchen view: relay video underneath, analyzed detections drawn on a canvas above.
// Detections arrive at the analysis rate (~3-4 fps); boxes glide between frames and the draw
// loop stops as soon as nothing is moving, so an idle feed costs no repaints.
import { fill, h, api, state, person, remaining, decision, duration } from "./core.js";

export const ROLE_COLORS = { dirty: "#d9a04a", sink: "#5b8ff0", clean: "#45b383", storage: "#9282e0" };
const FONT = '"Mona Sans", system-ui, sans-serif';
const FOCUS = "#ffffff", PERSON = "#e8ebee", LATE = "#ef5f55", WARN = "#e0a548";
const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

// Stable identity for a detection across frames. Inventory dishes have no track id but hold
// still, so their rounded box identifies them; people without a track id are not followed.
export function detKey(d) {
  if (d.scene_key) return `d:${d.scene_key}`;
  if (d.track_id != null) return `${d.kind === "person" ? "p" : "d"}:${d.track_id}`;
  if (d.kind === "person") return null;
  return `d:${d.kind}@${d.box.map(v => Math.round(v * 100)).join(",")}`;
}

function avcCodec(bytes) {
  // The avcC box in the MP4 header names the H.264 profile and level Media Source needs.
  for (let i = 0; i + 7 < bytes.length; i++) if (bytes[i] === 0x61 && bytes[i + 1] === 0x76 && bytes[i + 2] === 0x63 && bytes[i + 3] === 0x43) {
    const hex = n => n.toString(16).padStart(2, "0");
    return `avc1.${hex(bytes[i + 5])}${hex(bytes[i + 6])}${hex(bytes[i + 7])}`;
  }
  return null;
}

export class LiveView {
  constructor({ onSelect = () => {}, onUpdate = () => {} } = {}) {
    this.onSelect = onSelect; this.onUpdate = onUpdate;
    // Low-confidence boxes and walking trails are debugging aids; the page offers no switch for them.
    this.layers = { zones: true, dishes: true, people: true, ...JSON.parse(localStorage.getItem("dw-layers") || "{}"), lowconf: false, trails: false };
    this.quality = localStorage.getItem("dw-video") === "sd" ? "sd" : "hd";
    this.items = new Map(); this.trails = new Map(); this.focusId = null; this.hoverId = null;
    this.frameId = 0; this.data = null; this.error = ""; this.timer = 0; this.raf = 0; this.busy = false; this.alive = false;
    this.video = { ctl: null, url: "", ok: false, failedAt: 0 };
    this.imageURL = "";
    this.build();
  }

  build() {
    this.videoEl = h("video", { class: "feed-media", muted: true, autoplay: true, playsInline: true, hidden: true, "aria-hidden": "true" });
    this.videoEl.muted = true; this.videoEl.disableRemotePlayback = true;
    this.imgEl = h("img", { class: "feed-media", alt: "", hidden: true, draggable: "false" });
    this.canvas = h("canvas", { class: "feed-canvas", "aria-hidden": "true" });
    this.placeholder = h("div", { class: "feed-empty" });
    this.overlay = h("span", { class: "feed-ov", hidden: true });
    this.el = h("div", { class: "feed-stage", role: "img", "aria-label": "Live kitchen camera with detected dishes and people outlined" }, this.videoEl, this.imgEl, this.canvas, this.overlay, this.placeholder);
    this.ctx = this.canvas.getContext("2d");
    this.resize = new ResizeObserver(() => { this.fit(); this.kick(); });
    this.resize.observe(this.el);
    this.canvas.addEventListener("pointermove", e => {
      const id = this.hit(e);
      this.canvas.style.cursor = id ? "pointer" : "";
      if (id !== this.hoverId) { this.hoverId = id; this.kick(); }
    });
    this.canvas.addEventListener("pointerleave", () => { this.hoverId = null; this.canvas.style.cursor = ""; this.kick(); });
    this.canvas.addEventListener("click", e => { const key = this.hit(e); if (key) this.onSelect(key, this.items.get(key)?.d); });
    document.fonts?.ready.then(() => this.kick());
  }

  start() {
    this.alive = true;
    this.redrawTimer = setInterval(() => { if ([...this.items.values()].some(i => i.d.obligation_id)) this.kick(); }, 1000);
    this.tick();
  }

  stop() {
    this.alive = false;
    clearTimeout(this.timer); clearInterval(this.redrawTimer); cancelAnimationFrame(this.raf); this.raf = 0;
    this.stopVideo(); this.resize.disconnect();
    if (this.imageURL) URL.revokeObjectURL(this.imageURL);
  }

  setLayer(key, on) { this.layers[key] = on; localStorage.setItem("dw-layers", JSON.stringify(this.layers)); this.kick(); }
  setQuality(q) { this.quality = q; localStorage.setItem("dw-video", q); this.stopVideo(); this.video.failedAt = 0; }
  focus(key) { if (this.focusId !== key) { this.focusId = key; this.kick(); } }

  get mode() {
    const live = this.data?.live, cam = this.data?.camera || state.data?.camera;
    if (this.error) return "lost";
    if (state.data?.mode === "demo") return "demo";
    if (live) return "live";
    if (cam?.connected) return this.data?.vision_enabled ? "waiting" : "noVision";
    return "offline";
  }

  // ---- Polling. One request in flight at a time, paced to the analysis rate.
  async tick() {
    clearTimeout(this.timer);
    if (!this.alive) return;
    if (!document.hidden && !this.busy) {
      this.busy = true;
      try {
        const next = await api("/api/live");
        if (!this.alive) return;
        this.error = "";
        if (next.camera?.video && !this.video.ctl && Date.now() - this.video.failedAt > 15000) this.startVideo();
        let image = null;
        if (!this.video.ok) {
          if (next.live && next.live.frame_id !== this.frameId) image = await this.fetchImage(`/api/live/frame/${next.live.frame_id}`);
          else if (!next.live && next.camera?.connected && Date.now() - (this.snapAt || 0) > 2500) { image = await this.fetchImage(`${next.camera.snapshot_url}?t=${Date.now()}`); this.snapAt = Date.now(); }
        }
        if (!this.alive) { if (image) URL.revokeObjectURL(image.url); return; }
        if (image) this.showImage(image);
        const changed = next.live?.frame_id !== this.frameId;
        this.data = next;
        if (changed) { this.frameId = next.live?.frame_id || 0; this.ingest(next.live?.detections || []); }
        this.onUpdate(next);
      } catch (e) { if (this.alive) { this.error = e.message; this.onUpdate(this.data); } }
      finally { this.busy = false; this.renderPlaceholder(); }
    }
    const pace = this.data?.live ? (this.video.ok ? 240 : 320) : 1500;
    this.timer = setTimeout(() => this.tick(), pace);
  }

  async fetchImage(path) {
    try {
      const response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return null;
      const url = URL.createObjectURL(await response.blob());
      const probe = new Image(); probe.src = url;
      await probe.decode();
      return { url, width: probe.naturalWidth, height: probe.naturalHeight };
    } catch { return null; }
  }

  showImage(image) {
    this.imgEl.src = image.url;
    if (!this.video.ok) this.imgEl.hidden = false;
    if (this.imageURL) URL.revokeObjectURL(this.imageURL);
    this.imageURL = image.url;
    if (image.width) this.el.style.aspectRatio = `${image.width} / ${image.height}`;
  }

  async startVideo() {
    const Source = window.ManagedMediaSource || window.MediaSource;
    if (!Source || this.video.ctl) return;
    const ctl = new AbortController(); this.video.ctl = ctl;
    const el = this.videoEl;
    try {
      const response = await fetch(`/api/live/video?quality=${this.quality}`, { signal: ctl.signal, credentials: "same-origin", cache: "no-store" });
      if (!response.ok || !response.body) throw new Error("no video");
      const reader = response.body.getReader();
      let head = new Uint8Array(0), codec = null;
      while (!codec) {
        const { value, done } = await reader.read(); if (done) throw new Error("ended");
        const merged = new Uint8Array(head.length + value.length); merged.set(head); merged.set(value, head.length); head = merged;
        codec = avcCodec(head); if (head.length > 4e6) throw new Error("format");
      }
      const source = new Source();
      this.video.url = URL.createObjectURL(source); el.src = this.video.url;
      await new Promise(resolve => source.addEventListener("sourceopen", resolve, { once: true }));
      const buffer = source.addSourceBuffer(`video/mp4; codecs="${codec}"`); buffer.mode = "segments";
      const queue = [head];
      const pump = () => {
        if (buffer.updating || source.readyState !== "open") return;
        const range = buffer.buffered;
        if (range.length && range.end(range.length - 1) - range.start(0) > 30) { buffer.remove(range.start(0), range.end(range.length - 1) - 10); return; }
        if (queue.length) buffer.appendBuffer(queue.shift());
      };
      buffer.addEventListener("updateend", () => {
        const range = buffer.buffered;
        // Stay at the live edge; a backgrounded tab must not resume minutes behind.
        if (range.length && range.end(range.length - 1) - el.currentTime > 1.5) el.currentTime = range.end(range.length - 1) - 0.3;
        if (el.paused) el.play().catch(() => {});
        pump();
      });
      el.addEventListener("loadedmetadata", () => { if (el.videoWidth) this.el.style.aspectRatio = `${el.videoWidth} / ${el.videoHeight}`; }, { once: true });
      pump();
      this.video.ok = true; el.hidden = false; this.imgEl.hidden = true;
      this.renderPlaceholder(); this.onUpdate(this.data);
      for (;;) { const { value, done } = await reader.read(); if (done) break; queue.push(value); pump(); }
      throw new Error("ended");
    } catch {
      if (ctl.signal.aborted) return;
      this.video.ctl = null; this.video.ok = false; this.video.failedAt = Date.now(); el.hidden = true;
      if (this.imageURL) this.imgEl.hidden = false;
      this.renderPlaceholder(); this.onUpdate(this.data);
    }
  }

  stopVideo() {
    this.video.ctl?.abort(); this.video.ctl = null; this.video.ok = false;
    this.videoEl.removeAttribute("src"); this.videoEl.load(); this.videoEl.hidden = true;
    if (this.video.url) { URL.revokeObjectURL(this.video.url); this.video.url = ""; }
    if (this.imageURL) this.imgEl.hidden = false;
  }

  renderPlaceholder() {
    const showing = this.video.ok || (this.imageURL && !this.imgEl.hidden);
    this.placeholder.hidden = Boolean(showing);
    if (showing) return;
    const cam = this.data?.camera || state.data?.camera || {};
    const copy = {
      demo: ["No camera in the demo", "The dishes, people and history around it are simulated."],
      lost: ["Lost contact with the kitchen", this.error || "Trying again…"],
      offline: [cam.configured ? "The camera is offline" : "No camera connected", cam.message || "Connect the kitchen camera in Settings."],
      waiting: ["Waiting for the first picture", cam.message || "The camera is connecting."],
      noVision: ["Waiting for the first picture", "Detection is off, so nothing will be outlined."],
      live: ["Loading the picture", ""],
    }[this.mode];
    fill(this.placeholder, h("strong", {}, copy[0]), copy[1] && h("span", {}, copy[1]),
      ["offline", "noVision"].includes(this.mode) && state.data?.mode !== "demo" ? h("a", { href: "#/setup/camera", class: "link" }, "Open Settings") : null);
  }

  // ---- Detection bookkeeping
  ingest(detections) {
    const seen = new Set(), t = performance.now();
    detections.forEach((d, i) => {
      const key = detKey(d) ?? `p:anon-${i}`;
      seen.add(key);
      let item = this.items.get(key);
      if (!item) { item = { cur: [...d.box], alpha: 0, born: t, firstSeen: Date.now() }; this.items.set(key, item); }
      item.target = d.box; item.d = d; item.dying = false;
      if (!this.video.ok) item.cur = [...d.box]; // frame mode: boxes belong to this exact still
      if (d.kind === "person" && d.counted && d.track_id != null) {
        const trail = this.trails.get(d.track_id) || [];
        trail.push([d.box[0] + d.box[2] / 2, d.box[1] + d.box[3], Date.now()]);
        this.trails.set(d.track_id, trail);
      }
    });
    for (const [key, item] of this.items) if (!seen.has(key)) item.dying = true;
    for (const [id, trail] of this.trails) { const kept = trail.filter(p => Date.now() - p[2] < 8000); if (kept.length) this.trails.set(id, kept); else this.trails.delete(id); }
    this.kick();
  }

  // The smallest box under the pointer wins; labels count as part of their box.
  hit(event) {
    const r = this.canvas.getBoundingClientRect();
    const px = event.clientX - r.left, py = event.clientY - r.top, x = px / r.width, y = py / r.height;
    for (const tag of this.tagRects || []) if (px >= tag.x && px <= tag.x + tag.w && py >= tag.y && py <= tag.y + tag.h) return tag.key;
    let best = null, area = Infinity;
    for (const [key, item] of this.items) {
      const [bx, by, bw, bh] = item.cur;
      if (item.dying || !this.visible(item.d)) continue;
      const pad = 0.006;
      if (x >= bx - pad && x <= bx + bw + pad && y >= by - pad && y <= by + bh + pad && bw * bh < area) { best = key; area = bw * bh; }
    }
    return best;
  }

  visible(d) {
    if (d.kind === "person") return this.layers.people && d.counted;
    return this.layers.dishes && (d.counted || this.layers.lowconf);
  }

  fit() {
    const r = this.el.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
    this.W = r.width; this.H = r.height;
    this.canvas.width = Math.round(r.width * dpr); this.canvas.height = Math.round(r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  kick() { if (!this.raf && this.alive !== false) { this.last = performance.now(); this.raf = requestAnimationFrame(t => this.frame(t)); } }

  frame(t) {
    this.raf = 0;
    const dt = Math.min(100, t - this.last); this.last = t;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const k = reduce ? 1 : 1 - Math.exp(-dt / 95), ka = reduce ? 1 : 1 - Math.exp(-dt / 110);
    let moving = false;
    for (const [key, item] of this.items) {
      for (let i = 0; i < 4; i++) {
        const diff = item.target[i] - item.cur[i];
        if (Math.abs(diff) > 0.0004) { item.cur[i] += diff * k; moving = true; } else item.cur[i] = item.target[i];
      }
      const goal = item.dying ? 0 : 1, da = goal - item.alpha;
      if (Math.abs(da) > 0.02) { item.alpha += da * ka; moving = true; } else item.alpha = goal;
      if (item.dying && item.alpha <= 0.02) this.items.delete(key);
    }
    this.draw();
    if (moving) this.raf = requestAnimationFrame(n => this.frame(n));
  }

  // ---- Drawing
  draw() {
    const { ctx } = this;
    if (!this.W) this.fit();
    const W = this.W, H = this.H;
    ctx.clearRect(0, 0, W, H);
    const live = this.data?.live, areas = this.data?.areas || [], showing = this.video.ok || this.imageURL;
    this.tagRects = [];
    if (!showing) return;
    const P = b => [b[0] * W, b[1] * H, b[2] * W, b[3] * H];
    const placed = [];

    if (this.layers.zones) {
      for (const a of areas) {
        const [x, y, w, hh] = P(a.box), busy = live?.zones?.[a.id] === "busy";
        ctx.save();
        ctx.strokeStyle = rgba(ROLE_COLORS[a.role] || "#888888", busy ? .55 : .3); ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(Math.round(x) + .5, Math.round(y) + .5, Math.round(w) - 1, Math.round(hh) - 1);
        ctx.restore();
        const n = live?.by_area?.[a.id];
        const lines = [[{ dot: ROLE_COLORS[a.role] || "#888" }, { text: a.name, color: "rgba(232,234,237,.85)" }, n ? { text: String(n), color: "#fff", weight: 650 } : null]];
        // Overlapping area labels step down inside their own area; one that still collides is left off.
        const size = this.measure(lines), cx = Math.min(x + 3, W - size.w - 3);
        let cy = y + 3;
        for (let i = 0; i < 3 && placed.some(r => overlap(r, { x: cx, y: cy, w: size.w, h: size.h })); i++) cy += size.h + 2;
        if (cy + size.h <= y + hh && !placed.some(r => overlap(r, { x: cx, y: cy, w: size.w, h: size.h }))) {
          this.label(cx, cy, lines, { bg: "rgba(8,9,11,.72)", border: null });
          placed.push({ x: cx, y: cy, w: size.w, h: size.h });
        }
      }
    }

    if (this.layers.trails) {
      ctx.save(); ctx.strokeStyle = "rgba(255,255,255,.6)"; ctx.lineWidth = 1.5; ctx.setLineDash([1.5, 6]); ctx.lineCap = "round";
      for (const trail of this.trails.values()) {
        if (trail.length < 2) continue;
        ctx.beginPath(); trail.forEach((p, i) => (i ? ctx.lineTo : ctx.moveTo).call(ctx, p[0] * W, p[1] * H)); ctx.stroke();
      }
      ctx.restore();
    }

    const open = new Map((state.data?.obligations || []).map(o => [o.id, o]));
    const labels = [];
    for (const [key, item] of this.items) {
      const d = item.d;
      if (!this.visible(d)) continue;
      const [x, y, w, hh] = P(item.cur), focused = key === this.focusId || key === this.hoverId;
      const isPerson = d.kind === "person", low = !d.counted;
      const o = open.get(d.obligation_id), left = o ? remaining(o) : null, late = left !== null && left <= 0;
      // A dish someone left out is drawn in the state colour; everything else in its area's colour.
      const color = focused ? FOCUS : o ? (late ? LATE : WARN) : isPerson ? PERSON : ROLE_COLORS[d.role] || "#9aa3ab";
      ctx.save(); ctx.globalAlpha = item.alpha * (low && !focused ? .55 : 1);
      ctx.strokeStyle = rgba(color, focused ? .95 : o ? .9 : isPerson ? .45 : .7); ctx.lineWidth = o || focused ? 1.75 : 1.25;
      if (isPerson || low) ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.roundRect(x + .5, y + .5, w - 1, hh - 1, 3); ctx.stroke();
      ctx.restore();
      if (low && !focused) continue;
      const owner = o && o.confidence === "confirmed" ? person(o.resident_id)?.name : null;
      const lines = [[{ text: d.kind, color: "#fff", weight: 600 }, focused ? { text: `${Math.round(Number(d.score) * 100)}%`, color: "#a3aab2" } : null]];
      if (isPerson && d.name) lines[0].push({ text: `maybe ${d.name}`, color: "#c9ced4" });
      if (o) {
        const dec = decision(o);
        if (owner) lines[0].push({ text: owner, color: "#e8eaed" });
        lines[0].push({ text: dec ? dec.label.toLowerCase() : left === null ? "no timer" : late ? `${duration(left)} late` : `${duration(left)} left`, color: late ? LATE : WARN, weight: 600 });
      }
      labels.push({ key, x, y, w, hh, lines, focused, z: o ? 2 : isPerson ? 1 : 0 });
    }

    // Labels sit above their box, stepping up past earlier labels, flipping below at the top edge.
    labels.sort((a, b) => a.z - b.z || a.y - b.y || a.x - b.x);
    for (const l of labels) {
      const size = this.measure(l.lines);
      let cx = Math.max(2, Math.min(W - size.w - 2, l.x - 1)), cy = l.y - size.h - 3;
      for (let i = 0; i < 4 && [...placed, ...this.tagRects].some(r => overlap(r, { x: cx, y: cy, w: size.w, h: size.h })); i++) cy -= size.h + 2;
      if (cy < 2) cy = l.y + l.hh + 3;
      this.label(cx, cy, l.lines, { bg: "rgba(11,12,14,.86)", border: l.focused ? "rgba(255,255,255,.5)" : null });
      this.tagRects.push({ x: cx, y: cy, w: size.w, h: size.h, key: l.key });
    }
  }

  measure(lines) {
    const { ctx } = this;
    let w = 0;
    for (const line of lines) {
      let lw = 0;
      for (const p of line.filter(Boolean)) {
        if (p.dot) lw += 11;
        else { ctx.font = `${p.weight || 500} 11px ${FONT}`; lw += ctx.measureText(p.text).width + 7; }
      }
      w = Math.max(w, lw);
    }
    return { w: Math.ceil(w + 8), h: lines.length * 15 + 4 };
  }

  label(x, y, lines, { bg, border }) {
    const { ctx } = this, size = this.measure(lines);
    ctx.save();
    ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(x, y, size.w, size.h, 4); ctx.fill();
    if (border) { ctx.strokeStyle = border; ctx.lineWidth = 1; ctx.beginPath(); ctx.roundRect(x + .5, y + .5, size.w - 1, size.h - 1, 4); ctx.stroke(); }
    ctx.textBaseline = "middle";
    lines.forEach((line, row) => {
      let cx = x + 6; const cy = y + 9.5 + row * 15;
      for (const p of line.filter(Boolean)) {
        if (p.dot) { ctx.fillStyle = p.dot; ctx.beginPath(); ctx.arc(cx + 3, cy, 3, 0, Math.PI * 2); ctx.fill(); cx += 11; continue; }
        ctx.font = `${p.weight || 500} 11px ${FONT}`; ctx.fillStyle = p.color;
        ctx.fillText(p.text, cx, cy); cx += ctx.measureText(p.text).width + 7;
      }
    });
    ctx.restore();
  }
}

const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

export function viewState(camera) {
  const v = camera?.view;
  if (!v || v.state === "unknown" || !v.has_reference) return null;
  if (v.state === "moved") return { tone: "late", text: "Camera moved · new incidents paused" };
  const [dx, dy] = (v.offset || [0, 0]).map(n => n * 100);
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return null;
  return { tone: "warn", text: `Following a ${Math.hypot(dx, dy).toFixed(1)}% camera shift` };
}
