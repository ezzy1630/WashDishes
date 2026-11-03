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
const allowed = new Set([cfg.site, cfg.auth, cfg.api]);
const profile = path.join(cfg.run, `chrome-${mode}`);
await fs.mkdir(profile);
const chrome = spawn(process.env.HOUSE_E2E_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
  '--disable-background-networking', '--disable-component-update', '--disable-sync', '--metrics-recording-only',
  '--disable-default-apps', '--no-default-browser-check', 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] });
let chromeLog = '';
chrome.stderr.on('data', data => { chromeLog += data; });
let browser;
const events = [];
const pages = [];
const blocked = [];
const errors = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const ui = { join: 'Join|Set password|Create account|Continue', nameTab: 'Name visits|Name dishes|Unnamed',
  signOut: 'Sign out|Log out', signIn: 'Sign in|Log in', dispute: 'Dispute',
  disputeSubmit: 'Submit dispute|Send dispute|Dispute', settle: 'Settle|Review dispute',
  them: 'Them|It was them|Confirm Andrew', notThem: 'Not them|Not Andrew',
  ...cfg.ui };
const rx = key => new RegExp(`^(?:${ui[key]})$`, 'i');

async function screenshot(page, name) {
  const filename = `${String(events.length + 1).padStart(2, '0')}-${name}.png`;
  await page.screenshot({ path: path.join(cfg.run, filename) });
  events.push({ step: name, screenshot: filename });
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
  const target = new URL(url, cfg.api);
  assert(allowed.has(target.origin), `Refusing external URL: ${target}`);
  const response = await fetch(target, { method, headers: {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(operator ? { Cookie: cfg.operatorCookie, 'X-CSRF-Token': cfg.csrf } : {}),
    ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers,
  }, body: body ? JSON.stringify(body) : undefined, redirect: 'error' });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: response.status, data, headers: response.headers };
}

async function ok(url, options) {
  const result = await request(url, options);
  assert(result.status >= 200 && result.status < 300, `${url}: HTTP ${result.status}: ${JSON.stringify(result.data)}`);
  return result.data;
}

async function refused(url, options, statuses = [401, 403]) {
  const result = await request(url, options);
  assert(statuses.includes(result.status), `${url} should refuse (${statuses}), got ${result.status}: ${JSON.stringify(result.data)}`);
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
  // Discover only the operator join route's spelling, never an alternate auth path.
  const routes = cfg.fixture.routes.filter(r => r.methods.includes('POST') && r.path.startsWith('/api/') && /house|join/.test(r.path) && /join/.test(r.path));
  assert.equal(routes.length, 1, `Expected one operator join route, found ${JSON.stringify(routes)}. Check docs/house-api.md.`);
  const route = routes[0];
  const residentId = cfg.fixture.residents[name];
  const endpoint = route.path.replace(/\{(?:resident_id|id)\}/g, residentId);
  assert(!endpoint.includes('{'), `Unknown operator route parameter: ${endpoint}`);
  const body = route.body_fields.length ? { resident_id: residentId } : undefined;
  const result = await ok(endpoint, { method: 'POST', body, operator: true });
  const link = result.url || result.join_url || result.link;
  const token = result.token || result.join_token || (link && new URL(link, cfg.site).hash.slice(1));
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
  const me = await ok('/house/api/me', { token: auth.access_token });
  assert.equal(Number(me.resident?.id ?? me.resident_id ?? me.id), cfg.fixture.residents[name]);
  await screenshot(page, `join-${name}-signed-in`);
  return { page, token: auth.access_token };
}

async function week(actor, start) {
  return ok('/house/api/week' + (start ? '?start=' + start : ''), { token: actor.token });
}

function residentId(value) {
  return value == null ? null : Number(typeof value === 'object' ? value.resident_id ?? value.resident?.id ?? value.id : value);
}

function points(state, name) {
  const row = state.standings.find(row => residentId(row) === cfg.fixture.residents[name]);
  assert(row, `No standings row for ${name}`);
  return row.points;
}

function scores(state, andrew, jadon, pick) {
  assert.equal(points(state, 'andrew'), andrew, 'Andrew points');
  assert.equal(points(state, 'jadon'), jadon, 'Jadon points');
  for (const name of Object.keys(cfg.fixture.residents).filter(n => !['andrew', 'jadon'].includes(n))) assert.equal(points(state, name), 0, `${name} points`);
  assert.equal(residentId(state.pick), pick ? cfg.fixture.residents[pick] : null, 'weekly pick');
}

function findVisit(state, fixtureIndex) {
  const expected = cfg.fixture.visits[fixtureIndex].dish_ids;
  const visit = state.visits.find(v => expected.every(id => v.dish_ids.includes(id)));
  assert(visit, `Fixture visit ${fixtureIndex + 1} missing from /week: ${JSON.stringify(state.visits)}`);
  assert.deepEqual([...visit.dish_ids].sort(), [...expected].sort(), 'Arrival grouping must match exactly');
  return visit;
}

