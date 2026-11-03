// Real Chrome via CDP; all traffic is restricted to the three loopback origins.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createPrivateKey, generateKeyPairSync, sign } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const cfg = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const mode = process.argv[3];
const { chromium } = await import(pathToFileURL(path.join(cfg.playwright, 'index.mjs')));
const allowed = new Set([cfg.site, cfg.auth, cfg.api, cfg.privateApi]);
const profile = path.join(cfg.run, `chrome-${mode}`);
await fs.mkdir(profile);
const chrome = spawn(process.env.HOUSE_E2E_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  `--ignore-certificate-errors-spki-list=${cfg.tlsSpki}`,
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
  '--disable-background-networking', '--disable-component-update', '--disable-sync', '--metrics-recording-only',
  '--disable-default-apps', '--no-default-browser-check', 'about:blank',
  `--ignore-certificate-errors-spki-list=${cfg.tlsSpki}`,
], { stdio: ['ignore', 'ignore', 'pipe'] });
let chromeLog = '';
chrome.stderr.on('data', data => { chromeLog += data; });
let browser;
const events = [];
const pages = [];
const blocked = [];
const errors = [];
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const ui = { join: 'Set password & come in', nameTab: 'Name dishes',
  signOut: 'Sign out', signIn: 'Sign in', dispute: 'That wasn’t me',
  disputeSubmit: 'Submit dispute|Send dispute|Dispute', settle: 'Settle|Review dispute',
  them: 'Them|It was them|Confirm Theo', notThem: 'Not them|Not Theo',
  ...cfg.ui };
const rx = key => new RegExp(`^(?:${ui[key]})$`, 'i');

async function screenshot(page, name) {
  const filename = `${String(events.length + 1).padStart(2, '0')}-${name}.png`;
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(cfg.run, filename) });
  const full = filename.replace('.png', '-full.png');
  await page.screenshot({ path: path.join(cfg.run, full), fullPage: true });
  events.push({ step: name, screenshot: filename, full_page: full });
  console.log(`PASS ${name}`);
  await fs.writeFile(path.join(cfg.run, `${mode}-steps.json`), JSON.stringify(events, null, 2));
}

async function newPage() {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1,
    isMobile: true, hasTouch: true, serviceWorkers: 'block' });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (allowed.has(url.origin)) return route.continue();
    blocked.push(route.request().url());
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  pages.push(page);
  page.on('pageerror', error => errors.push(String(error)));
  page.setDefaultTimeout(12000);
  return page;
}

async function request(url, { token, body, method = 'GET', operator = false, headers = {} } = {}) {
  const target = new URL(url, String(url).startsWith('/api/') && operator ? cfg.privateApi : cfg.api);
  assert(allowed.has(target.origin), `Refusing external URL: ${target}`);
  const response = await fetch(target, { method, headers: {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(operator ? { Cookie: cfg.operatorCookie, 'X-CSRF-Token': cfg.csrf } : {}),
    ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers,
  }, body: body ? JSON.stringify(body) : undefined, redirect: 'error' });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  checks.push({ method, path: target.pathname, status: response.status });
  return { status: response.status, data, headers: response.headers };
}

async function ok(url, options) {
  const result = await request(url, options);
  assert(result.status >= 200 && result.status < 300, `${url}: HTTP ${result.status}: ${JSON.stringify(result.data)}`);
  return result.data;
}

async function refused(url, options, statuses = [401, 403], label = 'refused') {
  const result = await request(url, options);
  assert(statuses.includes(result.status), `${url} should refuse (${statuses}), got ${result.status}: ${JSON.stringify(result.data)}`);
  checks.push({ check: label, path: new URL(url, cfg.api).pathname, status: result.status, expected_statuses: statuses });
}

