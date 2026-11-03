// Weekly points stay independent of the older proof totals and camera-quality scoreboard.
import { api, avatar, busy, field, fill, form, h, toast } from './core.js';

export function leaguePanel() {
  let alive = true, selected = '', request = 0;
  const content = h('div', { 'aria-live': 'polite' }, h('p', { class: 'tx2' }, 'Loading this week…'));
  const root = h('section', { class: 'league', 'aria-label': 'Weekly kitchen duty' }, content);
  const label = value => new Date(`${value}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' });

  async function load() {
    const turn = ++request;
    try {
      const data = await api(`/api/league/week${selected ? `?start=${selected}` : ''}`);
      if (!alive || turn !== request) return;
      render(data);
    } catch (error) {
      if (!alive || turn !== request) return;
      fill(content, h('p', { class: 'form-error' }, `Could not load the week. ${error.message}`),
        h('button', { class: 'btn', type: 'button', onclick: load }, 'Try again'));
    }
  }

  function render(data) {
    const { week, rules, on_duty: duty, standings, visits } = data;
    const current = week.start === data.weeks[0].start;
    const choose = h('select', { 'aria-label': 'Week', onchange: e => { selected = e.target.value; load(); } },
      data.weeks.map(w => h('option', { value: w.start, selected: w.start === week.start }, `${label(w.start)}${w.closed ? ' · closed' : ''}`)));
    const dishList = h('details', { class: 'league-dishes' },
      h('summary', {}, `Visits this week (${visits.length})`),
      h('div', { class: 'league-dish-list' }, visits.length ? visits.map(d => dishRow(d, standings, week, visits, data.dishes))
        : h('p', { class: 'tx2' }, 'No verified late dishes this week. Dishes with an unverified deadline do not count.')));
    const jump = () => { dishList.open = true; dishList.scrollIntoView({ block: 'start', behavior: 'instant' }); };
    fill(content,
      h('div', { class: 'league-heading' }, h('h2', {}, current ? 'This week' : `Week of ${label(week.start)}`), field('Week of', choose)),
      h('p', { class: 'league-pick' }, data.pick.reason),
      h('p', { class: 'league-duty' }, duty ? `${duty.name} is on kitchen duty. ${duty.why}`
        : rules.enabled ? 'Nobody is on kitchen duty yet.' : 'Kitchen duty is off. Standings still count.'),
      h('ol', { class: 'league-standings', 'aria-label': 'Weekly points' }, standings.map(p => h('li', {},
        avatar(p.resident_id, p.name), h('a', { href: `#/people/${p.resident_id}` }, p.name),
        h('span', { class: 'league-points' }, h('b', {}, String(p.points)), ` ${p.points === 1 ? 'point' : 'points'}`)))),
      h('div', { class: 'league-foot' },
        h('p', {}, `${data.unreviewed_count} ${data.unreviewed_count === 1 ? 'visit needs' : 'visits need'} an answer.`),
        data.unreviewed_count && !week.closed ? h('button', { type: 'button', class: 'link', onclick: jump }, 'Review or name them') : null),
      h('p', { class: 'league-note' }, week.closed ? 'Closed. Names, disputes and points are frozen.'
        : `Monday–Sunday · ${week.timezone}. Closes Monday at noon, with 12 hours to name Sunday’s late dishes.`),
      h('p', { class: 'league-note' }, `Pan ${rules.weights.pan} · pot ${rules.weights.pot} · other ${rules.weights.default} points each. A pick needs ${rules.min_points} points and a lead of ${rules.min_lead}. Without a clear pick, the current duty person stays on.`),
      dishList,
      data.verdicts.length ? h('details', { class: 'league-dishes' }, h('summary', {}, `Reviewed answers (${data.verdicts.length})`),
        ...data.verdicts.map(v => h('div', { class: 'league-verdict' },
          h('p', {}, v.title), h('p', { class: 'league-note' }, `${v.by?.name || 'The operator'}: ${v.verdict === 'not_dish' ? 'Not a dish' : v.verdict === 'clean' ? 'It’s clean' : 'Same dish as another'} · ${new Date(v.at).toLocaleString()}`),
          !week.closed ? h('button', { type: 'button', class: 'btn sm', onclick: e => busy(e.currentTarget, async () => {
            await api(`/api/league/visits/${encodeURIComponent(v.visit_id)}/undo-verdict`, { method: 'POST', body: {} }); toast('Answer reversed.'); await load();
          }) }, 'Undo answer') : null))) : null);
  }

  function dishRow(dish, residents, week, visits, dishes) {
    const image = dish.filmstrip_url || dish.photo_url;
    const controls = h('div', { class: 'stack' });
    if (!week.closed) controls.append(h('p', { class: 'tx2' }, 'Name someone only if you can tell this is a used dish they left out. Being nearby is not enough.'));
    if (dish.can_settle) {
      controls.append(h('p', { class: 'tx2' }, `${dish.dispute?.by?.name || dish.resident?.name || 'The named person'} says “That wasn’t me.”${dish.dispute?.note ? ` ${dish.dispute.note}` : ''}`),
        h('div', { class: 'row-actions' }, ...[['them', 'It was them'], ['not_them', 'Not them']].map(([decision, text]) =>
          h('button', { type: 'button', class: 'btn', onclick: e => busy(e.currentTarget, async () => {
            await api(`/api/league/visits/${encodeURIComponent(dish.id)}/settle`, { method: 'POST', body: { decision } }); toast('Dispute settled.'); await load();
          }) }, text))));
    } else if (dish.can_name) {
      const select = h('select', { name: 'resident_id', required: true, 'aria-label': `Who left ${dish.title}?` },
        h('option', { value: '' }, 'Choose a housemate'),
        [...residents].sort((a, b) => a.name.localeCompare(b.name)).map(p => h('option', { value: p.resident_id, selected: p.resident_id === dish.resident?.id }, p.name)));
      const naming = form(async fd => {
        await api(`/api/league/visits/${encodeURIComponent(dish.id)}/name`, { method: 'POST', body: { resident_id: Number(fd.get('resident_id')) } });
        toast('Name saved.'); await load();
      }, { submit: dish.resident ? 'Change name' : 'Save name' });
      if (dish.guess && !dish.resident) controls.append(
        h('p', { class: 'send-guess' }, `Looks like ${dish.guess.name}: ${dish.guess.basis}`),
        h('button', { type: 'button', class: 'btn', onclick: e => busy(e.currentTarget, async () => {
          await api(`/api/league/visits/${encodeURIComponent(dish.id)}/name`, { method: 'POST', body: { resident_id: dish.guess.resident_id } });
          toast('Visit named.'); await load();
        }) }, `Yes, ${dish.guess.name}`));
      naming.append(field('Who left this visit’s dishes?', select)); naming.finish(); controls.append(naming);
    }
    if (!week.closed) {
      const answer = async verdict => { await api(`/api/league/visits/${encodeURIComponent(dish.id)}/verdict`, { method: 'POST', body: { verdict } }); toast(verdict === 'cant_tell' ? 'Skipped for you. No label saved.' : 'Answer saved. The operator can undo it.'); await load(); };
      const duplicate = form(async fd => {
        await api(`/api/league/visits/${encodeURIComponent(dish.id)}/verdict`, { method: 'POST', body: { verdict: 'duplicate', of_visit_id: fd.get('of_visit_id') } });
        toast('Duplicate recorded.'); await load();
      }, { submit: 'Same dish as another', kind: '' });
      duplicate.append(field('Which other visit shows the same dish?', h('select', { name: 'of_visit_id', required: true },
        h('option', { value: '' }, 'Choose the original visit or dish'), ...visits.filter(v => v.id !== dish.id).flatMap(v => [
          h('option', { value: v.id }, `${v.title} · ${v.area || 'Kitchen'} · ${new Date(v.at).toLocaleString()}`),
          ...(v.dish_ids.length > 1 ? v.dish_ids.map(id => h('option', { value: `dish-${id}` }, `Only ${dishes.find(d => d.id === id)?.title || 'this dish'} · ${v.area || 'Kitchen'} · ${new Date(v.at).toLocaleString()}`)) : []),
        ]))));
      duplicate.finish();
      controls.append(h('div', { class: 'divider' }), h('p', { class: 'tx2' }, 'Something else?'),
        h('div', { class: 'row-actions' }, ...[['not_dish', 'Not a dish'], ['clean', 'It’s clean'], ['cant_tell', 'Can’t tell']].map(([verdict, label]) =>
          h('button', { type: 'button', class: 'btn', onclick: e => busy(e.currentTarget, () => answer(verdict)) }, label))),
        h('p', { class: 'league-note' }, 'Can’t tell hides this visit just for you. The other answers remove it for everyone, with your name on the answer.'),
        h('details', {}, h('summary', {}, 'Same dish as another'), duplicate));
    }
    return h('details', { class: 'league-dish', 'data-visit-id': dish.id },
      h('summary', {}, h('span', {}, dish.title, h('small', {}, `${dish.area || 'Kitchen'} · ${dish.resident?.name || 'Unnamed'}${dish.dispute?.state === 'open' ? ' · disputed' : ''}`)),
        h('span', {}, `${dish.points} pt`)),
      h('div', { class: 'stack' }, image ? h('a', { href: image, target: '_blank', rel: 'noopener' },
        h('img', { class: 'league-photo', src: image, loading: 'lazy', alt: dish.filmstrip_url ? 'Arrival filmstrip' : 'Saved dish photo' })) : h('p', { class: 'tx2' }, 'No photo is kept for this dish.'),
      !dish.arrival_seen ? h('p', { class: 'league-note' }, `Already there at ${new Date(dish.at).toLocaleString()} — arrival not recorded. The cleanup follow-up starts at first observation.`) : null,
      dish.named_at ? h('p', { class: 'league-note' }, `Named by ${dish.named_by?.name || 'the operator'} on ${new Date(dish.named_at).toLocaleString()}.`) : null,
      week.closed ? h('p', { class: 'league-note' }, 'This week is closed.') : controls));
  }

  load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, 60000);
  return { el: root, refresh: load, destroy() { alive = false; clearInterval(timer); } };
}