async function card(page, visit) {
  const result = page.locator(`[data-visit-id="${visit.id}"]`).or(page.getByRole('article').filter({ hasText: visit.title }));
  await result.first().waitFor();
  assert.equal(await result.count(), 1, `Need one visit card for ${visit.id}. Set data-visit-id or article title.`);
  return result;
}

async function nameVisit(actor, visit, name, guess = false) {
  if (!await actor.page.locator(`[data-visit-id="${visit.id}"]`).count()) {
    const tab = actor.page.getByRole('button', { name: rx('nameTab') }).or(actor.page.getByRole('link', { name: rx('nameTab') }));
    if (await tab.count()) await tab.click();
  }
  const item = await card(actor.page, visit);
  const button = guess ? item.getByRole('button', { name: new RegExp(cfg.ui.guess || '^(?:Confirm Andrew|Yes,? Andrew|Andrew.*(?:guess|camera)|Looks like Andrew)$', 'i') }).or(item.locator('button[data-guess], button.guess'))
    : item.getByRole('button', { name: new RegExp(`^${name}$`, 'i') });
  const response = actor.page.waitForResponse(r => r.url().endsWith(`/house/api/visits/${visit.id}/name`) && r.request().method() === 'POST');
  await button.click();
  const saved = await response;
  assert(saved.ok(), `UI naming failed: ${saved.status()} ${await saved.text()}`);
  const body = saved.request().postDataJSON();
  assert.equal(Number(body.resident_id), cfg.fixture.residents[name.toLowerCase()]);
  await screenshot(actor.page, `named-${visit.id}-${name.toLowerCase()}${guess ? '-camera-guess' : ''}`);
}

async function dispute(actor, visit, label) {
  await actor.page.reload();
  const mine = actor.page.getByRole('button', { name: /^(My dishes|Mine)$/i }).or(actor.page.getByRole('link', { name: /^(My dishes|Mine)$/i }));
  if (await mine.count()) await mine.click();
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
  assert((await week(actor)).visits.find(v => v.id === visit.id).dispute, 'Dispute not persisted');
  await screenshot(actor.page, label);
}

async function settle(actor, visit, decision) {
  await actor.page.reload();
  const disputes = actor.page.getByRole('button', { name: /^Disputes$/i }).or(actor.page.getByRole('link', { name: /^Disputes$/i }));
  if (await disputes.count()) await disputes.click();
  const item = await card(actor.page, visit);
  const open = item.getByRole('button', { name: rx('settle') });
  if (await open.count()) await open.click();
  const response = actor.page.waitForResponse(r => r.url().endsWith(`/visits/${visit.id}/settle`) && r.request().method() === 'POST');
  await actor.page.getByRole('button', { name: rx(decision === 'them' ? 'them' : 'notThem') }).click();
  assert((await response).ok(), 'Settlement UI failed');
  await screenshot(actor.page, `settled-${decision}`);
}

function jwtWith(token, key, patch = {}) {
  const [head, payload] = token.split('.').map(v => JSON.parse(Buffer.from(v, 'base64url')));
  const data = [head, { ...payload, ...patch }].map(v => Buffer.from(JSON.stringify(v)).toString('base64url')).join('.');
  return data + '.' + sign('sha256', Buffer.from(data), { key, dsaEncoding: 'ieee-p1363' }).toString('base64url');
}

async function clockOffset(seconds) {
  await fs.writeFile(cfg.clockFile + '.next', String(seconds));
  await fs.rename(cfg.clockFile + '.next', cfg.clockFile);
}

