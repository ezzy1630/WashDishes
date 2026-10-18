// Shared wording for Photos and photo detail. Times match the downloaded image: 12-hour, local.
import { h, state } from './core.js';

const valid = v => v && !Number.isNaN(Date.parse(v));
const midnight = v => new Date(v).setHours(0, 0, 0, 0);

export const isFuture = v => valid(v) && Date.parse(v) > Date.now() + state.serverOffset;

export function time(v) {
  if (!valid(v)) return '—';
  const d = new Date(v), t = d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', hour12: true});
  const days = Math.round((midnight(Date.now() + state.serverOffset) - midnight(v)) / 864e5);
  if (days === 0) return t;
  if (days === 1) return `Yesterday ${t}`;
  if (days < 7) return `${d.toLocaleDateString([], {weekday: 'short'})} ${t}`;
  return `${d.toLocaleDateString([], {month: 'short', day: 'numeric'})}, ${t}`;
}

// "1 pan" reads as data; a single item is just "Pan".
export function dishTitle(text) {
  const t = /^1 [^,]+$/.test(text || '') ? text.slice(2) : text || 'Dish';
  return t[0].toUpperCase() + t.slice(1);
}

export const span = minutes => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;

// The share gate explains which photos this record has. A stage that is not attached says
// nothing about whether the camera recording exists, so never word it as missing footage.
export function photoReason(reason, captured) {
  if (!reason || /missing|do not clearly show/.test(reason))
    return captured >= 4 ? 'Photos do not clearly box this dish' : 'Earlier photos not attached';
  return reason.replace(/\.$/, '');
}

// A camera image cropped around its boxes so small dishes stay legible. `marks` are
// [x, y, w, h] fractions of the full frame; the uncropped original stays one click away.
export function croppedView({src, alt, marks, minWidth = .55, aspect = 2, anchor = .66}) {
  const view = h('div', {class: 'crop-view'}), img = h('img', {src, alt});
  const pct = v => `${v * 100}%`;
  img.addEventListener('load', () => {
    const ratio = img.naturalHeight / img.naturalWidth, boxes = marks.map(m => m.box);
    const x0 = Math.min(...boxes.map(b => b[0])), x1 = Math.max(...boxes.map(b => b[0] + b[2]));
    const y0 = Math.min(...boxes.map(b => b[1])), y1 = Math.max(...boxes.map(b => b[1] + b[3]));
    const w = boxes.length ? Math.min(1, Math.max(minWidth, x1 - x0 + .16)) : 1;
    const ht = boxes.length ? Math.min(1, Math.max(w / ratio / aspect, y1 - y0 + .12)) : 1;
    const left = boxes.length ? Math.min(Math.max((x0 + x1) / 2 - w / 2, 0), 1 - w) : 0;
    // Keep the entire union of dish boxes visible, even along the bottom edge.
    const lower = Math.max(0, Math.min(1 - ht, y1 + .04 - ht));
    const upper = Math.min(1 - ht, Math.max(lower, y0 - .04));
    const top = boxes.length ? Math.min(Math.max((y0 + y1) / 2 - ht * anchor, lower), upper) : 0;
    view.style.aspectRatio = `${w} / ${ht * ratio}`;
    Object.assign(img.style, {width: pct(1 / w), left: pct(-left / w), top: pct(-top / ht)});
    view.append(...marks.map(m => h('span', {class: `crop-mark ${m.kind || ''}`, 'aria-hidden': 'true',
      style: {left: pct((m.box[0] - left) / w), top: pct((m.box[1] - top) / ht), width: pct(m.box[2] / w), height: pct(m.box[3] / ht)}},
    m.label ? h('b', {}, m.label) : null)));
  }, {once: true});
  view.append(img);
  return view;
}
