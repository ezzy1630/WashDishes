import assert from 'node:assert/strict';
import { test } from 'node:test';

// Exercise the controller's browser boundary with explicit media events. No
// decoding/network timing is involved in checking recovery after a failed load.
class Element extends EventTarget {
  constructor(tag) {
    super(); this.tagName = tag; this.children = []; this.attributes = new Map();
    this.currentTime = 0; this.duration = 10; this.paused = true;
  }
  append(...children) { this.children.push(...children); for (const child of children) child.parent = this; }
  setAttribute(key, value) { this.attributes.set(key, value); }
  removeAttribute(key) { this.attributes.delete(key); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  load() {}
  pause() { this.paused = true; }
}

const videos = [];
globalThis.Node = Element;
globalThis.document = {
  createElement(tag) { const el = new Element(tag); if (tag === 'video') videos.push(el); return el; },
  createTextNode(text) { return Object.assign(new Element('#text'), { textContent: text }); },
};
globalThis.location = { hostname: 'localhost', origin: 'http://localhost', search: '?mock=1' };
globalThis.sessionStorage = { getItem: () => 'fixture' };
const chunk = {
  url: 'http://localhost/house/footage/same.mp4', start: 100, end: 110,
  offset: 2, previous: null, next: null, expires: Date.now() / 1000 + 300,
  clock_estimated: true,
};
globalThis.fetch = async () => new Response(JSON.stringify(chunk));
const { recordedPlayer } = await import('../house-site/js/footage-player.js');
const settle = () => new Promise(resolve => setImmediate(resolve));

for (const failure of ['timeout', 'media error']) test(`the same recording can be retried after ${failure}`, async t => {
  videos.length = 0;
  const statuses = [];
  const player = recordedPlayer({ status: text => statuses.push(text), clock() {}, changed() {} });
  t.after(() => player.stop());
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const first = player.load(102);
  await settle();
  assert.equal(videos.length, 1);
  if (failure === 'timeout') t.mock.timers.tick(15000);
  else videos[0].dispatchEvent(new Event('error'));
  await first;
  assert.match(statuses.at(-1), /too long|Could not load/);
  assert.equal(player.current, null);

  const transport = player.element.children[1];
  const retryButton = transport.children.find(el => el.tagName === 'button' && el.children.some(child => child.textContent === 'Retry recording'));
  assert.ok(retryButton, 'a failed recording needs a direct retry control');
  assert.equal(retryButton.hidden, false);
  assert.equal(retryButton.disabled, false);
  retryButton.dispatchEvent(new Event('click'));
  await settle();
  assert.equal(videos.length, 2, 'retry must create a fresh media load instead of reusing a rejected one');
  videos[1].dispatchEvent(new Event('loadeddata'));
  await settle();
  assert.equal(player.current.start, 100);
  assert.equal(retryButton.hidden, true);
  assert.equal(videos[1].currentTime, 2);
  assert.match(statuses.at(-1), /Scrub/);
});

test('retry returns to the failed time jump instead of the previous recording', async t => {
  videos.length = 0;
  const requests = [], statuses = [];
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async url => {
    const at = Number(new URL(url).searchParams.get('at'));
    requests.push(at);
    const start = at < 200 ? 100 : 200;
    return new Response(JSON.stringify({ ...chunk, start, end: start + 10, url: `http://localhost/house/footage/${start}.mp4` }));
  };
  const player = recordedPlayer({ status: text => statuses.push(text), clock() {}, changed() {} });
  t.after(() => player.stop());
  const initial = player.load(102);
  await settle();
  videos[0].dispatchEvent(new Event('loadeddata'));
  await initial;
  const jump = player.load(202);
  await settle();
  videos[1].dispatchEvent(new Event('error'));
  await jump;
  assert.equal(player.current.start, 100);
  const retry = player.element.children[1].children.find(el => el.tagName === 'button' && el.children.some(child => child.textContent === 'Retry recording'));
  assert.equal(retry.hidden, false);
  retry.dispatchEvent(new Event('click'));
  await settle();
  videos[2].dispatchEvent(new Event('loadeddata'));
  await settle();
  assert.equal(requests.at(-1), 202);
  assert.equal(player.current.start, 200);
  assert.match(statuses.at(-1), /Scrub/, 'a manual time jump must not be described as a recording gap');
});

test('a media error during playback can reload the current recording', async t => {
  videos.length = 0;
  const statuses = [];
  const player = recordedPlayer({ status: text => statuses.push(text), clock() {}, changed() {} });
  t.after(() => player.stop());
  const initial = player.load(102);
  await settle();
  videos[0].dispatchEvent(new Event('loadeddata'));
  await initial;
  videos[0].error = { code: 2 };
  videos[0].dispatchEvent(new Event('error'));
  assert.match(statuses.at(-1), /stopped loading/);
  const retry = player.element.children[1].children.find(el => el.tagName === 'button' && el.children.some(child => child.textContent === 'Retry recording'));
  retry.dispatchEvent(new Event('click'));
  await settle();
  assert.equal(videos.length, 2, 'an errored media element must not be reused');
  videos[1].dispatchEvent(new Event('loadeddata'));
  await settle();
  assert.equal(retry.hidden, true);
  assert.match(statuses.at(-1), /Scrub/);
});
