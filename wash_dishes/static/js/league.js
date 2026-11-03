// Weekly points stay independent of the older proof totals and camera-quality scoreboard.
import { api, avatar, busy, field, fill, form, h, toast } from './core.js';
import { nameChips } from './name-chips.js';

export function leaguePanel() {
  let alive = true, selected = '', request = 0;
  const pending = new Map();
  const content = h('div', { 'aria-live': 'polite' }, h('p', { class: 'tx2' }, 'Loading this week…'));
  const root = h('section', { class: 'league', 'aria-label': 'Weekly kitchen duty' }, content);
  const label = value => new Date(`${value}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' });

  async function load() {
    if (pending.size) return;
    const turn = ++request;
    try {
      const data = await api(`/api/league/week${selected ? `?start=${selected}` : ''}`);
      if (!alive || turn !== request || pending.size) return;
      render(data);
    } catch (error) {
      if (!alive || turn !== request || pending.size) return;
      fill(content, h('p', { class: 'form-error' }, `Could not load the week. ${error.message}`),
        h('button', { class: 'btn', type: 'button', onclick: load }, 'Try again'));
    }
  }

  function render(data) {
    const open = [...content.querySelectorAll('details[open]')].map(el => el.dataset.visitId || el.className);
    const { week, rules, on_duty: duty, standings, visits } = data;
    const current = week.start === data.weeks[0].start;
    const choose = h('select', { 'aria-label': 'Week', onchange: e => { selected = e.target.value; load(); } },
      data.weeks.map(w => h('option', { value: w.start, selected: w.start === week.start }, `${label(w.start)}${w.closed ? ' · closed' : ''}`)));
    const dishList = h('details', { class: 'league-dishes', 'data-visit-id': 'visits' },
      h('summary', {}, `Visits this week (${visits.length})`),
      h('div', { class: 'league-dish-list' }, visits.length ? visits.map(d => dishRow(d, standings, week, visits, data.dishes))
        : h('p', { class: 'tx2' }, 'No verified late dishes this week. Dishes with an unverified deadline do not count.')));
    const jump = () => { dishList.open = true; dishList.scrollIntoView({ block: 'start', behavior: 'instant' }); };
    fill(content,
      h('div', { class: 'league-heading' }, h('h2', {}, current ? 'This week' : `Week of ${label(week.start)}`), field('Week of', choose)),
      h('p', { class: 'league-pick' }, data.pick.reason),
      h('p', { class: 'league-duty' }, duty ? `${duty.name} is on kitchen duty. ${duty.why}`
        : rules.enabled ? 'Nobody is on kitchen duty yet.' : 'Kitchen duty is off. Standings still count.'),
      h('ol', { class: 'league-standings', 'aria-label': 'Weekly points' }, standings.filter(p => p.points > 0).map(p => h('li', {},
        avatar(p.resident_id, p.name), h('a', { href: `#/people/${p.resident_id}` }, p.name),
        h('span', { class: 'league-points' }, h('b', {}, String(p.points)), ` ${p.points === 1 ? 'point' : 'points'}`)))),
      standings.some(p => !p.points) ? h('p', { class: 'league-note league-zero' }, `${standings.some(p => p.points) ? 'Everyone else' : 'Everyone'} 0 — ${standings.filter(p => !p.points).map(p => p.name).sort((a, b) => a.localeCompare(b)).join(', ')}`) : null,
      h('div', { class: 'league-foot' },
        h('p', {}, `${data.unreviewed_count} ${data.unreviewed_count === 1 ? 'visit needs' : 'visits need'} an answer.`),
        data.unreviewed_count && !week.closed ? h('button', { type: 'button', class: 'link', onclick: jump }, 'Review or name them') : null),
      h('p', { class: 'league-note' }, `Pan/pot ${rules.weights.pan === rules.weights.pot ? rules.weights.pan : `${rules.weights.pan}/${rules.weights.pot}`} · other ${rules.weights.default} pt. Pick: ${rules.min_points} pts, ${rules.min_lead} ahead.`),
      h('p', { class: 'league-note' }, week.closed ? 'Closed. Names, disputes and points are frozen.'
        : 'Mon–Sun · closes Mon noon. No pick? Duty stays.'),
      dishList,
      data.verdicts.length ? h('details', { class: 'league-dishes', 'data-visit-id': 'reviewed' }, h('summary', {}, `Reviewed answers (${data.verdicts.length})`),
        ...data.verdicts.map(v => h('div', { class: 'league-verdict' },
          h('p', {}, v.title), h('p', { class: 'league-note' }, `${v.by?.name || 'The operator'}: ${v.verdict === 'not_dish' ? 'Not a dish' : v.verdict === 'clean' ? 'It’s clean' : 'Same dish as another'} · ${new Date(v.at).toLocaleString()}`),
          !week.closed ? h('button', { type: 'button', class: 'btn sm', onclick: e => busy(e.currentTarget, async () => {
            await api(`/api/league/visits/${encodeURIComponent(v.visit_id)}/undo-verdict`, { method: 'POST', body: {} }); toast('Answer reversed.'); await load();
            const restored = [...content.querySelectorAll('.league-dish')].find(el => el.dataset.visitId === v.visit_id);
            if (restored) { restored.open = true; restored.closest('.league-dishes').open = true; }
          }) }, 'Undo answer') : null))) : null);
    content.querySelectorAll('details').forEach(el => { if (open.includes(el.dataset.visitId || el.className)) el.open = true; });
  }

  function dishRow(dish, residents, week, visits, dishes) {
    const image = dish.filmstrip_url || dish.photo_url;
    const controls = h('div', { class: 'stack' });
    if (!week.closed) controls.append(h('p', { class: 'tx2' }, dish.arrival_seen ? 'Name someone only if you know they used or left these dishes. Being nearby is not enough.' : 'Arrival not recorded — only name someone if you know'));
    if (dish.can_settle) {
      controls.append(h('p', { class: 'tx2' }, `${dish.dispute?.by?.name || dish.resident?.name || 'The named person'} says “That wasn’t me.”${dish.dispute?.note ? ` ${dish.dispute.note}` : ''}`),
        h('div', { class: 'row-actions' }, ...[['them', 'It was them'], ['not_them', 'Not them']].map(([decision, text]) =>
          h('button', { type: 'button', class: 'btn', onclick: e => busy(e.currentTarget, async () => {
            await api(`/api/league/visits/${encodeURIComponent(dish.id)}/settle`, { method: 'POST', body: { decision } }); toast('Dispute settled.'); await load();
          }) }, text))));
    } else if (dish.can_name) {
      const people = residents.map(p => ({ id: p.resident_id, name: p.name }));
      const naming = h('div', { class: 'send-who' });
      const showNames = (all = false, selected = dish.resident?.id) => {
        const chips = nameChips(people, { selected, guess: dish.guess?.resident_id,
          choose: person => queue(dish, 'name', { resident_id: person.id }, controls, () => showNames(false, person.id)) });
        fill(naming, h('p', { class: 'send-label' }, selected ? 'Named as' : 'Who put it down?'),
          h('div', { class: 'name-chips' }, selected && !all ? [chips.find(c => c.classList.contains('on')),
            h('button', { type: 'button', class: 'btn quiet sm', onclick: () => showNames(true, selected) }, 'Change')] : chips),
          dish.guess && !selected ? h('p', { class: 'send-guess' }, `Looks like ${dish.guess.name}: ${dish.guess.basis}`) : null);
      };
      showNames(); controls.append(naming);
    }

    if (!week.closed) {
      const answer = verdict => queue(dish, 'verdict', { verdict }, controls);
      const duplicate = form(async fd => {
        queue(dish, 'verdict', { verdict: 'duplicate', of_visit_id: fd.get('of_visit_id') }, controls);
      }, { submit: 'Save duplicate', kind: '' });
      duplicate.append(field('Which other visit shows the same dish?', h('select', { name: 'of_visit_id', required: true },
        h('option', { value: '' }, 'Choose the original visit or dish'), ...visits.filter(v => v.id !== dish.id).flatMap(v => [
          h('option', { value: v.id }, `${v.title} · ${v.area || 'Kitchen'} · ${new Date(v.at).toLocaleString()}`),
          ...(v.dish_ids.length > 1 ? v.dish_ids.map(id => h('option', { value: `dish-${id}` }, `Only ${dishes.find(d => d.id === id)?.title || 'this dish'} · ${v.area || 'Kitchen'} · ${new Date(v.at).toLocaleString()}`)) : []),
        ]))));
      duplicate.finish();
      const picker = h('div', { class: 'league-duplicate', hidden: true }, duplicate);
      const duplicateButton = h('button', { type: 'button', class: 'btn', 'aria-expanded': 'false', onclick: () => {
        picker.hidden = !picker.hidden;
        duplicateButton.setAttribute('aria-expanded', String(!picker.hidden));
        if (!picker.hidden) picker.querySelector('select').focus();
      } }, 'Same as another');
      controls.append(h('div', { class: 'divider' }), h('p', { class: 'tx2' }, 'Something else?'),
        h('div', { class: 'league-verdict-buttons' },
          ...[['not_dish', 'Not a dish'], ['clean', 'It’s clean']].map(([verdict, label]) =>
            h('button', { type: 'button', class: 'btn', onclick: () => answer(verdict) }, label)),
          duplicateButton, h('button', { type: 'button', class: 'btn', onclick: () => answer('cant_tell') }, 'Can’t tell')),
        picker, h('p', { class: 'league-note' }, 'Can’t tell hides this just for you, without a label. Other answers remove it for everyone.'));

    }
    return h('details', { class: 'league-dish', 'data-visit-id': dish.id },
      h('summary', {}, h('span', {}, dish.title, h('small', {}, `${dish.area || 'Kitchen'} · ${dish.resident?.name || 'Unnamed'}${dish.dispute?.state === 'open' ? ' · disputed' : ''}`)),
        h('span', {}, `${dish.points} pt`)),
      h('div', { class: 'stack' }, image ? h('a', { href: image, target: '_blank', rel: 'noopener' },
        h('img', { class: 'league-photo', src: image, width: dish.filmstrip_url ? dish.filmstrip_width : dish.photo_width, height: dish.filmstrip_url ? dish.filmstrip_height : dish.photo_height, loading: 'lazy', alt: dish.filmstrip_url ? 'Arrival filmstrip' : 'Saved dish photo' })) : h('p', { class: 'tx2' }, 'No photo is kept for this dish.'),
      !dish.arrival_seen ? h('p', { class: 'league-note' }, `Already there at ${new Date(dish.at).toLocaleString()} — arrival not recorded. The cleanup follow-up starts at first observation.`) : null,
      dish.named_at ? h('p', { class: 'league-note' }, `Named by ${dish.named_by?.name || 'the operator'} on ${new Date(dish.named_at).toLocaleString()}.`) : null,
      week.closed ? h('p', { class: 'league-note' }, 'This week is closed.') : controls));
  }

  // Like Send, allow five seconds to undo a tap; flush on hide or navigation.
  function queue(dish, action, body, controls, selected) {
    if (pending.has(dish.id)) return;
    let notice;
    const undo = () => { clearTimeout(pending.get(dish.id)?.timer); pending.delete(dish.id); notice?.remove(); load(); };
    const commit = async () => {
      const entry = pending.get(dish.id);
      if (!entry) return;
      clearTimeout(entry.timer); pending.delete(dish.id); notice?.remove();
      try {
        await api(`/api/league/visits/${encodeURIComponent(dish.id)}/${action}`, { method: 'POST', body, keepalive: true });
        toast(action === 'name' ? 'Name saved.' : body.verdict === 'cant_tell' ? 'Skipped for you. No label saved.' : 'Answer saved.');
      } catch (error) { toast(error.message); }
      if (alive) await load();
    };
    controls.querySelectorAll('button, select').forEach(el => { el.disabled = true; });
    if (selected) selected();
    controls.querySelectorAll('button, select').forEach(el => { el.disabled = true; });
    controls.append(h('p', { class: 'league-note' }, action === 'name' ? 'Saving name…' : 'Saving answer…'),
      h('button', { type: 'button', class: 'btn', onclick: undo }, 'Undo'));
    pending.set(dish.id, { timer: setTimeout(commit, 5000), commit });
    notice = toast(action === 'name' ? 'Name selected.' : 'Answer selected.', { timeout: 5000, action: { label: 'Undo', run: undo } });
  }
  const flush = () => { for (const entry of [...pending.values()]) entry.commit(); };
  const hidden = () => { if (document.hidden) flush(); };
  document.addEventListener('visibilitychange', hidden);

  load();
  const timer = setInterval(() => { if (!document.hidden && !pending.size) load(); }, 60000);
  return { el: root, refresh: load, destroy() { alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', hidden); flush(); } };
}
