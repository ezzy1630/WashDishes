import {api, fill, h, state, icon} from '../core.js';
import {time, dishTitle, isFuture, photoReason, croppedView} from '../photo-ui.js';

const FRAME_MS = 15000;

export function feedPage(main, route) {
  if (route.query?.record) { location.hash = `#/incident/${Number(route.query.record)}`; return {update() {}, destroy() {}}; }
  let alive = true, busy = false, next = null, signature = '', frame = null;
  const archive = route.query?.filter === 'archive';
  const listOnly = archive || ['tracking', 'out', 'unknown'].includes(route.query?.filter);
  const person = Number(route.query?.person || (route.page === 'person' ? route.sub : 0));
  const personQuery = person > 0 ? `&resident_id=${person}` : '';
  const status = h('p', {class: 'tx2'});
  const error = h('p', {class: 'form-error', role: 'alert'});
  const more = h('button', {class: 'btn', hidden: true}, 'Load more');

  if (listOnly) {
    const rows = h('div', {class: 'watch-list'});
    fill(main, h('div', {class: 'page photo-home'},
      h('a', {class: 'crumb', href: '#/feed'}, icon('arrowLeft'), 'Photos'),
      h('header', {class: 'page-head'}, h('div', {}, h('h1', {id: 'page-title', tabindex: '-1'}, archive ? 'Archive' : 'Being watched'), status)),
      error, rows, more));
    status.textContent = archive ? 'Older records kept outside current counts.' : 'Photos are attached automatically.';
    const load = async (append = false) => {
      if (busy) return; busy = true;
      try {
        const data = await api(`/api/incidents?filter=${archive ? 'archive' : 'tracking'}&limit=24&offset=${append ? next || 0 : 0}${personQuery}`);
        if (!alive) return;
        if (!append) fill(rows);
        rows.append(...data.items.map(c => h('a', {class: 'watch-row', href: `#/incident/${c.id}`},
          h('span', {}, h('strong', {}, dishTitle(c.title)), h('span', {class: 'tx2'}, c.location || 'Kitchen')),
          h('span', {class: 'tx2'}, `Tracked since ${time(c.created_at)}`), icon('chevronRight', 16))));
        if (!data.total) rows.append(h('p', {class: 'tx2'}, 'No records here.'));
        next = data.next_offset; more.hidden = next === null;
      } catch (e) { if (alive) error.textContent = `Could not load records: ${e.message}`; }
      finally { busy = false; }
    };
    more.onclick = () => load(true);
    load();
    return {update() {}, destroy() { alive = false; }};
  }

  const picture = h('div');
  const caption = h('figcaption', {class: 'tx2'});
  const figure = h('figure', {class: 'now-frame', hidden: true}, picture, caption);
  const outList = h('ol', {class: 'now-list'});
  const ready = h('div', {class: 'share-list'});
  fill(main, h('div', {class: 'page photo-home'},
    h('header', {class: 'page-head'}, h('div', {}, h('h1', {id: 'page-title', tabindex: '-1'}, 'Out now'), status),
      h('a', {href: '#/live', class: 'btn'}, icon('camera'), 'Live kitchen')),
    error,
    h('div', {class: 'home-grid'},
      h('section', {class: 'out-now', 'aria-labelledby': 'page-title'}, figure, outList),
      h('section', {class: 'ready', 'aria-labelledby': 'ready-title'},
        h('h2', {id: 'ready-title'}, 'Ready to share'),
        h('p', {class: 'section-note'}, 'Four camera views, with the dish in the same spot two hours after it was left out.'),
        ready, more))));

  function health() {
    const cam = state.data?.camera || {};
    if (state.data?.mode === 'snapshot') status.textContent = 'Saved household preview. No camera picture.';
    else if (!cam.connected) status.textContent = 'Camera offline. Showing saved records.';
    else if (cam.view?.state === 'moved') status.textContent = 'Camera moved. New records are paused.';
    else status.textContent = 'Camera on. Dishes on counters and in the sink are tracked automatically.';
  }

  // One recent analysed picture, with boxes drawn only for the exact frame they came from.
  async function refreshFrame() {
    let live = null;
    const clearFrame = () => { if (frame) URL.revokeObjectURL(frame.url); frame = null; };
    if (frame && Date.now() + state.serverOffset - Date.parse(frame.at) > 30000) clearFrame();
    try { const response = await api('/api/live'); live = response.fresh ? response.live : null; } catch { /* the list still stands on its own */ }
    if (!live || Date.now() + state.serverOffset - Date.parse(live.frame_at) > 30000) { clearFrame(); return; }
    if (frame?.id === live.frame_id || (frame && Date.now() - frame.fetched < FRAME_MS)) return;
    try {
      const response = await fetch(`/api/live/frame/${live.frame_id}`, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) return;
      const blob = await response.blob();
      if (!alive) return;
      if (frame) URL.revokeObjectURL(frame.url);
      frame = {id: live.frame_id, at: live.frame_at, url: URL.createObjectURL(blob), fetched: Date.now(),
        dishes: live.detections.filter(d => d.kind !== 'person' && d.counted && ['dirty', 'sink'].includes(d.role))};
    } catch { /* keep the previous picture */ }
  }

  function drawFrame(numbers) {
    figure.hidden = !frame;
    if (!frame) return;
    fill(picture, croppedView({src: frame.url, alt: 'Recent camera picture of the counters with detected dishes outlined',
      marks: frame.dishes.map(d => ({box: d.box, label: numbers.get(d.obligation_id), kind: numbers.has(d.obligation_id) ? '' : 'unlinked'}))}));
    caption.textContent = `Camera picture ${time(frame.at)}, cropped to the counters. Boxes are detector suggestions; they can overlap or miss dishes.`;
  }

  function outRow(c, n, inPicture) {
    const waiting = isFuture(c.followup_at);
    const reason = c.share_ready ? 'Photo ready to share' : photoReason(c.share_reason, c.captured);
    const note = [waiting ? `next photo ${time(c.followup_at)}` : null, !c.share_ready && c.followup_captured ? 'two-hour photo attached' : null, !waiting || !/two-hour/.test(reason) ? reason.toLowerCase() : null,
      frame && !inPicture ? 'not in this picture' : null].filter(Boolean).join(' · ');
    return h('li', {}, h('a', {class: 'now-row', href: `#/incident/${c.id}`},
      h('span', {class: `now-mark${!frame ? ' plain' : inPicture ? '' : ' absent'}`, 'aria-hidden': 'true'}, n),
      h('span', {class: 'now-text'}, h('strong', {}, `${dishTitle(c.title)} `, h('span', {class: 'tx2'}, c.location || 'Kitchen')),
        h('span', {class: 'tx2'}, [`Tracked since ${time(c.created_at)}`, note].filter(Boolean).join(' · '))),
      icon('chevronRight', 16)));
  }

  function card(c) {
    return h('article', {class: 'share-card'},
      h('a', {class: 'share-picture', href: `#/incident/${c.id}`, 'aria-label': `Open ${dishTitle(c.title)} photo`},
        h('img', {src: c.share_url, alt: `${dishTitle(c.title)}: earlier, handling, left out and later views`, loading: 'lazy',
          onerror: e => e.target.replaceWith(h('p', {class: 'photo-error'}, 'Photo unavailable. Open to retry.'))})),
      h('div', {class: 'share-card-foot'},
        h('h3', {class: 'sr-only'}, `${dishTitle(c.title)}, ${c.location || 'Kitchen'}`),
        h('a', {class: 'btn quiet', href: `#/incident/${c.id}`}, 'Details'),
        h('a', {class: 'btn primary', href: c.share_url, download: `dish-${c.id}.jpg`}, 'Download')));
  }

  async function load(append = false) {
    if (busy) return; busy = true;
    try {
      const [photos, watching] = await Promise.all([
        api(`/api/incidents?filter=photos&limit=${append ? 24 : Math.min(100, Math.max(24, ready.querySelectorAll('.share-card').length))}&offset=${append ? next || 0 : 0}${personQuery}`),
        append ? null : api(`/api/incidents?filter=tracking&limit=100${personQuery}`), append ? null : refreshFrame()]);
      if (!alive) return;
      error.textContent = '';
      if (append) { ready.append(...photos.items.map(card)); }
      else {
        // Oldest first: the dish that has been out longest leads.
        const out = [...watching.items, ...photos.items.filter(c => c.active)].sort((a, b) => a.created_at.localeCompare(b.created_at));
        const fresh = JSON.stringify([photos, watching, frame?.id]);
        if (fresh !== signature) {
          signature = fresh;
          const numbers = new Map(out.map((c, i) => [c.id, i + 1]));
          const seen = new Set(frame?.dishes.map(d => d.obligation_id));
          drawFrame(numbers);
          fill(outList, out.map((c, i) => outRow(c, i + 1, seen.has(c.id))),
            out.length ? null : h('li', {class: 'now-empty tx2'}, frame ? 'No dishes tracked on counters or in the sink.' : 'No dishes tracked right now.'));
          fill(ready, photos.items.map(card), photos.total ? null : h('p', {class: 'photo-empty tx2'}, 'Nothing ready yet.'));
        }
      }
      next = photos.next_offset; more.hidden = next === null;
    } catch (e) { if (alive) error.textContent = `Could not load dishes: ${e.message}`; }
    finally { busy = false; }
  }
  more.onclick = () => load(true);
  health(); load();
  return {update(kind) { if (kind === 'data') { health(); load(); } }, destroy() { alive = false; if (frame) URL.revokeObjectURL(frame.url); }};
}
