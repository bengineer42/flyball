// Proactive event notifications, end to end, headless. Usage:
//   node events-e2e.mjs <ui-url> <api-url>
// Against a runner for examples/simulated/furnace.yaml: drives real WARNING+ events via the
// furnace device's own `fail`/`restore` simulation commands (POST /api/devices/furnace/commands/…),
// while the browser sits on the Overview page (not Events), and checks: a toast appears in the DOM
// for a fresh event while elsewhere; the nav badge shows the right unread count; dismissing a toast
// removes it and decrements the badge; visiting Events + "Mark all read" clears the badge. Prints
// PASS/FAIL per step and the console counts; exits non-zero on any FAIL.
import { chromium } from '../../ui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';

const [ui, api, ...rest] = process.argv.slice(2);
if (!ui || !api) {
  console.error('usage: node events-e2e.mjs <ui-url> <api-url>');
  process.exit(2);
}
let shots = null;
for (let i = 0; i < rest.length; i++) if (rest[i] === '--shots') shots = rest[++i];
if (shots) fs.mkdirSync(shots, { recursive: true });

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const counts = { error: 0, warning: 0, pageerror: 0 };
const log = [];
page.on('console', (m) => { const t = m.type(); if (t === 'error' || t === 'warning') { counts[t]++; log.push(`[${t}] ${m.text().slice(0, 300)} @ ${m.location().url}`); } });
page.on('pageerror', (e) => { counts.pageerror++; log.push(`[pageerror] ${String(e).slice(0, 300)}`); });

let failed = 0;
const step = async (name, fn) => {
  try { await fn(); console.log(`PASS ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}: ${String(e).split('\n')[0]}`); }
};
const shot = async (name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png` }); };
const T = (id) => page.getByTestId(id);
// Direct hash navigation (a real user action too -- the app's whole routing is `location.hash`):
// avoids the flakiness of clicking a specific nav `<a>` while the drawer/list may be re-rendering
// from a just-arrived event.
const goTo = async (hash) => {
  await page.evaluate((h) => { window.location.hash = h; }, hash);
  await page.waitForURL((u) => u.hash === hash, { timeout: 5000 });
};

// Fires a real simulated hardware fault (ERROR event: "thermocouple open circuit") on the given
// signal, via the runner's own API -- the same command `flyball furnace fail --signal zone3` sends.
const fail = async (signal) => {
  const res = await fetch(`${api}/api/devices/furnace/commands/fail`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ signal }),
  });
  if (!res.ok) throw new Error(`fail(${signal}) -> ${res.status} ${await res.text()}`);
};
const restore = async (signal) => {
  const res = await fetch(`${api}/api/devices/furnace/commands/restore`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ signal }),
  });
  if (!res.ok) throw new Error(`restore(${signal}) -> ${res.status} ${await res.text()}`);
};

await step('the app loads on the overview page, streams live, no badge with no unread events', async () => {
  await page.goto(`${ui}/#/`, { waitUntil: 'networkidle' });
  // The events websocket takes a moment to open after load; firing a fault before it does would
  // be missed until the next reconnect (no periodic re-seed, only the initial GET + live push).
  await page.getByText('live', { exact: true }).first().waitFor({ timeout: 15000 });
  if (await T('events-nav-badge').count()) throw new Error('badge present with no unread events yet');
  await shot('overview-no-badge');
});

