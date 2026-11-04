// The weekly result and review use the same visit contract as the house site.
import { api, busy, field, fill, h, toast } from './core.js';
import { nameChips, guessSentence } from './name-chips.js';
import { time } from './photo-ui.js';
import { sendButton } from './share.js';

export function leaguePanel({ onChanged = () => {} } = {}) {
  let alive = true, selected = '', request = 0, data = null, signature = '', currentId = null, showWeak = false;
  const pending = new Map();
  const summary = h('section', { class: 'week-summary', 'aria-label': 'Weekly kitchen duty' }, h('p', { class: 'tx2' }, 'Loading this week…'));
  const queueTitle = h('h2', { tabindex: '-1' }, 'To review');
  const weakLink = h('button', { type: 'button', class: 'weak-evidence-link', hidden: true });
  const queueNote = h('p', { class: 'league-note' }, 'Unnamed visits simply don’t count.');
  const queueBody = h('div', { class: 'review-body', 'aria-busy': 'true' });
  const receipts = h('div', { class: 'review-receipts', 'aria-live': 'polite' });
  const errorBox = h('p', { class: 'form-error', role: 'alert' });
  const queue = h('section', { class: 'visit-review', 'aria-label': 'Visit review' },
    h('header', { class: 'home-section-head' }, queueTitle), weakLink, queueNote, receipts, errorBox, queueBody);
  const history = h('details', { class: 'week-history' }, h('summary', {}, 'Week’s record'));
  const sharing = h('section', { class: 'week-share', 'aria-label': 'Share this week' });
  const label = value => new Date(`${value}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' });

  async function load() {
    if (!alive) return;
    const turn = ++request;
    try {
      const next = await api(`/api/league/week${selected ? `?start=${selected}` : ''}`);
      if (!alive || turn !== request) return;
      const changed = JSON.stringify(next) !== signature;
      data = next; signature = JSON.stringify(next);
      if (errorBox.textContent.startsWith('Could not refresh')) errorBox.textContent = '';
      if (changed) render();
    } catch (error) {
      if (!alive || turn !== request) return;
      errorBox.textContent = `Could not refresh this week. ${error.message}`;
      queueBody.removeAttribute('aria-busy');
      if (!data) fill(queueBody, h('button', { class: 'btn', type: 'button', onclick: load }, 'Try again'));
    }
  }

  function render() {
    const { week, rules, on_duty: duty, standings, pick } = data;
    const current = week.start === data.weeks[0].start;
    const choose = h('select', { 'aria-label': 'Week of', disabled: pending.size > 0, onchange: e => {
      selected = e.target.value; signature = ''; currentId = null; showWeak = false; load();
    } }, data.weeks.map(w => h('option', { value: w.start, selected: w.start === week.start }, `${label(w.start)}${w.closed ? ' · closed' : ''}`)));
    const leaders = standings.filter(p => p.points > 0);
    fill(summary,
      h('header', { class: 'home-section-head' }, h('h1', { id: 'page-title', tabindex: '-1' }, current ? 'This week' : `Week of ${label(week.start)}`), choose),
      h('div', { class: 'week-duty' }, h('p', {}, 'On kitchen ', h('strong', {}, duty?.name || 'Nobody yet')),
        h('p', {}, !rules.enabled ? 'Kitchen duty is off.' : week.closed ? 'Week closed · points frozen' : `Closes ${new Intl.DateTimeFormat('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit', timeZone: week.timezone }).format(new Date(week.closes_at))} · house time`)),
      h('ol', { class: 'week-standings', 'aria-label': 'Weekly points' }, leaders.slice(0, 3).map(p => h('li', {},
        h('a', { href: `#/people/${p.resident_id}` }, p.name), h('b', {}, `${p.points} pt`)))),
      leaders.length > 3 ? h('p', { class: 'league-note' }, leaders.slice(3).map(p => `${p.name} ${p.points} pt`).join(' · ')) : null,
      leaders.length < standings.length ? h('p', { class: 'league-note' }, leaders.length ? 'Everyone else 0' : 'Everyone 0 points') : null,
      h('p', { class: 'week-next' }, pick.state === 'pick' ? `${pick.name} ${week.closed ? 'was picked for' : 'is in line for'} next week’s kitchen.`
        : pick.state === 'disabled' ? 'Kitchen duty is off.' : week.closed ? 'No one picked this week.' : 'No clear pick for next week yet.'),
      h('p', { class: 'league-note' }, pick.reason,
        pick.state !== 'pick' && pick.state !== 'disabled' && duty ? ` · ${duty.name} stays on kitchen.` : ''),
      h('p', { class: 'league-note' }, `League started ${label(data.start_week)}.`));
    renderQueue(); renderHistory(); renderShare();
  }

  function renderQueue() {
    queueBody.removeAttribute('aria-busy');
    const available = data.week.closed ? [] : data.visits.filter(v => ((!v.resident && v.can_name) || v.can_settle) && !pending.has(v.id));
    const clear = available.filter(v => !v.resident && v.evidence !== 'weak').length;
    const weak = available.filter(v => !v.resident && v.evidence === 'weak').length;
    const disputes = available.filter(v => v.can_settle).length;
    queueTitle.textContent = data.week.closed ? 'Week closed' : `${clear} clear ${clear === 1 ? 'one' : 'ones'} to name`;
    queueNote.textContent = disputes ? `${disputes} ${disputes === 1 ? 'dispute needs' : 'disputes need'} settling. Those points are paused. Unnamed visits don’t count.` : 'One visit = one kitchen trip. Unnamed visits don’t count.';
    weakLink.hidden = !weak;
    weakLink.textContent = showWeak ? 'Hide weak evidence' : `+${weak} with weak evidence`;
    weakLink.setAttribute('aria-expanded', String(showWeak));
    weakLink.onclick = () => { showWeak = !showWeak; currentId = null; renderQueue(); returnToReview(); };
    const visits = available.filter(v => v.can_settle || showWeak || v.evidence !== 'weak');
    const next = visits.find(v => v.id === currentId) || visits[0];
    currentId = next?.id;
    fill(queueBody, next ? visitCard(next) : h('div', { class: 'review-empty' },
      h('h3', {}, data.week.closed ? 'This week is closed' : pending.size ? 'All visits answered' : data.visits.length || data.verdicts.length ? 'You’re caught up' : 'Nothing to review yet'),
      h('p', {}, data.week.closed ? 'Names, answers and points are frozen. Choose an open week to review.'
        : pending.size ? 'Your answers will save in a moment. You can still undo them.'
        : weak ? 'The remaining visits have weak evidence. You can leave them unnamed or reveal them above.'
        : 'New visits appear when dishes are seen past their deadline. Only confirmed names score points.'),
      h('a', { class: 'link', href: '#/live' }, 'Open Live kitchen')));
  }

  function returnToReview() {
    queueTitle.focus({ preventScroll: true });
    queue.scrollIntoView({ block: 'start', behavior: 'instant' });
  }

  function visitCard(visit, { reviewing = true } = {}) {
    const image = visit.filmstrip_url || visit.photo_url;
    const media = h('div', { class: 'visit-media' }, image ? h('a', { href: image, target: '_blank', rel: 'noopener', title: 'Open full size' },
      h('img', { src: image, width: visit.filmstrip_url ? visit.filmstrip_width : visit.photo_width,
        height: visit.filmstrip_url ? visit.filmstrip_height : visit.photo_height, decoding: 'async',
        loading: reviewing ? 'eager' : 'lazy', alt: visit.filmstrip_url ? `Camera views for ${visit.title}. Open full size to inspect.` : `Saved view of ${visit.title}; arrival was not recorded.` }))
      : h('p', { class: 'media-unavailable' }, 'No saved photo. Only answer if you know these dishes.'));
    media.querySelector('img')?.addEventListener('error', () => fill(media, h('p', { class: 'media-unavailable' }, 'Photo unavailable. Open the record or only answer if you know.')), { once: true });
    const controls = h('div', { class: 'visit-controls' });
    if (!data.week.closed) {
      controls.append(h('p', { class: 'visit-caution' }, visit.arrival_seen
        ? 'Name who used or left these dishes. Being nearby isn’t enough.' : 'Arrival not recorded. Only name someone if you know.'));
      if (visit.can_settle) {
        controls.append(h('p', {}, `${visit.dispute?.by?.name || visit.resident?.name || 'The named person'} disputes this name. Points are paused.`),
          visit.dispute?.note ? h('p', { class: 'tx2' }, visit.dispute.note) : null,
          h('div', { class: 'row-actions' }, ...[['them', 'It was them'], ['not_them', 'Not them']].map(([decision, text]) =>
            h('button', { type: 'button', class: 'btn', onclick: () => answer(visit, 'settle', { decision }, text) }, text))));
      } else if (visit.can_name) {
        controls.append(h('h3', {}, 'Who used or left these dishes?'),
          h('div', { class: 'name-chips', role: 'group', 'aria-label': 'Name the person responsible' },
            nameChips(data.standings.map(p => ({ id: p.resident_id, name: p.name })), { selected: visit.resident?.id, guess: visit.guess?.resident_id,
              choose: person => answer(visit, 'name', { resident_id: person.id }, `Named as ${person.name}`) })),
          visit.guess && !visit.resident ? h('p', { class: 'send-guess' }, guessSentence(visit.guess.name, visit.guess.basis)) : null);
      }
      const others = data.visits.filter(v => v.id !== visit.id && !pending.has(v.id));
      const picker = h('div', { class: 'league-duplicate', hidden: true });
      const select = h('select', { 'aria-label': 'Original visit', required: true }, h('option', { value: '' }, 'Choose the original visit or dish'),
        others.flatMap(v => [h('option', { value: v.id }, `${v.title} · ${v.area || 'Kitchen'} · ${time(v.at)}`),
          ...(v.dish_ids.length > 1 ? v.dish_ids.map(id => h('option', { value: `dish-${id}` }, `Only ${data.dishes.find(d => d.id === id)?.title || 'this dish'} · ${time(v.at)}`)) : [])]));
      fill(picker, field('Which visit shows the same dish?', select),
        h('button', { type: 'button', class: 'btn', onclick: () => {
          if (!select.value) { select.reportValidity(); return; }
          answer(visit, 'verdict', { verdict: 'duplicate', of_visit_id: select.value }, 'Same dish as another');
        } }, 'Save same dish'));
      controls.append(h('div', { class: 'league-verdict-buttons' },
        ...[['not_dish', 'Not a dish'], ['clean', 'It’s clean']].map(([verdict, text]) => h('button', { type: 'button', class: 'btn', onclick: () => answer(visit, 'verdict', { verdict }, text) }, text)),
        h('button', { type: 'button', class: 'btn', 'aria-expanded': 'false', onclick: e => {
          picker.hidden = !picker.hidden; e.currentTarget.setAttribute('aria-expanded', String(!picker.hidden));
          if (!picker.hidden) select.focus();
        } }, 'Same as another'),
        h('button', { type: 'button', class: 'btn', onclick: () => answer(visit, 'verdict', { verdict: 'cant_tell' }, 'Can’t tell · skipped for you') }, 'Can’t tell')),
        picker, h('p', { class: 'league-note' }, 'Can’t tell skips just for you; no label saved. Other answers apply to the whole house.'));
    } else controls.append(h('p', { class: 'league-note' }, 'This week is closed.'));
    return h('article', { class: 'visit-card', 'data-visit-id': visit.id },
      h('header', { class: 'visit-heading' }, h('div', {}, h('h3', {}, visit.title),
        h('p', {}, `${visit.evidence[0].toUpperCase() + visit.evidence.slice(1)} evidence · ${visit.area || 'Kitchen'} · ${time(visit.at)}`),
        visit.reason ? h('p', {}, visit.reason) : null), h('span', {}, visit.resident ? `${visit.resident.name} · ${visit.points} pt` : `${visit.points} potential pt`)),
      h('div', { class: 'visit-content' }, media, controls),
      h('a', { class: 'visit-record link', href: `#/incident/${visit.dish_ids[0]}` }, 'Photos & record'));
  }

  // Advance immediately; each tap remains cancellable for five seconds. Keep saving entries
  // excluded until a fresh server read acknowledges them, even with several quick answers.
  function answer(visit, action, body, label) {
    if (pending.has(visit.id)) return;
    errorBox.textContent = '';
    const receipt = h('div', { class: 'review-receipt' }, h('span', {}, `${visit.title}: ${label}`));
    const undo = h('button', { type: 'button', class: 'btn', onclick: () => {
      clearTimeout(entry.timer); pending.delete(visit.id); receipt.remove(); currentId = visit.id;
      render(); returnToReview();
    } }, 'Undo');
    receipt.append(undo); receipts.append(receipt);
    const entry = { timer: null, commit: null, saving: false };
    entry.commit = async () => {
      if (entry.saving) return;
      entry.saving = true; clearTimeout(entry.timer); undo.disabled = true;
      try {
        await api(`/api/league/visits/${encodeURIComponent(visit.id)}/${action}`, { method: 'POST', body, keepalive: true });
        await Promise.all([load(), alive ? onChanged() : null]);
      } catch (error) {
        errorBox.textContent = `Answer not saved. ${error.message}`;
        toast(`Answer not saved. ${error.message}`, { error: true });
      } finally {
        pending.delete(visit.id); receipt.remove();
        if (alive) render();
      }
    };
    entry.timer = setTimeout(entry.commit, 5000); pending.set(visit.id, entry);
    render(); returnToReview();
  }

  function renderHistory() {
    const open = history.open;
    fill(history, h('summary', {}, `Week’s record · ${data.visits.length} visits`),
      h('p', { class: 'league-note' }, `Pan ${data.rules.weights.pan} · pot ${data.rules.weights.pot} · other ${data.rules.weights.default} pt. Pick needs ${data.rules.min_points} points and ${data.rules.min_lead} ahead. Only human-confirmed names count.`),
      ...data.visits.filter(v => !pending.has(v.id)).map(v => h('details', { class: 'league-dish' }, h('summary', {}, `${v.title} · ${v.resident?.name || 'Unnamed'}${v.dispute?.state === 'open' ? ' · disputed' : ''}`), visitCard(v, { reviewing: false }))),
      ...data.verdicts.map(v => h('div', { class: 'league-verdict' }, h('p', {}, v.title),
        h('p', { class: 'league-note' }, `${v.by?.name || 'Operator'}: ${v.kind === 'not_dish' ? 'Not a dish' : v.kind === 'clean' ? 'It’s clean' : 'Same dish as another'} · ${time(v.at)}`),
        !data.week.closed ? h('button', { type: 'button', class: 'btn', onclick: e => busy(e.currentTarget, async () => {
          await api(`/api/league/visits/${encodeURIComponent(v.visit_id)}/undo-verdict`, { method: 'POST', body: {} });
          currentId = v.visit_id; await Promise.all([load(), onChanged()]);
        }) }, 'Undo answer') : null)));
    history.open = open;
  }

  let shareSignature = '', shareRevision = 0;
  function renderShare() {
    const value = JSON.stringify([data.week, data.standings, data.pick, data.on_duty, data.rules, pending.size > 0]);
    if (value === shareSignature) return;
    shareSignature = value;
    if (pending.size) {
      fill(sharing, h('h3', {}, 'Share this week'), h('p', { class: 'tx2' }, 'Saving your answers before sharing the updated standings.'),
        h('button', { type: 'button', class: 'btn', disabled: true }, 'Saving answers…'));
      return;
    }
    const url = `/api/league/share?start=${data.week.start}&v=${++shareRevision}`;
    const send = sendButton(url, `highest-house-${data.week.start}.jpg`, { label: 'Share this week', downloadLabel: 'Download this week' });
    const preview = h('details', { class: 'week-image-preview' }, h('summary', {}, 'Preview group chat image'));
    preview.addEventListener('toggle', () => {
      if (preview.open && !preview.querySelector('img')) preview.append(h('a', { href: url, target: '_blank', rel: 'noopener' },
        h('img', { src: url, alt: `Highest House standings for the week of ${label(data.week.start)}`, width: 1080, decoding: 'async' })));
    });
    fill(sharing, h('h3', {}, 'Share this week'), h('p', { class: 'tx2' }, data.week.closed ? 'The closed result, ready for the house chat.' : 'Standings and next duty, marked as a week in progress.'),
      h('div', { class: 'send-bar' }, send.el), preview);
    send.preload();
  }

  const flush = () => { for (const entry of pending.values()) entry.commit(); };
  const hidden = () => { if (document.hidden) flush(); };
  document.addEventListener('visibilitychange', hidden);
  load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, 60000);
  return { summary, queue, history, sharing, refresh: load, destroy() {
    alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', hidden); flush();
  } };
}
