import { nameChips as residentChips } from '../name-chips.js';
// Home: one finished picture per dish still out past its deadline, ready to send to the house chat.
// Found in the footage by the Jetson on its own; a picture leaves when its dish does. Dishes still out
// whose put-down it could not find retain an explicitly incomplete four-stage view.
import { act, api, busy, cap, fill, h, icon, toast, when } from '../core.js';
import { sendButton } from '../share.js';

const REFRESH_MS = 60000, UNDO_MS = 5000;
// Rendered picture sizes (collage.py), so the page keeps its layout while pictures load.
export const PICTURE = { width: 1600, height: 1572 };

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
      if (!alive) return;
      if (data) { sub.textContent = `Could not refresh the kitchen. These are the last saved results. ${e.message}`; return; }
      list.removeAttribute('aria-busy');
      fill(list, h('div', { class: 'send-empty' }, h('p', {}, `Could not load the pictures. ${e.message}`),
        h('button', { type: 'button', class: 'btn', onclick: load }, icon('refresh', 15), h('span', {}, 'Try again'))));
    }
  }

  function render() {
    const items = data.items.filter(p => !removing.has(p.id)), out = data.out.filter(p => !removing.has(p.id));
    const total = items.length + out.length;
    title.textContent = `${items.length} complete ${items.length === 1 ? 'collage' : 'collages'}`;
    sub.textContent = coming(data);
    const g = data.guesses;
    foot.textContent = g.checked ? `Name guesses have been right ${g.correct} of ${g.checked} times. Each name you tap teaches it.`
      : '';
    // Rebuilding unchanged cards would reload every picture once a minute.
    const signature = JSON.stringify([items, out, data.people, total ? null : [data.checking, data.upcoming]]);
    if (signature === shown) return;
    shown = signature;
    list.removeAttribute('aria-busy');
    fill(list, total ? [...items.map((p, i) => card(p, i)),
      out.length ? h('div', { class: 'send-section' }, h('h2', {}, `${out.length} incomplete ${out.length === 1 ? 'sequence' : 'sequences'}`),
        h('p', {}, 'These dishes were seen after their deadline. The evidence does not yet establish who left them.')) : null,
      ...out.map(incompleteCard)] : nothingYet(data));
  }

  function incompleteCard(p) {
    const stages = [['before', 'Before'], ['using', 'Use / handling'], ['leaving', 'Left there'], ['later', 'Later']];
    return h('article', { class: 'send-card incomplete', 'aria-label': `${cap(p.dish)}: incomplete evidence` },
      h('div', {}, h('h3', {}, `${cap(p.dish)} · ${p.location || 'Kitchen'}`),
        h('p', { class: 'send-label' }, p.resident_name ? `Named by a person: ${cap(p.resident_name)}` : 'Person not identified')),
      h('div', { class: 'evidence-grid' }, stages.map(([key, label]) => {
        const stage = (p.stages || []).find(s => s.key === key);
        const photo = stage?.url ? h('a', { href: `#/incident/${p.id}`, 'aria-label': `Inspect ${label.toLowerCase()} photo` },
          h('img', { src: stage.url, alt: `${label}: saved camera view; inspect the dish in the full record`, loading: 'lazy', decoding: 'async' }))
          : h('p', { class: 'evidence-missing' }, 'Photo unavailable');
        photo.querySelector?.('img')?.addEventListener('error', () => fill(photo, h('p', { class: 'evidence-missing' }, 'Photo unavailable')), { once: true });
        return h('figure', {}, photo, h('figcaption', {}, h('b', {}, stage?.title || label),
          h('span', {}, stage?.at && stage?.url ? when(stage.at) : 'Missing evidence')));
      })),
      h('p', { class: 'send-label' }, searchExplanation(p)),
      h('a', { class: 'btn', href: `#/incident/${p.id}` }, 'Inspect photos & video', icon('arrowRight', 15)));
  }

  function card(p, index) {
    const named = p.resident_id != null;
    const send = sendButton(p.image_url, `${p.resident_name || 'dish'}-${p.dish}-${p.id}.jpg`.replace(/[^\w.-]+/g, '-'));
    const img = h('img', { src: p.image_url, alt: `${cap(p.dish)} left out at ${p.location || 'the kitchen'}: before, being put down, left there, and still there later.`,
      ...PICTURE, loading: index ? 'lazy' : 'eager', fetchpriority: index ? null : 'high', decoding: 'async' });
    img.addEventListener('load', send.preload, { once: true });
    const tally = named && data.people.find(x => x.id === p.resident_id);
    const ask = 'Who put it down?';
    const who = h('div', { class: 'send-who', role: 'group', 'aria-label': ask });
    const showChips = (all) => fill(who,
      h('p', { class: 'send-label' }, named ? 'Put down by' : ask),
      h('div', { class: 'name-chips' }, all ? nameChips(p) : [nameChips(p).find(c => c.classList.contains('on')) || h('span', { class: 'name-chip on' }, p.resident_name),
        h('button', { type: 'button', class: 'btn quiet sm', onclick: () => showChips(true) }, 'Change')]),
      named ? tally && h('a', { class: 'send-record', href: `#/people/${tally.id}` },
        `${cap(tally.name)} has ${tally.count} ${tally.count === 1 ? 'dish' : 'dishes'} on record`, icon('chevronRight', 14))
        : p.guess_name ? h('p', { class: 'send-guess' }, p.guess_basis ? `Looks like ${cap(p.guess_name)}: ${p.guess_basis.replace(/\.$/, '')}.` : `The camera guesses ${cap(p.guess_name)}.`) : null);
    showChips(!named);
    // A wrong proof leaves the dish's record open; "Not a dish" closes it, whichever card it is on.
    const reject = reason => () => api(`/api/proofs/${p.id}/reject`, { method: 'POST', body: { reason }, keepalive: true });
    const actions = [['Not a dish', reject('not_a_dish')], ['Wrong photos', reject('wrong_photos')]];
    return h('article', { class: 'send-card', 'aria-label': named ? `${cap(p.resident_name)}'s ${p.dish}` : cap(p.dish) },
      h('a', { class: 'send-photo', href: p.image_url, target: '_blank', rel: 'noopener', title: 'Open full size' }, img),
      h('div', { class: 'send-bar' }, send.el),
      who,
      h('div', { class: 'send-more' }, ...actions.map(([label, commit]) =>
        h('button', { type: 'button', class: 'btn quiet sm', onclick: () => remove(p, label, commit) }, label))));
  }

  // Housemates in a fixed order, so a name is always in the same place; the camera's guess outlined.
  function nameChips(p) {
    return residentChips(data.people, { selected: p.resident_id, guess: p.guess_id,
      choose: (person, chip) => busy(chip, async () => {
        await act(p.id, { action: 'confirm', resident_id: person.id, note: 'Named from the proof picture' });
        await load();
      }) });
  }

  // Removing is undoable for a few seconds: a mis-tap on a phone should not lose a proof. Leaving
  // the page or switching apps sends it at once, since a phone may freeze or close a hidden tab.
  function remove(p, label, send) {
    const commit = () => {
      removing.delete(p.id);
      send().then(() => alive && load(), e => toast(e.message, { error: true }));
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
  const c = data.kitchen?.available && data.kitchen.counts;
  return [c ? `${c.dirty + c.sink} dishes tracked: ${c.dirty} on counters or stove, ${c.sink} in the sink. Stacked dishes can be missed.`
    : 'Current kitchen count unavailable.',
    data.checking && `Searching footage for ${data.checking} ${data.checking === 1 ? 'dish' : 'dishes'}.`,
    data.upcoming && `${data.upcoming} tracked ${data.upcoming === 1 ? 'dish has' : 'dishes have'} not reached the ${data.deadline_minutes}-minute deadline.`].filter(Boolean).join(' ');
}

function nothingYet(data) {
  const rows = [[data.checking, 'being checked in the footage now'], [data.upcoming, 'not yet at the cleanup deadline']].filter(([n]) => n);
  return h('section', { class: 'send-empty' },
    rows.length ? h('ul', { class: 'send-coming' }, rows.map(([n, text]) => h('li', {}, h('b', {}, `${n} ${n === 1 ? 'dish' : 'dishes'}`), ` ${text}`)))
      : h('p', { class: 'send-coming' }, 'No complete evidence sequences available.'),
    h('p', {}, 'A complete collage needs four supported views: before, handling, left there, and later. Missing evidence stays marked.'),
    h('a', { class: 'btn', href: '#/live' }, 'Check the kitchen', icon('arrowRight', 15)));
}

export function searchExplanation(p) {
  if (p.search_status === 'queued') return 'Waiting for the footage search.';
  if (p.search_status === 'waiting') return 'Waiting for a newer, clear view of this dish.';
  if (p.search_status === 'rejected') return 'The previous photos were marked wrong.';
  const reason = p.search_reason || '';
  if (/gap|cannot resolve|could not read/.test(reason)) return 'Recording gaps prevented a complete sequence.';
  if (/after|settle|handling finished/.test(reason)) return 'The search could not verify a clear view after the person left.';
  if (/before|already (there|present)/.test(reason)) return 'No verified view from before this dish arrived.';
  if (/time budget/.test(reason)) return 'The footage search ran out of time.';
  return 'The footage search could not establish a complete sequence.';
}

export function placeholderCard() {
  return h('div', { class: 'send-card placeholder', 'aria-hidden': 'true' }, h('div', { class: 'send-photo' }), h('div', { class: 'send-bar' }, h('i')));
}
