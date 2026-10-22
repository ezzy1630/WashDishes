// Home: one finished picture per dish still out past its deadline, ready to send to the house chat.
// Found in the footage by the Jetson on its own; a picture leaves when its dish does.
import { act, api, busy, cap, fill, h, icon, toast } from '../core.js';
import { sendButton } from '../share.js';

const REFRESH_MS = 60000, UNDO_MS = 5000;
// Rendered picture size (collage.py), so the page keeps its layout while pictures load.
export const PICTURE = { width: 1600, height: 1590 };

export function proofPage(main) {
  let alive = true, shown = '', data = null;
  const removing = new Map();  // proof id -> {timer, commit} while its Undo is offered
  const title = h('h1', { id: 'page-title', tabindex: '-1' }, 'Dishes left out');
  const sub = h('p', { class: 'send-sub' });
  const list = h('div', { class: 'send-list', 'aria-busy': 'true' }, placeholderCard());
  const foot = h('p', { class: 'send-foot' });
  fill(main, h('div', { class: 'page send-page' }, h('header', { class: 'send-head' }, title, sub), list, foot));

  async function load() {
    try {
      data = await api('/api/proofs');
      if (alive) render();
    } catch (e) {
      if (!alive || data) return;
      list.removeAttribute('aria-busy');
      fill(list, h('div', { class: 'send-empty' }, h('p', {}, `Could not load the pictures. ${e.message}`),
        h('button', { type: 'button', class: 'btn', onclick: load }, icon('refresh', 15), h('span', {}, 'Try again'))));
    }
  }

  function render() {
    const items = data.items.filter(p => !removing.has(p.id));
    title.textContent = items.length ? `${items.length} ${items.length === 1 ? 'dish' : 'dishes'} left out` : 'Nothing to send';
    sub.textContent = items.length ? coming(data) : '';
    const g = data.guesses;
    foot.textContent = g.checked ? `Name guesses have been right ${g.correct} of ${g.checked} times. Each name you tap teaches it.`
      : items.length ? 'Tap who put it down; each name you tap teaches the camera to guess.' : '';
    // Rebuilding unchanged cards would reload every picture once a minute.
    const signature = JSON.stringify([items, data.people]);
    if (signature === shown) return;
    shown = signature;
    list.removeAttribute('aria-busy');
    fill(list, items.length ? items.map((p, i) => card(p, i)) : nothingYet(data));
  }

  function card(p, index) {
    const named = p.resident_id != null;
    const send = sendButton(p.image_url, `${p.resident_name || 'dish'}-${p.dish}-${p.id}.jpg`.replace(/[^\w.-]+/g, '-'));
    const img = h('img', { src: p.image_url, alt: `${cap(p.dish)} left out at ${p.location || 'the kitchen'}: before, being put down, left there, and still there later.`,
      ...PICTURE, loading: index ? 'lazy' : 'eager', fetchpriority: index ? null : 'high', decoding: 'async' });
    img.addEventListener('load', send.preload, { once: true });
    const tally = named && data.people.find(x => x.id === p.resident_id);
    const who = h('div', { class: 'send-who', role: 'group', 'aria-label': 'Who put it down?' });
    const showChips = (all) => fill(who,
      h('p', { class: 'send-label' }, named ? 'Put down by' : 'Who put it down?'),
      h('div', { class: 'name-chips' }, all ? nameChips(p) : [nameChips(p).find(c => c.classList.contains('on')) || h('span', { class: 'name-chip on' }, p.resident_name),
        h('button', { type: 'button', class: 'btn quiet sm', onclick: () => showChips(true) }, 'Change')]),
      named ? tally && h('a', { class: 'send-record', href: `#/people/${tally.id}` },
        `${cap(tally.name)} has ${tally.count} ${tally.count === 1 ? 'dish' : 'dishes'} on record`, icon('chevronRight', 14))
        : p.guess_name ? h('p', { class: 'send-guess' }, p.guess_basis ? `Looks like ${cap(p.guess_name)}: ${p.guess_basis.replace(/\.$/, '')}.` : `The camera guesses ${cap(p.guess_name)}.`) : null);
    showChips(!named);
    return h('article', { class: 'send-card', 'aria-label': named ? `${cap(p.resident_name)}'s ${p.dish}` : cap(p.dish) },
      h('a', { class: 'send-photo', href: p.image_url, target: '_blank', rel: 'noopener', title: 'Open full size' }, img),
      h('div', { class: 'send-bar' }, send.el),
      who,
      h('div', { class: 'send-more' }, ...[['not_a_dish', 'Not a dish'], ['wrong_photos', 'Wrong photos']].map(([reason, label]) =>
        h('button', { type: 'button', class: 'btn quiet sm', onclick: () => remove(p, reason, label) }, label))));
  }

  // Housemates in a fixed order, so a name is always in the same place; the camera's guess outlined.
  function nameChips(p) {
    const named = p.resident_id != null;
    return [...data.people].sort((a, b) => a.name.localeCompare(b.name)).map(person => {
      const chosen = p.resident_id === person.id, guessed = !named && p.guess_id === person.id;
      const chip = h('button', { type: 'button', class: `name-chip${chosen ? ' on' : ''}${guessed ? ' guess' : ''}`, 'aria-pressed': String(chosen) },
        person.name, guessed ? h('small', {}, 'guess') : null);
      chip.addEventListener('click', () => chosen || busy(chip, async () => {
        await act(p.id, { action: 'confirm', resident_id: person.id, note: 'Named from the proof picture' });
        await load();
      }));
      return chip;
    });
  }

  // Removing is undoable for a few seconds: a mis-tap on a phone should not lose a proof. Leaving
  // the page or switching apps sends it at once, since a phone may freeze or close a hidden tab.
  function remove(p, reason, label) {
    const commit = () => {
      removing.delete(p.id);
      api(`/api/proofs/${p.id}/reject`, { method: 'POST', body: { reason }, keepalive: true }).then(() => alive && load(), e => toast(e.message, { error: true }));
    };
    removing.set(p.id, { timer: setTimeout(commit, UNDO_MS), commit });
    render();
    toast(`${label}: picture removed.`, { timeout: UNDO_MS, action: { label: 'Undo', run: () => { clearTimeout(removing.get(p.id)?.timer); removing.delete(p.id); shown = ''; render(); } } });
  }

  const flush = () => { for (const { timer: pending, commit } of [...removing.values()]) { clearTimeout(pending); commit(); } };
  const hidden = () => { if (document.hidden) flush(); };
  document.addEventListener('visibilitychange', hidden);
  load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  return { update() {}, destroy() { alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', hidden); flush(); } };
}

// What else is on its way, under the pictures already here.
function coming(data) {
  return [data.checking && `Checking ${data.checking} more in the footage now.`,
    data.upcoming && `${data.upcoming} more out, not two hours yet.`].filter(Boolean).join(' ');
}

function nothingYet(data) {
  const rows = [[data.checking, 'being checked in the footage now'], [data.upcoming, 'out, but not for two hours yet']].filter(([n]) => n);
  return h('section', { class: 'send-empty' },
    rows.length ? h('ul', { class: 'send-coming' }, rows.map(([n, text]) => h('li', {}, h('b', {}, `${n} ${n === 1 ? 'dish' : 'dishes'}`), ` ${text}`)))
      : h('p', { class: 'send-coming' }, 'No dishes are out right now.'),
    h('p', {}, 'When a dish sits out for two hours, the camera finds who put it down and its picture shows up here by itself.'));
}

export function placeholderCard() {
  return h('div', { class: 'send-card placeholder', 'aria-hidden': 'true' }, h('div', { class: 'send-photo' }), h('div', { class: 'send-bar' }, h('i')));
}
