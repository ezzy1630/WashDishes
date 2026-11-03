// Home: decide the week's visits, then share the result or a finished proof.
import { act, api, busy, cap, fill, h, icon, toast, ago, state } from '../core.js';
import { nameChips, guessSentence } from '../name-chips.js';
import { sendButton } from '../share.js';
import { leaguePanel } from '../league.js';

export const PICTURE = { width: 1600, height: 1572 };

export function proofPage(main) {
  let alive = true, shown = '', data = null;
  const removing = new Map();
  const league = leaguePanel({ onChanged: loadProofs });
  const warning = h('div', { class: 'home-warning', hidden: true, role: 'status' });
  const health = h('p', { class: 'home-health' });
  const mode = h('p', { class: 'home-mode', hidden: !state.session?.demo }, 'Demo · synthetic household');
  const list = h('div', { class: 'send-list home-proofs', 'aria-busy': 'true' }, h('p', { class: 'tx2' }, 'Loading proof pictures…'));
  const errorBox = h('div', { class: 'form-error', role: 'alert' });
  const proofs = h('div', { class: 'home-proof-block' }, list);
  const sendGrid = h('div', { class: 'home-send-grid' }, league.sharing);
  fill(main, h('div', { class: 'page operator-home' }, mode, warning, league.summary, league.queue,
    h('section', { class: 'home-ready', 'aria-label': 'Ready to send' }, h('h2', {}, 'Ready to send'), errorBox,
      sendGrid),
    league.history, health));

  async function loadProofs() {
    if (!alive) return;
    try {
      data = await api('/api/proofs?ready_only=true');
      if (alive) { errorBox.textContent = ''; renderProofs(); }
    } catch (e) {
      if (!alive) return;
      list.removeAttribute('aria-busy');
      fill(errorBox, h('p', {}, `Could not refresh proof pictures. ${e.message}`),
        h('button', { class: 'btn', type: 'button', onclick: loadProofs }, 'Try again'));
    }
  }

  async function loadHealth() {
    try {
      const status = await api('/api/operator-status');
      if (!alive) return;
      renderHealth(status);
    } catch (e) {
      if (alive) {
        warning.hidden = false;
        fill(warning, h('p', {}, `Health unavailable. ${e.message}`), h('button', { type: 'button', class: 'btn', onclick: loadHealth }, 'Retry health'));
      }
    }
  }

  function renderHealth(status) {
    const demo = status.mode !== 'live', cam = status.camera;
    mode.hidden = !demo;
    mode.textContent = status.mode === 'demo' ? 'Demo · synthetic household' : 'Saved snapshot · not live';
    const stale = !status.analyzed_at || Date.parse(status.server_time) - Date.parse(status.analyzed_at) > 120000;
    const problem = demo ? null : !cam.connected ? 'Camera offline. New visits may be missing.'
      : cam.view?.state === 'moved' ? 'Camera moved. New dishes are paused.'
      : !status.vision_enabled ? 'Detection is off. New visits are paused.'
      : stale ? 'Analysis is behind. New visits may be delayed.' : null;
    warning.hidden = !problem;
    fill(warning, problem ? [icon('alert', 16), h('div', {}, h('p', {}, problem), h('a', { class: 'link', href: '#/setup' }, 'Check camera & detection'))] : []);
    fill(health, h('span', {}, demo ? status.mode === 'demo' ? 'Demo · synthetic data' : 'Saved snapshot' : cam.connected ? 'Camera live' : 'Camera offline'),
      h('span', {}, status.analyzed_at ? `Last analysed ${ago(status.analyzed_at)}` : 'No analysis yet'),
      h('span', {}, status.footage_hours === null ? 'Footage hours unavailable' : `≈${status.footage_hours.toFixed(1)} h footage saved`),
      h('a', { class: 'link', href: '#/live' }, 'Inspect dishes in Live'));
  }

  function renderProofs() {
    const items = data.items.filter(p => !removing.has(p.id));
    const sig = JSON.stringify([items, data.people]);
    if (sig === shown) return;
    shown = sig; list.removeAttribute('aria-busy');
    if (items.length) sendGrid.append(proofs);
    else proofs.remove();
    sendGrid.classList.toggle('has-proofs', items.length > 0);
    fill(list, items.map(card));
  }

  function card(p, index) {
    const send = sendButton(p.image_url, `${p.resident_name || 'dish'}-${p.dish}-${p.id}.jpg`.replace(/[^\w.-]+/g, '-'));
    const img = h('img', { src: p.image_url, alt: `${cap(p.dish)} left out at ${p.location || 'the kitchen'}: before, use, left there and later.`,
      ...PICTURE, loading: index ? 'lazy' : 'eager', decoding: 'async' });
    img.addEventListener('load', send.preload, { once: true });
    const who = h('div', { class: 'send-who' });
    const showNames = (all = false) => fill(who,
      h('p', { class: 'send-label' }, p.resident_id ? `Named as ${p.resident_name}` : 'Who used or left this dish?'),
      h('div', { class: 'name-chips' }, p.resident_id && !all ? h('button', { type: 'button', class: 'btn', onclick: () => showNames(true) }, 'Change name')
        : nameChips(data.people, { selected: p.resident_id, guess: p.guess_id, choose: (person, chip) => busy(chip, async () => {
          await act(p.id, { action: 'confirm', resident_id: person.id, note: 'Named from the proof picture' });
          await Promise.all([loadProofs(), league.refresh()]);
        }) })),
      p.guess_name && !p.resident_id ? h('p', { class: 'send-guess' }, guessSentence(p.guess_name, p.guess_basis)) : null,
      p.resident_id ? h('a', { class: 'send-record', href: `#/people/${p.resident_id}` }, 'Person’s proof history') : null);
    showNames();
    return h('article', { class: 'send-card', 'aria-label': `${cap(p.dish)} proof` },
      h('a', { class: 'send-photo', href: p.image_url, target: '_blank', rel: 'noopener', title: 'Open full size' }, img),
      h('div', { class: 'send-bar' }, send.el), who,
      h('div', { class: 'send-more' }, ...[['Not a dish', 'not_a_dish'], ['Wrong photos', 'wrong_photos']].map(([label, reason]) =>
        h('button', { type: 'button', class: 'btn quiet sm', onclick: () => remove(p, label, reason) }, label))));
  }

  function remove(p, label, reason) {
    if (removing.has(p.id)) return;
    let notice;
    const undo = () => {
      const entry = removing.get(p.id); if (!entry || entry.saving) return;
      clearTimeout(entry.timer); removing.delete(p.id); if (alive) renderProofs();
    };
    const commit = async () => {
      const entry = removing.get(p.id); if (!entry || entry.saving) return;
      entry.saving = true; clearTimeout(entry.timer); notice?.remove();
      try { await api(`/api/proofs/${p.id}/reject`, { method: 'POST', body: { reason }, keepalive: true }); await Promise.all([loadProofs(), league.refresh()]); }
      catch (e) { toast(e.message, { error: true }); }
      finally { removing.delete(p.id); if (alive) renderProofs(); }
    };
    removing.set(p.id, { timer: setTimeout(commit, 5000), commit, saving: false });
    notice = toast(label, { timeout: 5000, action: { label: 'Undo', run: undo } }); renderProofs();
  }
  const flush = () => { for (const entry of removing.values()) entry.commit(); };
  const hidden = () => { if (document.hidden) flush(); };
  document.addEventListener('visibilitychange', hidden);
  loadProofs(); loadHealth();
  const timer = setInterval(() => { if (!document.hidden) loadProofs(); }, 60000);
  return { update(kind) { if (kind === 'data' && state.data?.lean) renderHealth(state.data); }, destroy() {
    alive = false; clearInterval(timer); league.destroy(); document.removeEventListener('visibilitychange', hidden); flush();
  } };
}

export function placeholderCard() {
  return h('div', { class: 'send-card placeholder', 'aria-hidden': 'true' }, h('div', { class: 'send-photo' }), h('div', { class: 'send-bar' }, h('i')));
}
