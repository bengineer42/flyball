// The door, end to end, headless. Usage:
//   node auth-e2e.mjs <ui-url> <secret> [--shots <dir>] [--anonymous-read] [--token-link]
// Against a runner started with --password (or --token): the login page shows, a wrong secret is refused, the
// right one gets the app with its sockets open, sign out gets the login page back. --anonymous-read: the app
// shows without a login, a write is refused with the nudge, the chip leads to the login page. --token-link:
// opening ?token=<secret> signs in and strips the query. Prints PASS/FAIL per step and the console counts.
import { chromium } from '../../ui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const [ui, secret, ...rest] = process.argv.slice(2);
const opt = { shots: null, anonymousRead: false, tokenLink: false };
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === '--shots') opt.shots = rest[++i];
  else if (rest[i] === '--anonymous-read') opt.anonymousRead = true;
  else if (rest[i] === '--token-link') opt.tokenLink = true;
}
if (opt.shots) fs.mkdirSync(opt.shots, { recursive: true });
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const counts = { error: 0, warning: 0, pageerror: 0 };
const log = [];
page.on('console', (m) => { const t = m.type(); if (t === 'error' || t === 'warning') { counts[t]++; log.push(`[${t}] ${m.text().slice(0, 300)} @ ${m.location().url}`); } });
page.on('pageerror', (e) => { counts.pageerror++; log.push(`[pageerror] ${String(e).slice(0, 300)}`); });
let sockets = 0;
page.on('websocket', () => sockets++);
let failed = 0;
const step = async (name, fn) => {
  try { await fn(); console.log(`PASS ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}: ${String(e).split('\n')[0]}`); }
};
const shot = async (name) => { if (opt.shots) await page.screenshot({ path: `${opt.shots}/${name}.png` }); };
const T = (id) => page.getByTestId(id);

if (opt.tokenLink) {
  await step('?token= on the page URL signs in and is dropped', async () => {
    await page.goto(`${ui}/?token=${encodeURIComponent(secret)}#/`, { waitUntil: 'networkidle' });
    await T('auth-chip').waitFor({ timeout: 15000 });
    if (new URL(page.url()).searchParams.has('token')) throw new Error('token still in the address');
    await shot('token-link');
  });
  await ctx.clearCookies();
}

if (opt.anonymousRead) {
  await step('anonymous reader sees the app and the read-only chip', async () => {
    await page.goto(`${ui}/#/`, { waitUntil: 'networkidle' });
    await T('auth-chip').waitFor({ timeout: 15000 });
    const label = await T('auth-chip').innerText();
    if (!/read only/i.test(label)) throw new Error(`chip says ${JSON.stringify(label)}`);
    await shot('anonymous-read');
  });
  await step('a refused write shows the nudge', async () => {
    await page.goto(`${ui}/#/sessions`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Start recording' }).click({ timeout: 10000 });
    await T('auth-nudge').waitFor({ timeout: 5000 });
    await shot('anonymous-nudge');
    await T('nudge-signin').click();
    await T('login').waitFor({ timeout: 5000 });
    await T('login-cancel').click();
  });
  await step('the chip leads to the login page', async () => {
    await T('auth-chip').click();
    await T('login').waitFor({ timeout: 5000 });
    await T('login-cancel').waitFor({ timeout: 5000 });
    await shot('anonymous-login');
    await T('login-cancel').click();
    await T('auth-chip').waitFor({ timeout: 5000 });
  });
}

await step('the login page replaces the app', async () => {
  if (!opt.anonymousRead) {
    await page.goto(`${ui}/#/`, { waitUntil: 'networkidle' });
    await T('login').waitFor({ timeout: 15000 });
    await shot('login');
  } else {
    await T('auth-chip').click();
    await T('login').waitFor({ timeout: 5000 });
  }
});
await step('a wrong secret is refused on the page', async () => {
  await T('login-secret').fill(secret + 'x');
  await T('login-submit').click();
  await T('login-error').waitFor({ timeout: 5000 });
  await shot('login-wrong');
});
await step('the right secret gets the app with its sockets open', async () => {
  const before = sockets;
  await T('login-secret').fill(secret);
  await T('login-submit').click();
  await T('auth-chip').waitFor({ timeout: 15000 });
  await page.waitForTimeout(3000);
  if (sockets <= before) throw new Error('no socket opened after the login');
  const cookies = await ctx.cookies();
  const session = cookies.find((c) => c.name === 'flyball_session');
  if (!session || !session.httpOnly) throw new Error('no HttpOnly session cookie');
  if (await page.evaluate(() => Object.keys(localStorage).some((k) => /token/i.test(k)))) throw new Error('a token in localStorage');
  await shot('signed-in');
});
await step('sign out gets the login page back', async () => {
  await T('auth-chip').click();
  await T('auth-signout').click();
  if (opt.anonymousRead) await T('auth-chip').waitFor({ timeout: 10000 });
  else await T('login').waitFor({ timeout: 10000 });
  await shot('signed-out');
});

console.log(`${failed ? 'SOME FAIL' : 'ALL PASS'} · sockets=${sockets} errors=${counts.error} warnings=${counts.warning} pageerrors=${counts.pageerror}`);
if (log.length) console.log(log.join('\n'));
await browser.close();
process.exit(failed ? 1 : 0);