async function showStandings(actor, theo, wren, pick, label) {
  await actor.page.goto(cfg.site + '/');
  await actor.page.getByRole('heading', { name: 'This week', exact: true }).waitFor();
  for (const [name, expected] of [['Theo', theo], ['Wren', wren]]) {
    const row = actor.page.locator('.rankings li').filter({ has: actor.page.locator('.rank-name > span:first-child', { hasText: new RegExp(`^${name}$`) }) });
    if (expected) assert.equal(await row.locator('.points').innerText(), String(expected), `${name} rendered points`);
    else assert.equal(await row.count(), 0, `${name} must not have a positive points row`);
  }
  assert.match(await actor.page.locator('.pick h2').innerText(), pick ? /Theo is in line/ : /No clear pick yet/);
  const source = await week(actor);
  assert.equal(await actor.page.locator('.pick > p').first().innerText(), source.pick.reason, 'render the API explanation verbatim');
  assert.equal(source.pick.points, Math.max(theo, wren));
  assert.equal(source.pick.lead, Math.abs(theo - wren));
  assert.equal(source.pick.state === 'pick', Boolean(pick));
  assert(!/Theo|Wren/.test(source.pick.reason), 'pick reason must not repeat a name');
  assert.match(await actor.page.locator('.duty').innerText(), /Ezzy/);
  await screenshot(actor.page, label);
}

async function session(page) {
  await page.waitForFunction(() => Object.values(localStorage).some(value => {
    try { return !!JSON.parse(value)?.access_token; } catch { return false; }
  }));
  return page.evaluate(() => Object.values(localStorage).map(value => {
    try { return JSON.parse(value); } catch { return null; }
  }).find(value => value?.access_token));
}

