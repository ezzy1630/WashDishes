// Home: one finished picture per dish still out past its deadline, ready to download and send.
// Found in the footage by the Jetson on its own; a picture leaves when its dish does.
import {act, api, busy, fill, h, icon} from '../core.js';

const REFRESH_MS = 60000;

export function proofPage(main) {
  let alive = true, shown = '';
  const status = h('p', {}, 'Loading…');
  const list = h('div', {class: 'proof-list'});
  const foot = h('p', {class: 'proof-foot tx3'});
  fill(main, h('div', {class: 'page proof-page'},
    h('header', {class: 'page-head'}, h('div', {}, h('h1', {id: 'page-title', tabindex: '-1'}, 'Dishes left out'), status)),
    list, foot));

  async function load() {
    try {
      const data = await api('/api/proofs');
      if (!alive) return;
      const coming = [data.checking && `Searching the footage for ${plural(data.checking, 'dish')} now.`,
        data.upcoming && `${plural(data.upcoming, 'more dish', 'more dishes')} ${data.upcoming === 1 ? 'is' : 'are'} out but not past two hours yet.`].filter(Boolean).join(' ');
      status.textContent = data.items.length
        ? `Found in the camera footage automatically. A picture disappears once its dish is gone. ${coming}`
        : coming || 'Nothing to send right now. When a dish sits out past its two hours, its picture shows up here on its own.';
      // Rebuilding unchanged cards would reload every picture once a minute.
      const signature = JSON.stringify([data.items, data.people]);
      if (signature !== shown) { shown = signature; fill(list, data.items.map(p => card(p, data.people))); }
      const g = data.guesses;
      foot.textContent = g.checked ? `Name guesses have been right ${g.correct} of ${g.checked} times. Every name you tap teaches it.`
        : 'Tap who it is on each picture; that teaches the camera to guess names.';
    } catch (e) { if (alive) status.textContent = `Could not load: ${e.message}`; }
  }

  function card(p, people) {
    const when = new Date(p.put_down_at).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'});
    const name = async (button, person) => busy(button, async () => {
      await act(p.id, {action: 'confirm', resident_id: person.id, note: 'Named from the proof picture'});
      await load();
    });
    const chips = people.map(person => {
      const chosen = p.resident_id === person.id, guessed = !p.resident_id && p.guess_id === person.id;
      const b = h('button', {type: 'button', class: `name-chip${chosen ? ' on' : ''}${guessed ? ' guess' : ''}`, 'aria-pressed': String(chosen),
        title: guessed ? 'The camera’s guess' : null}, person.name, guessed ? h('small', {}, 'guess') : null);
      b.onclick = () => chosen || name(b, person);
      return b;
    });
    const notDish = h('button', {type: 'button', class: 'btn quiet sm'}, 'Not a dish');
    notDish.onclick = () => busy(notDish, async () => { await act(p.id, {action: 'dismiss', note: 'Not a dish'}); await load(); });
    const wrong = h('button', {type: 'button', class: 'btn quiet sm'}, 'Wrong photos');
    wrong.onclick = () => busy(wrong, async () => { await api(`/api/proofs/${p.id}/reject`, {method: 'POST', body: {}}); await load(); });
    return h('article', {class: 'proof-card'},
      h('a', {href: p.image_url, target: '_blank', rel: 'noopener'}, h('img', {src: p.image_url, alt: `${p.dish} left out at ${p.location}`, loading: 'lazy', width: 1600, height: 1290})),
      h('div', {class: 'proof-bar'},
        h('div', {class: 'grow'}, h('strong', {}, p.resident_name ? `${cap(p.resident_name)}’s ${p.dish}` : cap(p.dish)), h('span', {class: 'tx2'}, ` · ${p.location || 'Kitchen'} · put down ${when}`)),
        h('a', {class: 'btn primary', href: p.image_url, download: `dish-${p.id}${p.resident_name ? '-' + p.resident_name : ''}.jpg`}, icon('download', 15), 'Download')),
      h('div', {class: 'proof-names'}, h('span', {class: 'tx2'}, p.resident_name ? 'Named:' : 'Who is it?'), chips),
      h('div', {class: 'proof-more'}, notDish, wrong));
  }

  load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  return {update() {}, destroy() { alive = false; clearInterval(timer); }};
}

const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const plural = (n, one, many = `${one}es`) => `${n} ${n === 1 ? one : many}`;
