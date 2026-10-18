import { api, fill, h, when, activeResidents, emit, icon } from '../core.js';
import { time, dishTitle, isFuture, photoReason, croppedView } from '../photo-ui.js';
const STAGE_NAMES = {before: 'Earlier', using: 'Handling', leaving: 'Left out', later: 'Two hours later'};
const QUESTIONS = [
  ['before', 'Before', 'The target dish is absent from this position, or its clean starting state is visible.'],
  ['using', 'Handling', 'This person uses or places this exact dish. Standing nearby is not enough.'],
  ['leaving', 'Left behind', 'The dish remains when that person leaves.'],
  ['later', 'Later', 'The same dish is still there after the deadline; check for replacement or a camera gap.']
];
function advancedIncidentPage(main, route) {
  let alive = true, dirty = false, saving = false, loading = false, detail;
  const content = h('div', {class: 'page incident-workspace'});
  fill(main, content);
  function select(name, options, value) {
    return h('select', {name}, options.map(([key, label]) => h('option', {value: key, selected: String(value ?? '') === String(key)}, label)));
  }
  function field(label, control, hint = '') { return h('label', {class: 'incident-field'}, h('span', {}, label), control, hint ? h('small', {class: 'tx3'}, hint) : null); }
  async function load() {
    if (loading) return; loading = true;
    try { const result = await api(`/api/incidents/${Number(route.sub)}`); if (!alive || dirty || saving) return; if (detail && result.revision === detail.revision && result.sequence?.fingerprint === detail.sequence?.fingerprint) return; detail = result; dirty = false; render(); }
    catch (e) { if (alive && !detail) fill(content, h('a', {href: '#/feed'}, '← Incidents'), h('h1', {}, 'Could not open this incident'), h('p', {role: 'alert'}, e.message)); }
    finally { loading = false; }
  }
  function render() {
    const {record: o, sequence: seq, decision: d} = detail;
    const people = activeResidents().map(p => [p.id, p.name]);
    const error = h('p', {class: 'form-error', role: 'alert'}), saved = h('p', {'aria-live': 'polite', class: 'tx2'});
    const save = h('button', {type: 'submit', class: 'btn primary'}, 'Save review');
    const f = h('form', {class: 'incident-review', oninput: () => { dirty = true; saved.textContent = 'Unsaved changes'; }, onsubmit: async e => {
      e.preventDefault(); if (saving) return; saving = true; save.disabled = true; error.textContent = '';
      const fd = new FormData(f), checks = Object.fromEntries(QUESTIONS.map(([k]) => [k, fd.get(k) === '' ? null : fd.get(k) === 'yes']));
      const person = fd.get('person'), owner = fd.get('responsibility');
      const body = {revision: detail.revision, checks, person_id: person ? Number(person) : null,
        identity: fd.get('identity'), responsibility: owner, condition: fd.get('condition'), outcome: fd.get('outcome'),
        cleaner_id: fd.get('cleaner') ? Number(fd.get('cleaner')) : null,
        resolved_at: fd.get('resolved_at') ? new Date(fd.get('resolved_at')).toISOString() : null, note: fd.get('note')};
      try { detail = await api(`/api/incidents/${o.id}/review`, {method: 'PUT', body}); dirty = false; emit('changed'); if (alive) { render(); content.querySelector('[data-saved]').textContent = 'Review saved'; } }
      catch (err) { error.textContent = err.message; if (err.status === 409) error.append(h('button', {type: 'button', class: 'btn', onclick: load}, 'Reload latest evidence')); }
      finally { saving = false; save.disabled = false; }
    }});
    const answers = seq?.stale ? {} : seq?.checks || {};
    f.append(h('h2', {}, 'Check the evidence'), h('p', {class: 'tx2'}, 'Save what you can establish. “Cannot tell” keeps the uncertainty visible.'),
      ...QUESTIONS.map(([key, title, question]) => field(`${title}: ${question}`, select(key, [['', 'Cannot tell'], ['yes', 'Yes'], ['no', 'No']], answers[key] === true ? 'yes' : answers[key] === false ? 'no' : ''))),
      h('hr'), h('h2', {}, 'Person and responsibility'),
      field('Person in the evidence', select('person', [['', 'Unknown'], ...people], detail.identified_person_id || o.resident_id || d.person_id)),
      field('Identity decision', select('identity', [['keep', 'Keep existing identity'], ['named', 'I recognize the selected person'], ['unknown', 'Cannot identify this person']], 'keep'), 'Recognizing someone does not assign the dish to them.'),
      field('Who left this dish?', select('responsibility', [['keep', 'Keep existing responsibility'], ['confirm', 'The selected person left it'], ['unknown', 'Cannot establish responsibility']], 'keep')),
      field('Dish condition', select('condition', [['unknown', 'Cannot tell'], ['used', 'Used / dirty dish'], ['clean', 'Clean dish'], ['not_dish', 'Not a dish']], d.condition || 'unknown')),
      h('hr'), h('h2', {}, 'What happened afterward?'),
      field('Outcome', select('outcome', [['keep', 'Keep current outcome'], ['out', 'Still out'], ['removed', 'Removed · washing unknown'], ['washed', 'Washing confirmed by me'], ['disputed', 'Dispute this incident']], 'keep')),
      field('Who washed it?', select('cleaner', [['', 'Unknown / not applicable'], ...people], ''), 'Used only when washing is confirmed.'),
      field('Time removed or washed', h('input', {type: 'datetime-local', name: 'resolved_at'}), 'Leave blank only if you are confirming it happened now.'),
      field('Note', h('textarea', {name: 'note', rows: 2, maxlength: 1000}, d.note || '')),
      error, h('div', {class: 'incident-save'}, save, h('span', {'data-saved': '', 'aria-live': 'polite'})), saved);
    const stageViews = h('div', {class: 'original-stages'}, (seq?.stages || []).map(stage => h('figure', {},
      stage.url ? h('a', {href: stage.url, target: '_blank', rel: 'noopener'}, h('img', {src: stage.url, alt: stage.title, loading: 'lazy'})) : h('div', {class: 'missing-stage'}, 'No retained frame'),
      h('figcaption', {}, stage.title, h('span', {class: 'tx3'}, stage.at ? when(stage.at) : 'Time unknown')))));
    const searches = h('section', {class: 'incident-clips'}, h('h2', {}, 'Earlier activity search'),
      (detail.searches || []).length ? detail.searches.map(job => h('div', {}, h('p', {class: 'tx2'}, job.message),
        (job.candidates || []).map(c => h('article', {class: 'search-candidate'}, h('p', {class: 'tx3'}, `${when(c.from)} → ${when(c.to)} · estimated recording times`),
          h('div', {class: 'original-stages'}, ['before', 'after'].map(side => h('img', {src: `/api/traces/${job.id}/pictures/${c.index}/${side}`, alt: `Earlier activity ${side}`, loading: 'lazy'}))),
          h('p', {class: 'tx3'}, c.reason), h('a', {class: 'btn sm', href: `/api/traces/${job.id}/clips/${c.index}`, target: '_blank', rel: 'noopener'}, 'Watch candidate clip'),
          h('button', {type: 'button', class: 'btn sm', disabled: c.kept, onclick: async e => {
            e.currentTarget.disabled = true;
            try { await api(`/api/traces/${job.id}/keep`, {method: 'POST', body: {candidate: c.index}}); e.target.textContent = 'Candidate saved'; }
            catch (err) { error.textContent = err.message; e.target.disabled = false; }
          }}, c.kept ? 'Candidate saved' : 'Keep this evidence'))))) : h('p', {class: 'tx3'}, 'Recent incomplete incidents are searched automatically while their recording is retained. Older missing activity cannot be recovered after it expires.'));
    const clips = o.evidence.filter(e => e.kind === 'clip' && !e.expired);
    fill(content, h('a', {class: 'crumb', href: '#/feed'}, icon('arrowLeft', 14), 'Incidents'),
      o.archived_at ? h('p', {class: 'feed-health'}, 'Archived before the fresh start. This record does not appear in current incidents or counts.', h('a', {href: '#/feed?filter=archive'}, 'View archive')) : null,
      h('header', {class: 'page-head'}, h('div', {},
        h('h1', {id: 'page-title', tabindex: '-1'}, `${o.resident_name && o.confidence === 'confirmed' ? o.resident_name + ' · ' : ''}${seq?.dishes || o.dishes.map(d => `${d.quantity} ${d.kind}`).join(', ')}`),
        h('p', {class: 'tx2'}, `#${o.id} · ${o.location || 'Kitchen'} · ${when(o.created_at)} · ${{removed: 'Removed; washing unknown', washed: 'Washing confirmed', out: 'Detected on a counter or in the sink', unknown: 'Continuity needs checking', clean: 'Clean dish'}[detail.outcome] || detail.outcome}${o.due_at ? ` · deadline ${when(o.due_at)}` : ` · two-hour photo ${when(o.followup_at)} (from an unattended view)`}`))),
      h('div', {class: 'incident-columns'}, h('section', {class: 'incident-evidence', 'aria-label': 'Incident evidence'},
        seq?.image_url ? h('div', {class: 'evidence-caption'},
          h('a', {class: 'btn primary', href: seq.image_url, download: `dish-${o.id}-four-stage.jpg`}, 'Download photo'),
          h('p', {class: 'tx2'}, 'Saves the four-frame collage as one image to attach in your house chat. Check the photos before assigning responsibility.')) : null,
        seq?.image_url ? h('a', {href: seq.image_url, target: '_blank', rel: 'noopener', class: 'full-collage'}, h('img', {src: seq.image_url, alt: 'Before, handling, departure, and later evidence collage'})) : h('div', {class: 'incident-empty'}, h('h2', {}, 'Four-stage evidence is not ready'), h('p', {}, 'Review the retained originals below. Missing stages cannot be reconstructed from repeated photos.')),
        h('div', {class: 'evidence-caption'}, h('strong', {}, `${seq?.captured || 0}/4 frames saved${seq?.status === 'ready' ? ' · reviewed' : !seq?.object_localized ? ' · dish tracking incomplete' : ' · needs checking'}`), h('p', {class: 'tx2'}, 'Yellow boxes locate dish candidates. Blue marks the comparison area in the earlier view. Enlarged crops help you compare; separate frames do not prove what happened between them.'),
          (seq?.warnings || []).map(w => h('p', {class: 'tx3'}, w))),
        h('details', {}, h('summary', {}, 'Inspect original full frames'), stageViews),
        h('section', {class: 'incident-clips'}, h('h2', {}, 'Video context'), clips.length ? clips.map(e => h('figure', {}, h('video', {src: e.url, controls: '', preload: 'none'}), h('figcaption', {}, e.label === 'Putting the dish down' ? 'Around the first observation · handling unconfirmed' : e.label))) : h('p', {class: 'tx3'}, 'No retained video for this incident.'),
          detail.capture_jobs.filter(j => j.status !== 'saved').map(j => h('p', {class: 'tx3'}, `${j.label}: ${j.status === 'failed' ? 'not captured' : 'capture pending'}${j.error ? ' · '+j.error : ''}`))), searches), f));
  }
  const beforeUnload = e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);
  load();
  return {update(kind) { if (kind === 'data' && !dirty && !saving) load(); }, beforeLeave() { return !dirty || window.confirm('Discard this unsaved review?'); }, destroy() { alive = false; window.removeEventListener('beforeunload', beforeUnload); }};
}