async function foundation() {
  const page = await newPage();
  await page.goto(cfg.site + '/__e2e__/auth.html');
  await page.getByLabel('Email', { exact: true }).fill('probe@house-e2e.invalid');
  await page.getByLabel('Password', { exact: true }).fill('probe-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#result').textContent.includes('access_token'));
  const signedIn = await session(page);
  assert.equal(signedIn.user.email, 'probe@house-e2e.invalid');
  await screenshot(page, 'sdk-signed-in');
  await page.getByRole('button', { name: 'Get user', exact: true }).click();
  await page.waitForFunction(() => {
    const r = JSON.parse(document.querySelector('#result').textContent);
    return r.data?.user && !r.data.session;
  });
  await screenshot(page, 'sdk-get-user');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.waitForFunction(old => {
    const r = JSON.parse(document.querySelector('#result').textContent);
    return r.data?.session?.refresh_token && r.data.session.refresh_token !== old;
  }, signedIn.refresh_token);
  const refreshed = await session(page);
  await screenshot(page, 'sdk-refreshed');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.waitForFunction(async () => !(await window.authClient.auth.getSession()).data.session);
  await screenshot(page, 'sdk-signed-out');
  await refused(cfg.auth + '/auth/v1/user', { token: refreshed.access_token });
  await page.getByLabel('Password', { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForFunction(() => JSON.parse(document.querySelector('#result').textContent).error);
  assert.equal((await page.evaluate(() => window.authClient.auth.getSession())).data.session, null);
  await screenshot(page, 'sdk-wrong-password-refused');
}

async function operatorJoin(name) {
  const residentId = cfg.fixture.residents[name];
  const result = await ok(`/api/league/residents/${residentId}/join-link`, { method: 'POST', operator: true });
  const token = new URL(result.url).hash.slice(1);
  assert.equal(typeof token, 'string', `Operator join response has no token: ${JSON.stringify(result)}`);
  return token;
}

async function join(name) {
  const page = await newPage();
  const token = await operatorJoin(name);
  await page.goto(cfg.site + '/join#' + token);
  await screenshot(page, `join-${name}-opened`);
  const passwords = page.locator('input[type=password]');
  await passwords.first().waitFor();
  for (let i = 0; i < await passwords.count(); i++) await passwords.nth(i).fill('house-e2e-password');
  await page.getByRole('button', { name: rx('join') }).click();
  const auth = await session(page);
  assert.equal(auth.user.email, `${name}@highest-house.ezzyrappeport.com`);
  // Let the site's initial authenticated request finish before this independent
  // probe: a concurrent cold JWKS fetch deliberately returns a fast 503.
  await page.getByRole('link', { name: 'This week', exact: true }).waitFor();
  const me = await ok('/house/api/me', { token: auth.access_token });
  assert.equal(Number(me.resident?.id ?? me.resident_id ?? me.id), cfg.fixture.residents[name]);
  await screenshot(page, `join-${name}-signed-in`);
  return { page, token: auth.access_token, name };
}

async function week(actor, start) {
  return ok('/house/api/week' + (start ? '?start=' + start : ''), { token: actor.token });
}

function residentId(value) {
  const id = value == null ? null : typeof value === 'object'
    ? ('resident_id' in value ? value.resident_id : value.resident?.id ?? value.id) : value;
  return id == null ? null : Number(id);
}

function points(state, name) {
  const row = state.standings.find(row => residentId(row) === cfg.fixture.residents[name]);
  assert(row, `No standings row for ${name}`);
  return row.points;
}

function scores(state, theo, wren, pick) {
  assert.equal(points(state, 'theo'), theo, 'Theo points');
  assert.equal(points(state, 'wren'), wren, 'Wren points');
  for (const name of Object.keys(cfg.fixture.residents).filter(n => !['theo', 'wren'].includes(n))) assert.equal(points(state, name), 0, `${name} points`);
  assert.equal(residentId(state.pick), pick ? cfg.fixture.residents[pick] : null, 'weekly pick');
  checks.push({ check: 'score-and-pick', expected: { theo, wren, pick }, observed: { standings: state.standings, pick: state.pick, on_duty: state.on_duty } });
}

function findVisit(state, fixtureIndex) {
  const expected = cfg.fixture.visits[fixtureIndex].dish_ids;
  const visit = state.visits.find(v => expected.every(id => v.dish_ids.includes(id)));
  assert(visit, `Fixture visit ${fixtureIndex + 1} missing from /week: ${JSON.stringify(state.visits)}`);
  assert.deepEqual([...visit.dish_ids].sort(), [...expected].sort(), 'Arrival grouping must match exactly');
  return visit;
}

async function card(page, visit) {
  const result = page.locator(`[data-dish-id="${visit.id}"]`);
  if (visit.evidence === 'weak' && !await result.count()) {
    await page.getByRole('button', {name: /with weak evidence/}).click();
  }
  await result.first().waitFor();
  assert.equal(await result.count(), 1, `Need one data-dish-id visit card for ${visit.id}`);
  return result;
}

async function nameVisit(actor, visit, name, guess = false) {
  await actor.page.getByRole('link', { name: rx('nameTab') }).click();
  const item = await card(actor.page, visit);
  const button = guess ? item.getByRole('button', { name: new RegExp(cfg.ui.guess || '^Camera thinks Theo — tap to confirm$', 'i') })
    : item.getByRole('button', { name: new RegExp(`^${name}$`, 'i') });
  if (guess) await screenshot(actor.page, 'camera-guess-awaiting-one-tap-confirmation');
  const response = actor.page.waitForResponse(r => r.url().endsWith(`/house/api/visits/${visit.id}/name`) && r.request().method() === 'POST');
  const refreshed = actor.page.waitForResponse(r => new URL(r.url()).pathname === '/house/api/week' && r.request().method() === 'GET');
  await button.click();
  const saved = await response;
  assert(saved.ok(), `UI naming failed: ${saved.status()} ${await saved.text()}`);
  const updated = await saved.json();
  assert.equal(residentId(updated.resident), cfg.fixture.residents[name.toLowerCase()]);
  assert.equal(residentId(updated.named_by), cfg.fixture.residents[actor.name]);
  assert.deepEqual(updated.dish_ids, visit.dish_ids);
  checks.push({ check: 'UI visit named atomically', visit: updated });
  const body = saved.request().postDataJSON();
  assert.equal(Number(body.resident_id), cfg.fixture.residents[name.toLowerCase()]);
  assert((await refreshed).ok(), 'Name must refresh the real week');
  await item.waitFor({ state: 'detached' });
  await screenshot(actor.page, `named-${visit.id}-${name.toLowerCase()}${guess ? '-camera-guess' : ''}`);
}

async function dispute(actor, visit, label) {
  await actor.page.reload();
  await actor.page.getByRole('link', { name: 'My dishes', exact: true }).click();
  const item = await card(actor.page, visit);
  const response = actor.page.waitForResponse(r => r.url().endsWith(`/visits/${visit.id}/dispute`) && r.request().method() === 'POST');
  await item.getByRole('button', { name: rx('dispute') }).click();
  const note = actor.page.locator('textarea');
  if (await note.count()) await note.fill('Synthetic E2E dispute: please review this visit.');
  const submit = actor.page.getByRole('button', { name: rx('disputeSubmit') });
  // A one-tap dispute may already have saved; otherwise submit its form.
  if (await note.count()) {
    await submit.click();
  }
  assert((await response).ok(), 'Dispute UI failed');
  assert.equal((await week(actor)).visits.find(v => v.id === visit.id).dispute.state, 'open', 'Dispute not persisted');
  await item.getByText('Disputed · open', { exact: true }).waitFor();
  await screenshot(actor.page, label);
}

async function settle(actor, visit, decision) {
  await actor.page.reload();
  await actor.page.getByRole('link', { name: 'My dishes', exact: true }).click();
  const item = await card(actor.page, visit);
  const open = item.getByRole('button', { name: rx('settle') });
  if (await open.count()) await open.click();
  const response = actor.page.waitForResponse(r => r.url().endsWith(`/visits/${visit.id}/settle`) && r.request().method() === 'POST');
  await actor.page.getByRole('button', { name: rx(decision === 'them' ? 'them' : 'notThem') }).click();
  assert((await response).ok(), 'Settlement UI failed');
  const updated = await (await response).json();
  assert.equal(updated.dispute.state, decision === 'them' ? 'upheld' : 'overturned');
  assert.equal(residentId(updated.dispute.settled_by), cfg.fixture.residents.milo);
  assert.equal(residentId(updated.resident), decision === 'them' ? cfg.fixture.residents.theo : null);
  checks.push({ check: 'UI dispute settled', decision, visit: updated });
  await item.waitFor({ state: 'detached' });
  await actor.page.getByText('All settled here.', { exact: true }).waitFor();
  await screenshot(actor.page, `settled-${decision}`);
}

function jwtWith(token, key, patch = {}) {
  const [head, payload] = token.split('.').slice(0, 2).map(v => JSON.parse(Buffer.from(v, 'base64url')));
  const data = [head, { ...payload, ...patch }].map(v => Buffer.from(JSON.stringify(v)).toString('base64url')).join('.');
  return data + '.' + sign('sha256', Buffer.from(data), { key, dsaEncoding: 'ieee-p1363' }).toString('base64url');
}

async function clockOffset(seconds) {
  await fs.writeFile(cfg.clockFile + '.next', String(seconds));
  await fs.rename(cfg.clockFile + '.next', cfg.clockFile);
}

async function journey() {
  const wren = await join('wren');
  const theo = await join('theo');
  const milo = await join('milo');
  await wren.page.getByRole('link',{name:'Help camera',exact:true}).click();
  await wren.page.getByRole('heading',{name:'Is this a dish?',exact:true}).waitFor();
  await wren.page.getByRole('button',{name:'Yes',exact:true}).click();
  await wren.page.getByText('Saved. Another housemate will check it.',{exact:true}).waitFor();
  await screenshot(wren.page,'help-answer-pending-consensus');
  await wren.page.getByRole('button',{name:'Undo',exact:true}).click();
  await wren.page.getByRole('heading',{name:'Is this a dish?',exact:true}).waitFor();
  await wren.page.getByRole('link',{name:'This week',exact:true}).click();
  let state = await week(wren);
  assert.equal(state.start_week, cfg.fixture.closed_start, 'Persisted first-run week');
  assert.equal(state.weeks.length, 3, 'History includes only weeks since the league started');
  assert(state.weeks.every(w => w.start >= state.start_week), 'No pre-start week in navigation');
  const beforeStart = new Date(state.start_week + 'T12:00:00Z');
  beforeStart.setUTCDate(beforeStart.getUTCDate() - 7);
  const preStart = beforeStart.toISOString().slice(0, 10);
  await refused('/house/api/week?start=' + preStart, { token: wren.token }, [400]);
  await refused('/api/league/week?start=' + preStart, { operator: true }, [400]);
  const operatorPage = await newPage();
  await operatorPage.context().addCookies(cfg.operatorCookie.split('; ').map(cookie => {
    const split = cookie.indexOf('=');
    return { name: cookie.slice(0, split), value: cookie.slice(split + 1), url: cfg.privateApi, secure: true };
  }));
  await operatorPage.goto(cfg.privateApi + '/#/proof');
  const weekSelector = operatorPage.getByLabel('Week of', { exact: true });
  await weekSelector.waitFor();
  assert.deepEqual(await weekSelector.locator('option').evaluateAll(options => options.map(o => o.value)),
    state.weeks.map(w => w.start), 'Operator selector uses the same bounded history');
  await operatorPage.getByText(/^League started /).waitFor();
  await screenshot(operatorPage, 'operator-start-bounded-history');
  await operatorPage.goto(cfg.privateApi+'/#/setup/learning');
  await operatorPage.getByText('No learning runs yet.',{exact:true}).waitFor();
  await screenshot(operatorPage,'operator-learning-panel');
  await operatorPage.close();
  scores(state, 0, 0, null);
  assert.equal(state.rules.min_points, 4);
  assert.equal(state.rules.min_lead, 2);
  const duty = state.on_duty;
  assert.equal(residentId(duty), cfg.fixture.residents.ezzy, 'Ezzy must be the current duty person');
  const multi = findVisit(state, 0);
  const single = findVisit(state, 1);
  const pot = findVisit(state, 2);
  assert.equal(multi.points, 4);
  assert.equal(single.points, 1);
  assert.equal(residentId(multi.guess), cfg.fixture.residents.theo, 'Camera guess Theo required');
  assert(multi.filmstrip_url, 'Shared arrival filmstrip URL missing');
  // Real verdict response -> production parser -> refreshed review history.
  await wren.page.getByRole('link', { name: rx('nameTab') }).click();
  const review = await card(wren.page, multi);
  const verdictResponse = wren.page.waitForResponse(r => r.url().endsWith(`/visits/${multi.id}/verdict`) && r.request().method() === 'POST');
  await review.getByRole('button', { name: 'Not a dish', exact: true }).click();
  const savedVerdict = await verdictResponse;
  assert.equal(savedVerdict.status(), 200);
  assert.equal((await savedVerdict.json()).verdict.kind, 'not_dish');
  await wren.page.getByText(/Reviewed this week/).click();
  await wren.page.getByText('Marked not a dish by Wren', { exact: true }).waitFor();
  for (const actor of [wren, theo, milo]) {
    const excluded = await week(actor);
    assert(!excluded.visits.some(v => v.id === multi.id));
    assert(!excluded.dishes.some(d => multi.dish_ids.includes(d.id)));
    assert.equal(excluded.standings.reduce((n,r) => n+r.points,0), 0);
    assert.equal(excluded.verdicts.find(v => v.visit_id === multi.id).kind, 'not_dish');
    await actor.page.goto(cfg.site + '/name');
    await actor.page.getByText(/Reviewed this week/).click();
    await actor.page.getByText('Marked not a dish by Wren', { exact: true }).waitFor();
  }
  await screenshot(wren.page, 'global-verdict-history');
  await ok(`/api/league/visits/${multi.id}/undo-verdict`, {method:'POST',operator:true,body:{}});
  state = await week(wren);
  assert(findVisit(state,0));
  assert.equal(state.verdicts.length,0);
  await wren.page.goto(cfg.site + '/');
  await nameVisit(wren, multi, 'Theo', true);
  // The fair pot precedes the weak cup now. Exercise the real private skip,
  // then opt into weak evidence; another resident can still name the pot later.
  await wren.page.getByRole('link', {name: rx('nameTab')}).click();
  const potReview = await card(wren.page, pot);
  const skippedPot = wren.page.waitForResponse(r => r.url().endsWith(`/visits/${pot.id}/verdict`) && r.request().method() === 'POST');
  await potReview.getByRole('button', {name: 'Can’t tell', exact: true}).click();
  assert.equal((await skippedPot).status(), 200);
  await potReview.waitFor({state: 'detached'});
  assert(!(await week(wren)).visits.some(v => v.id === pot.id));
  assert((await week(milo)).visits.some(v => v.id === pot.id));
  await nameVisit(wren, single, 'Wren');
  state = await week(wren);
  scores(state, 4, 1, 'theo');
  await refused(`/house/api/visits/${multi.id}/verdict`, {token:theo.token,method:'POST',body:{verdict:'not_dish'}}, [409], 'resident cannot exclude named visit');

  assert.equal(state.unnamed_count, 0, 'The only remaining unnamed visit was privately skipped');
  await showStandings(wren, 4, 1, 'theo', 'standings-theo-four-wren-one');
  const disputePath = `/house/api/visits/${multi.id}/dispute`;
  const settlePath = `/house/api/visits/${multi.id}/settle`;
  await refused(disputePath, { token: wren.token, method: 'POST', body: { note: 'Not my visit' } }, [403], 'only the named person may dispute');
  await dispute(theo, multi, 'theo-disputed');
  state = await week(wren);
  scores(state, 0, 1, null);
  assert.deepEqual(state.on_duty, duty, 'No pick retains the current duty person');
  await showStandings(wren, 0, 1, null, 'disputed-visit-zero-points');
  for (const actor of [wren, theo]) await refused(settlePath, { token: actor.token, method: 'POST', body: { decision: 'them' } }, [403], actor === wren ? 'namer cannot settle' : 'disputer cannot settle');
  await settle(milo, multi, 'them');
  scores(await week(wren), 4, 1, 'theo');
  await showStandings(wren, 4, 1, 'theo', 'upheld-points-restored');
  await dispute(theo, multi, 'theo-disputed-again');
  scores(await week(wren), 0, 1, null);
  await settle(milo, multi, 'not_them');
  state = await week(wren);
  scores(state, 0, 1, null);
  const reset = state.visits.find(v => v.id === multi.id);
  assert.equal(residentId(reset.resident), null, 'Not-them must return visit to unnamed');
  assert.equal(state.unnamed_count, 3);
  assert.equal(state.unreviewed_count, 1);
  await showStandings(wren, 0, 1, null, 'overturned-back-to-unnamed');
  // Exercise the lead threshold independently of the minimum-points threshold.
  await wren.page.reload();
  await nameVisit(wren, multi, 'Theo');
  await milo.page.reload();
  await nameVisit(milo, pot, 'Wren');
  state = await week(wren);
  scores(state, 4, 3, null);
  assert.deepEqual(state.on_duty, duty);
  await showStandings(wren, 4, 3, null, 'lead-one-no-pick');
  const closed = await week(wren, cfg.fixture.closed_start);
  const closedVisit = findVisit(closed, 3);
  assert.equal(closed.week.closed, true);
  await refused(`/house/api/visits/${closedVisit.id}/name`, { token: wren.token, method: 'POST', body: { resident_id: cfg.fixture.residents.wren } }, [409]);
  assert.equal(closedVisit.can_name, false, 'Closed-week visit must not offer naming');
  const past = wren.page.getByRole('button', { name: /^(Past weeks|History)$/i }).or(wren.page.getByRole('link', { name: /^(Past weeks|History)$/i }));
  await past.click();
  const closedWeek = wren.page.locator('.week-choice').filter({ hasText: new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: closed.week.timezone }).format(new Date(cfg.fixture.closed_start + 'T12:00:00Z')) });
  const pastResponse = wren.page.waitForResponse(r => r.url().endsWith('/house/api/week?start=' + cfg.fixture.closed_start));
  await closedWeek.click();
  assert((await pastResponse).ok());
  await wren.page.locator('.past-detail').getByText('Closed', { exact: true }).waitFor();
  assert.equal(await wren.page.locator('.week-choice').count(), 3, 'Past-weeks UI respects the start boundary');
  assert.equal(await wren.page.locator('.past-detail button').count(), 0, 'Closed history must not offer naming');
  await screenshot(wren.page, 'past-weeks-closed-naming-refused');
  await ok('/house/api/me', { token: theo.token });
  const key = createPrivateKey(await fs.readFile(cfg.privateKey));
  await refused('/house/api/me', { token: jwtWith(theo.token, key, { exp: Math.floor(Date.now() / 1000) - 10 }) }, [401], 'expired JWT');
  await refused('/house/api/me', { token: jwtWith(theo.token, key, { aud: 'wrong-audience' }) }, [401], 'wrong JWT audience');
  await refused('/house/api/me', { token: jwtWith(theo.token, key, { iss: 'http://wrong-issuer.invalid/auth/v1' }) }, [401], 'wrong JWT issuer');
  await refused('/house/api/me', { token: jwtWith(theo.token, generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey) }, [401], 'forged JWT signature');
  await refused('/house/api/me', {}, [401], 'missing JWT');
  await refused('/api/dashboard', { operator: true, headers: { 'Cf-Connecting-IP': '203.0.113.20' } }, [403, 404], 'tunnel cannot reach operator dashboard');
  await refused(cfg.api + '/api/dashboard', {operator:true}, [404], 'tunnel rejects operator session without CF header');
  await refused(cfg.api + '/api/health', {}, [404], 'tunnel rejects private health');
  await ok('/house/api/me', { token: theo.token, headers: { 'Cf-Connecting-IP': '203.0.113.20' } });
  const freshVisit = findVisit(await week(wren), 0);
  const media = new URL(freshVisit.filmstrip_url, cfg.api);
  assert(allowed.has(media.origin));
  const good = await request(media);
  assert.equal(good.status, 200, 'Signed media must work before expiry');
  assert(good.headers.get('content-type').startsWith('image/'));
  assert.equal(good.headers.get('cache-control'), 'private, no-store');
  const other = new URL(media);
  other.pathname = other.pathname.replace(/[^/]+$/, cfg.fixture.visits[1].evidence_id);
  await refused(other, undefined, [403], 'signed media bound to evidence id');
  const expires = Number(media.searchParams.get('exp'));
  assert(Number.isFinite(expires) && expires > 0, 'Signed media exp missing');
  assert(expires > Date.now() / 1000, 'Fresh signed media URL already expired');
  // The child's real verifier sees a wall clock beyond its own signed exp.
  // Restore and fetch again so refusal cannot pass because media disappeared.
  await clockOffset(expires - Math.floor(Date.now() / 1000) + 2);
  try { await refused(media, undefined, [403], 'signed media expired'); }
  finally { await clockOffset(0); }
  assert.equal((await request(media)).status, 200, 'Media must work again when fixture clock is restored');
  await screenshot(theo.page, 'auth-media-and-tunnel-refusals-checked');
  await theo.page.getByRole('button', { name: rx('signOut') }).click();
  await theo.page.waitForFunction(() => !Object.values(localStorage).some(v => { try { return !!JSON.parse(v)?.access_token; } catch { return false; } }));
  await theo.page.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
  await screenshot(theo.page, 'signed-out');
  assert.equal(await theo.page.getByLabel('Your name').inputValue(), 'theo');
  await theo.page.locator('input[type=password]').fill('house-e2e-password');
  await theo.page.getByRole('button', { name: rx('signIn') }).click();
  assert.equal((await session(theo.page)).user.email, 'theo@highest-house.ezzyrappeport.com');
  await theo.page.getByRole('heading', { name: 'This week', exact: true }).waitFor();
  await screenshot(theo.page, 'remembered-name-signed-in-again');
}