async function journey() {
  const jadon = await join('jadon');
  const andrew = await join('andrew');
  const daniel = await join('daniel');
  let state = await week(jadon);
  scores(state, 0, 0, null);
  assert.equal(state.rules.min_points, 4);
  assert.equal(state.rules.min_lead, 2);
  const duty = state.on_duty;
  assert(duty != null, 'Seed a current duty person through the merged Lane B store API in seed.py; a null duty would make retention checks vacuous');
  const multi = findVisit(state, 0);
  const single = findVisit(state, 1);
  assert.equal(multi.points, 4);
  assert.equal(single.points, 1);
  assert.equal(residentId(multi.guess), cfg.fixture.residents.andrew, 'Camera guess Andrew required');
  assert(multi.filmstrip_url, 'Shared arrival filmstrip URL missing');
  await nameVisit(jadon, multi, 'Andrew', true);
  await nameVisit(jadon, single, 'Jadon');
  state = await week(jadon);
  scores(state, 4, 1, 'andrew');
  assert.equal(state.unnamed_count, 1, 'One current-week visit remains unnamed');
  await jadon.page.reload();
  await screenshot(jadon.page, 'standings-andrew-four-jadon-one');
  const disputePath = `/house/api/visits/${multi.id}/dispute`;
  const settlePath = `/house/api/visits/${multi.id}/settle`;
  await refused(disputePath, { token: jadon.token, method: 'POST', body: { note: 'Not my visit' } });
  await dispute(andrew, multi, 'andrew-disputed');
  state = await week(jadon);
  scores(state, 0, 1, null);
  assert.deepEqual(state.on_duty, duty, 'No pick retains the current duty person');
  for (const actor of [jadon, andrew]) await refused(settlePath, { token: actor.token, method: 'POST', body: { decision: 'them' } });
  await settle(daniel, multi, 'them');
  scores(await week(jadon), 4, 1, 'andrew');
  await dispute(andrew, multi, 'andrew-disputed-again');
  scores(await week(jadon), 0, 1, null);
  await settle(daniel, multi, 'not_them');
  state = await week(jadon);
  scores(state, 0, 1, null);
  const reset = state.visits.find(v => v.id === multi.id);
  assert.equal(residentId(reset.resident), null, 'Not-them must return visit to unnamed');
  assert.equal(state.unnamed_count, 2);
  // Exercise the lead threshold independently of the minimum-points threshold.
  await jadon.page.reload();
  await nameVisit(jadon, multi, 'Andrew');
  await nameVisit(jadon, findVisit(state, 2), 'Jadon');
  state = await week(jadon);
  scores(state, 4, 3, null);
  assert.deepEqual(state.on_duty, duty);
  await screenshot(jadon.page, 'lead-one-no-pick');
  const closed = await week(jadon, cfg.fixture.closed_start);
  const closedVisit = findVisit(closed, 3);
  await refused(`/house/api/visits/${closedVisit.id}/name`, { token: jadon.token, method: 'POST', body: { resident_id: cfg.fixture.residents.jadon } }, [400, 403, 409]);
  assert.equal(closedVisit.can_name, false, 'Closed-week visit must not offer naming');
  const past = jadon.page.getByRole('button', { name: /^(Past weeks|History)$/i }).or(jadon.page.getByRole('link', { name: /^(Past weeks|History)$/i }));
  await past.click();
  await screenshot(jadon.page, 'past-weeks-closed-naming-refused');
  await ok('/house/api/me', { token: andrew.token });
  const key = createPrivateKey(await fs.readFile(cfg.privateKey));
  await refused('/house/api/me', { token: jwtWith(andrew.token, key, { exp: Math.floor(Date.now() / 1000) - 10 }) });
  await refused('/house/api/me', { token: jwtWith(andrew.token, key, { aud: 'wrong-audience' }) });
  await refused('/house/api/me', { token: jwtWith(andrew.token, key, { iss: 'http://wrong-issuer.invalid/auth/v1' }) });
  await refused('/house/api/me', { token: jwtWith(andrew.token, generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).privateKey) });
  await refused('/house/api/me', {});
  await refused('/api/dashboard', { operator: true, headers: { 'Cf-Connecting-IP': '203.0.113.20' } });
  const freshVisit = findVisit(await week(jadon), 0);
  const media = new URL(freshVisit.filmstrip_url, cfg.api);
  assert(allowed.has(media.origin));
  const good = await request(media);
  assert.equal(good.status, 200, 'Signed media must work before expiry');
  assert(good.headers.get('content-type').startsWith('image/'));
  const other = new URL(media);
  other.pathname = other.pathname.replace(/[^/]+$/, cfg.fixture.visits[1].evidence_id);
  await refused(other, undefined, [401, 403, 404, 410]);
  const expires = Number(media.searchParams.get('exp'));
  assert(Number.isFinite(expires) && expires > 0, 'Signed media exp missing');
  assert(expires > Date.now() / 1000, 'Fresh signed media URL already expired');
  // The child's real verifier sees a wall clock beyond its own signed exp.
  // Restore and fetch again so refusal cannot pass because media disappeared.
  await clockOffset(expires - Math.floor(Date.now() / 1000) + 2);
  try { await refused(media, undefined, [401, 403, 410]); }
  finally { await clockOffset(0); }
  assert.equal((await request(media)).status, 200, 'Media must work again when fixture clock is restored');
  await screenshot(andrew.page, 'auth-media-and-tunnel-refusals-checked');
  await andrew.page.getByRole('button', { name: rx('signOut') }).click();
  await andrew.page.waitForFunction(() => !Object.values(localStorage).some(v => { try { return !!JSON.parse(v)?.access_token; } catch { return false; } }));
  await screenshot(andrew.page, 'signed-out');
  const picker = andrew.page.getByRole('combobox');
  if (await picker.count()) await picker.selectOption({ label: 'Andrew' });
  else await andrew.page.getByRole('button', { name: /^Andrew$/i }).click();
  await andrew.page.locator('input[type=password]').fill('house-e2e-password');
  await andrew.page.getByRole('button', { name: rx('signIn') }).click();
  assert.equal((await session(andrew.page)).user.email, 'andrew@highest-house.ezzyrappeport.com');
  await screenshot(andrew.page, 'name-picker-signed-in-again');
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
  if (browser) await browser.close().catch(() => {});
  if (chrome.exitCode === null) chrome.kill('SIGTERM');
  await fs.writeFile(path.join(cfg.run, `${mode}-chrome.log`), chromeLog);
}