export function incidentDetailPage(main, route) {
  if (route.query?.review === '1') return advancedIncidentPage(main, route);
  let alive=true, loading=false, editing=false, detail, signature='';
  const content=h('div',{class:'page photo-detail'});
  content.append(h('p',{class:'tx2'},'Loading photos…'));
  fill(main,content);
  async function load() {
    if(loading || editing) return;
    loading=true;
    try {
      const result=await api(`/api/incidents/${Number(route.sub)}`);
      if(!alive || editing) return;
      const fresh=JSON.stringify([result.revision,result.sequence?.fingerprint,result.share_ready,result.share_reason]);
      if(fresh===signature) return;
      signature=fresh;detail=result;render();
    } catch(e) {if(alive && !detail)fill(content,h('h1',{},'Could not load photos'),h('p',{role:'alert'},e.message),h('button',{class:'btn',onclick:load},'Try again'));}
    finally {loading=false;}
  }
  function render() {
    const o=detail.record,seq=detail.sequence,d=detail.decision;
    const title=dishTitle(seq?.dishes || o.dishes.map(x=>`${x.quantity} ${x.kind}`).join(', '));
    const stages=seq?.stages || [], available=stages.filter(s=>s.url);
    const last=[...available].reverse().find(s=>['later','leaving'].includes(s.key)) || available.at(-1);
    const watching=!o.archived_at && o.lifecycle==='open' && !['clean','not_dish'].includes(d.condition);
    const due=watching && isFuture(o.followup_at);
    const status=!watching ? (['clean','not_dish'].includes(d.condition) ? 'Marked as not a dirty dish. No longer tracked.' : 'Closed. No longer tracked.')
      : due ? `Next photo ${time(o.followup_at)}` : photoReason(detail.share_reason, seq?.captured);
    const action=h('select',{name:'correction','aria-label':'What needs correcting?',required:true,onchange:()=>{personField.hidden=action.value!=='name';editing=true;}},
      [['','Choose a correction'],['clean','This dish is clean'],['not_dish','This is not a dish'],['wrong','These photos show different dishes'],['removed','This dish has been put away'],['name','Identify the person in the photo']].map(([value,label])=>h('option',{value},label)));
    const person=h('select',{name:'person','aria-label':'Person in the photo'},h('option',{value:''},'Choose a person'),activeResidents().map(p=>h('option',{value:p.id,selected:p.id===detail.identified_person_id},p.name)));
    const personField=h('label',{class:'incident-field',hidden:true},'Person in the handling photo',person);
    const error=h('p',{class:'form-error',role:'alert'});
    const correction=h('details',{class:'photo-disclosure',ontoggle:e=>{editing=e.currentTarget.open;}},h('summary',{},'Something wrong?'));
    const form=h('form',{class:'photo-correction',onsubmit:async e=>{
      e.preventDefault();const button=form.querySelector('button');button.disabled=true;error.textContent='';
      const body={revision:detail.revision,checks:seq?.stale ? {} : seq?.checks || {},condition:d.condition || 'unknown',identity:'keep',responsibility:'keep',outcome:'keep',note:d.note || ''};
      if(action.value==='name'){body.identity='named';body.person_id=person.value ? Number(person.value) : null;}
      if(['clean','not_dish'].includes(action.value))body.condition=action.value;
      if(action.value==='wrong')body.outcome='disputed';
      if(action.value==='removed')body.outcome='removed';
      try{detail=await api(`/api/incidents/${o.id}/review`,{method:'PUT',body});editing=false;signature='';render();emit('changed');content.querySelector('[data-feedback]').textContent='Correction saved';}
      catch(err){error.textContent=err.message;if(err.status===409)error.append(h('button',{type:'button',class:'btn',onclick:()=>{editing=false;load();}},'Reload latest photos'));}
      finally{button.disabled=false;}
    }},h('label',{class:'incident-field'},'What needs correcting?',action),personField,error,h('button',{class:'btn',type:'submit'},'Save correction'));
    correction.append(form);
    const originals=h('details',{class:'photo-disclosure'},h('summary',{},'Original photos & video'),
      h('div',{class:'original-stages'},available.map(s=>h('figure',{},h('a',{href:s.url,target:'_blank',rel:'noopener'},h('img',{src:s.url,alt:s.title,loading:'lazy'})),h('figcaption',{},`${s.title} · ${time(s.at)}`)))),
      o.evidence.filter(e=>e.kind==='clip' && !e.expired).map(e=>h('video',{src:e.url,controls:'',preload:'none'})),
      h('a',{class:'record-tools',href:`#/incident/${o.id}?review=1`},'Detailed record'));
    // Which of the four views this record has. Not attached is about this record, not the recording.
    const checklist=h('ol',{class:'stage-list'},stages.map(s=>{
      const later=s.key==='later' && !s.url && due;
      return h('li',{class:s.url ? 'has' : ''},h('span',{},STAGE_NAMES[s.key]),h('span',{class:'tx2'},s.url ? time(s.at) : later ? `Due ${time(o.followup_at)}` : 'Not attached'));
    }));
    fill(content,h('a',{class:'crumb',href:'#/feed'},icon('arrowLeft',16),'Photos'),
      h('header',{class:'page-head'},h('div',{},h('h1',{id:'page-title',tabindex:'-1'},title),
        h('p',{class:'tx2'},`${o.location || 'Kitchen'} · tracked since ${time(o.created_at)}`)),
        detail.share_ready ? h('a',{class:'btn primary',href:detail.share_url,download:`dish-${o.id}.jpg`},'Download photo') : null),
      detail.share_ready ? h('img',{class:'share-full',src:detail.share_url,alt:'Earlier, handling, left out and two-hour follow-up photos'})
        : h('section',{class:'pending-view'},
          last ? h('figure',{},croppedView({src:last.url,alt:`Saved photo: ${last.title}`,marks:last.box ? [{box:last.box}] : [],minWidth:.42,aspect:1.6,anchor:.62}),
            h('figcaption',{class:'tx2'},`Saved photo from ${time(last.at)}, not a live view. Full scene under Original photos.`)) : null,
          h('div',{class:'pending-facts'},h('p',{class:'photo-status'},status),watching ? checklist : null,
            watching ? h('p',{class:'section-note'},'The share image needs all four views, with the dish in the same spot two hours after Left out.') : null)),
      h('p',{'data-feedback':'','aria-live':'polite'}), correction, originals);
  }
  load();
  return {update(kind){if(kind==='data')load();},destroy(){alive=false;}};
}