try {
  let devtools;
  for (let attempt = 0; attempt < 150; attempt++) {
    if (chrome.exitCode !== null) throw new Error(`Chrome exited: ${chromeLog}`);
    try { devtools = await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8'); break; } catch { await delay(100); }
  }
  assert(devtools, 'Chrome CDP startup timed out');
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${devtools.split('\n')[0]}`);
  console.log(`Chrome ${browser.version()} via CDP, viewport 390x844`);
  if (mode === 'foundation') await foundation();
  else if (mode === 'journey') await journey();
  else throw new Error('Unknown browser mode ' + mode);
  assert.deepEqual(blocked, [], 'Product attempted external network requests');
  assert.deepEqual(errors, [], 'Browser page errors');
} catch (error) {
  for (let i = 0; i < pages.length; i++) {
    await pages[i].screenshot({ path: path.join(cfg.run, `${mode}-FAILED-${i + 1}.png`), fullPage: true }).catch(() => {});
  }
  console.error(error.stack || error);
  process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(cfg.run, `${mode}-network.json`), JSON.stringify({ blocked, errors }, null, 2));
  await fs.writeFile(path.join(cfg.run, `${mode}-checks.json`), JSON.stringify(checks, null, 2));
  if (browser) await browser.close().catch(() => {});
  if (chrome.exitCode === null) chrome.kill('SIGTERM');
  await fs.writeFile(path.join(cfg.run, `${mode}-chrome.log`), chromeLog);
}
