// People: who has left dishes out, counted from the proof pictures someone named, and one tap
// into a person for each of their pictures, newest first ("click into Sam's 9").
import { api, avatar, cap, fill, h, icon } from '../core.js';
import { time } from '../photo-ui.js';
import { sendButton } from '../share.js';
import { PICTURE, placeholderCard } from './proof.js';

export function ledgerPage(main, route) {
  return route.sub ? record(main, Number(route.sub)) : everyone(main);
}

function everyone(main) {
  let alive = true;
  const list = h('ol', { class: 'tally', 'aria-busy': 'true' }, Array.from({ length: 4 }, () => h('li', { class: 'tally-row placeholder', 'aria-hidden': 'true' })));
  const note = h('div', { class: 'tally-note' });
  fill(main, h('div', { class: 'page people-page' },
    h('header', { class: 'send-head' }, h('h1', { id: 'page-title', tabindex: '-1' }, 'People'),
      h('p', { class: 'send-sub' }, 'Dishes left out, from the pictures you named.')), list, note));
  api('/api/people').then(({ people }) => {
    if (!alive) return;
    list.removeAttribute('aria-busy');
    // Only people with a named picture get a row; the rest would be a column of zeros.
    const named = people.filter(p => p.count), rest = people.filter(p => !p.count);
    fill(list, named.map(p => h('li', {}, h('a', { class: 'tally-row', href: `#/people/${p.id}` },
      avatar(p.id, p.name, 'lg'),
      h('span', { class: 'tally-name' }, cap(p.name), h('small', {}, `Last one ${day(p.last_at)}`)),
      h('b', { class: 'tally-count', 'aria-label': `${p.count} ${p.count === 1 ? 'dish' : 'dishes'}` }, String(p.count)),
      icon('chevronRight', 16)))));
    fill(note, named.length ? null : h('p', {}, 'Nobody has been named yet. On Send, tap who put each dish down.'),
      rest.length ? h('p', {}, `${named.length ? 'No named pictures yet' : 'Everyone'}: ${rest.map(p => cap(p.name)).join(', ')}.`) : null);
  }, e => { if (alive) { list.removeAttribute('aria-busy'); fill(list, h('li', { class: 'send-empty' }, `Could not load people. ${e.message}`)); } });
  return { update() {}, destroy() { alive = false; } };
}

function record(main, id) {
  let alive = true;
  const title = h('h1', { id: 'page-title', tabindex: '-1' }, 'Loading…');
  const sub = h('p', { class: 'send-sub' });
  const face = h('span');
  const list = h('div', { class: 'send-list' }, placeholderCard());
  const older = h('div');
  fill(main, h('div', { class: 'page send-page' },
    h('a', { class: 'crumb', href: '#/people' }, icon('arrowLeft', 13), 'People'),
    h('header', { class: 'send-head record-head' }, face, h('div', {}, title, sub)), list, older));
  api(`/api/people/${id}`).then(r => {
    if (!alive) return;
    face.replaceChildren(avatar(r.id, r.name, 'lg'));
    title.textContent = cap(r.name);
    sub.textContent = r.count ? `${r.count} ${r.count === 1 ? 'dish' : 'dishes'} left out` : 'Nothing named yet';
    const shown = r.items.filter(i => i.image_url), gone = r.items.filter(i => !i.image_url);
    fill(list, shown.length ? shown.map((item, i) => card(item, i, r.name))
      : r.count ? null : h('div', { class: 'send-empty' }, h('p', {}, `When you tap ${cap(r.name)} on a picture in Send, it shows up here.`)));
    fill(older, gone.length ? h('section', { class: 'record-older' },
      h('h2', {}, shown.length ? 'Earlier' : 'Pictures'), h('p', { class: 'send-sub' }, 'Pictures are only kept for a week.'),
      h('ul', {}, gone.map(i => h('li', {}, h('b', {}, cap(i.dish)), h('span', {}, i.location), h('time', { datetime: i.put_down_at }, time(i.put_down_at)))))) : null);
  }, e => {
    if (!alive) return;
    title.textContent = e.status === 404 ? 'No one by that name' : 'Could not load';
    fill(list, h('div', { class: 'send-empty' }, h('p', {}, e.message), h('a', { class: 'btn', href: '#/people' }, 'See everyone')));
  });
  return { update() {}, destroy() { alive = false; } };
}

function card(item, index, name) {
  const send = sendButton(item.image_url, `${name}-${item.dish}-${item.id}.jpg`.replace(/[^\w.-]+/g, '-'), { kind: '' });
  const img = h('img', { src: item.image_url, alt: `${cap(name)} left a ${item.dish} out at ${item.location}.`, ...PICTURE,
    loading: index ? 'lazy' : 'eager', decoding: 'async' });
  img.addEventListener('load', send.preload, { once: true });
  return h('article', { class: 'send-card' },
    h('a', { class: 'send-photo', href: item.image_url, target: '_blank', rel: 'noopener', title: 'Open full size' }, img),
    h('div', { class: 'send-bar' }, h('time', { class: 'send-when', datetime: item.put_down_at }, time(item.put_down_at)), send.el));
}

// "today", "yesterday", "Tuesday", then "Oct 14": a list of people only needs the day.
function day(v) {
  const d = new Date(v), days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(v).setHours(0, 0, 0, 0)) / 864e5);
  return days === 0 ? 'today' : days === 1 ? 'yesterday' : days < 7 ? `on ${d.toLocaleDateString([], { weekday: 'long' })}`
    : `on ${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
}