// `useUnreadEvents`'s toast queue only arms itself once it has seen a non-empty events array (see
// the hook's own `if (events.length === 0) return;` early-out before `mountedRef.current = true`):
// against a totally fresh rig (empty event history, as this check gets one every run) the very
// first live event ever is treated as "the backlog" and never toasts -- correct behaviour (nobody
// wants the whole history dumped as toasts on first load), but it means this check needs one real
// event to arm the hook before it can assert anything about *fresh* events. `restore` on a signal
// that was never failed is a true no-op (the sim device never emits an event for it) so it can't be
// used to arm this; a real fail/restore pair is used instead, on a signal (zone2) the rest of this
// script never touches, then cleared with "Mark all read" so it doesn't pollute the badge assertions.
await step('prime the event stream (one real event, needed to arm the toast queue), then mark it read', async () => {
  await fail('zone2');
  // The "offline" event only fires once the device's own poll notices the simulated fault
  // (poll_s: 1.0, wall-clock); restoring immediately after fail (no wait) can race ahead of that
  // poll and cancel the fault before it's ever observed, silently producing no event at all.
  await page.waitForTimeout(1500);
  await restore('zone2');
  // The fail's ERROR event counts as unread the moment the store delivers it -- wait for that
  // (the badge appearing), not a fixed sleep, before navigating to the page that lists it.
  await T('events-nav-badge').waitFor({ state: 'visible', timeout: 10000 });
  await goTo('#/events');
  await T('events-mark-all-read').waitFor({ timeout: 10000 });
  await T('events-mark-all-read').click();
  await page.waitForTimeout(300);
  await goTo('#/overview');
  if (await T('events-nav-badge').count()) throw new Error('badge should be clear after priming + mark all read');
});

await step('a fresh WARNING+ event produces a toast while off the Events page', async () => {
  await fail('zone3');
  await T('event-toast').waitFor({ state: 'visible', timeout: 10000 });
  const text = await T('event-toast').locator('.MuiAlert-message').innerText();
  if (!/zone3|furnace/i.test(text)) throw new Error(`toast text unexpected: ${JSON.stringify(text)}`);
  await shot('toast-visible');
});

await step('the nav badge shows the unread count (1)', async () => {
  await T('events-nav-badge').waitFor({ state: 'visible', timeout: 5000 });
  const label = await T('events-nav-badge').innerText();
  if (label.trim() !== '1') throw new Error(`badge says ${JSON.stringify(label)}, expected 1`);
});

await step('dismissing the toast removes it AND marks it read (badge drops to 0)', async () => {
  await T('event-toast-dismiss').click();
  await T('event-toast').waitFor({ state: 'hidden', timeout: 5000 });
  await page.waitForTimeout(300);
  if (await T('events-nav-badge').count()) throw new Error('badge still present after dismissing the only unread toast');
  await shot('toast-dismissed');
});

await step('a second fault toasts again and raises the badge to 1', async () => {
  await restore('zone3'); // an INFO event: does not toast, does not count as unread
  await fail('zone1');
  await T('event-toast').waitFor({ state: 'visible', timeout: 10000 });
  await T('events-nav-badge').waitFor({ state: 'visible', timeout: 5000 });
  const label = await T('events-nav-badge').innerText();
  if (label.trim() !== '1') throw new Error(`badge says ${JSON.stringify(label)}, expected 1`);
});

await step('visiting the Events page and "Mark all read" clears the badge', async () => {
  await goTo('#/events');
  await T('events-mark-all-read').waitFor({ timeout: 10000 });
  await shot('events-page');
  await T('events-mark-all-read').click();
  await page.waitForTimeout(500);
  if (await T('events-nav-badge').count()) throw new Error('badge still present after mark all read');
  await shot('events-marked-read');
});

await step('navigating away and back stays read (badge stays clear, no stale re-toast)', async () => {
  await goTo('#/overview');
  await page.waitForTimeout(1000);
  if (await T('events-nav-badge').count()) throw new Error('badge reappeared after navigating away and back with nothing new');
  if (await T('event-toast').count()) throw new Error('a toast reappeared for already-read events');
});

await step('restore leaves the rig clean (both signals back)', async () => {
  await restore('zone1');
});

console.log(`${failed ? 'SOME FAIL' : 'ALL PASS'} · errors=${counts.error} warnings=${counts.warning} pageerrors=${counts.pageerror}`);
if (log.length) console.log(log.join('\n'));
await browser.close();
process.exit(failed ? 1 : 0);
