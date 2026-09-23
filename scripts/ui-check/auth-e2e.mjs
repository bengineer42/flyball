// The door, end to end, headless, against real processes. Usage:
//   node scripts/ui-check/auth-e2e.mjs --flyball <flyball built with the UI> [--shots <dir>] [--only password|none|starting|local|bare]
// (or FLYBALL_BIN=<binary> instead of --flyball). The binary must embed the dashboard (see README.md:
// a plain `go build` embeds only the placeholder page). flyball-runner comes from engine/.venv.
//
// It starts each rig itself -- `flyball run` on port 0 with its own XDG dirs in a temp dir, and a bare
// flyball-runner on a port in 18470-18499 -- and stops them and removes the dir at the end, pass or fail:
//   password  runner.front auth: password, anonymous: read. Anonymous sees the app read-only with no stop
//             button; a wrong password is refused on the page; the right one signs in (HttpOnly cookie,
//             nothing in localStorage, sockets open); the "Software stop" button shows and reports the interim
//             stop (controllers to manual, nothing written);
//             sign out closes the sockets and hides the button; a session revoked from outside (the
//             cookie's logout, not the page's) turns the page read-only with no reload loop.
//   none      auth: password, anonymous: none. The login page replaces the app; after a revoked session the
//             login page comes back, with no reload loop.
//   local     the local shape: no login, no chip, the stop button shows.
//   starting  a page opened before `flyball run`'s runner answers (the front's 503) comes up once it does.
//   bare      a bare runner with a token: its one-time link signs in and drops the nonce; a second use fails.
// Prints PASS/FAIL per step and the console counts; exits 1 on any failure or any console error/warning.
import { chromium } from '../../ui/node_modules/playwright-core/index.mjs';
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const venv = path.join(root, 'engine/.venv/bin');
const args = process.argv.slice(2);
const opt = { flyball: process.env.FLYBALL_BIN || '', shots: null, only: null };
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--flyball') opt.flyball = args[++i];
  else if (args[i] === '--shots') opt.shots = args[++i];
  else if (args[i] === '--only') opt.only = args[++i];
  else { console.error(`unknown argument ${args[i]}`); process.exit(2); }
}
if (!opt.flyball || !fs.existsSync(opt.flyball)) {
  console.error('usage: node scripts/ui-check/auth-e2e.mjs --flyball <flyball built with the UI> [--shots dir] [--only phase]');
  process.exit(2);
}
if (!fs.existsSync(path.join(venv, 'flyball-runner'))) {
  console.error('no engine/.venv/bin/flyball-runner: cd engine && UV_FROZEN=1 uv sync --all-extras');
  process.exit(2);
}
opt.flyball = path.resolve(opt.flyball);
if (opt.shots) fs.mkdirSync(opt.shots, { recursive: true });

// Launched before anything is started, so a missing browser leaves nothing behind.
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const PASSWORD = 'e2e-ui-password-1';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'fe2e-ui-'));
for (const d of ['rt', 'state', 'config', 'home']) fs.mkdirSync(path.join(tmp, d), { mode: 0o700 });
const env = {
  ...Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(FLYBALL|XDG_)/.test(k))),
  PATH: `${venv}:${process.env.PATH}`,
  XDG_RUNTIME_DIR: path.join(tmp, 'rt'),
  XDG_STATE_HOME: path.join(tmp, 'state'),
  XDG_CONFIG_HOME: path.join(tmp, 'config'),
  HOME: path.join(tmp, 'home'),
  UV_FROZEN: '1',
};
const procs = [];
const oven = fs.readFileSync(path.join(root, 'examples/simulated/oven.yaml'), 'utf8');
const scrypt = execFileSync(opt.flyball, ['password', PASSWORD], { env }).toString().trim();

let failed = 0;
const counts = { error: 0, warning: 0, pageerror: 0 };
// The browser logs every 4xx fetch as a console error; the wrong-password step makes exactly this one on purpose.
const EXPECTED = [/Failed to load resource: the server responded with a status of 401 .*\/api\/auth\/login$/];
// While a runner starts, the front answers 503 (Retry-After): expected only in the `starting` phase.
const STARTING = [/status of 503 \(Service Unavailable\)/, /Unexpected response code: 503/];
let phase = '';
const log = [];
const step = async (name, fn) => {
  try { await fn(); console.log(`PASS ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}: ${String(e).split('\n')[0]}`); }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Start `cmd args` in its own process group, output to <tmp>/<name>.log; resolves the first match of re in it. */
async function start(name, cmd, cmdArgs, re, cwd = tmp) {
  const out = fs.openSync(path.join(tmp, `${name}.log`), 'a');
  const p = spawn(cmd, cmdArgs, { cwd, env, detached: true, stdio: ['ignore', out, out] });
  procs.push(p);
  const end = Date.now() + 60000;
  while (Date.now() < end) {
    const text = fs.readFileSync(path.join(tmp, `${name}.log`), 'utf8');
    const m = text.match(re);
    if (m) return m;
    if (p.exitCode !== null) throw new Error(`${name} exited ${p.exitCode}:\n${text}`);
    await sleep(100);
  }
  throw new Error(`${name}: no ${re} within 60 s`);
}

/** A rig directory with the oven and `extra` appended; returns the rig file. */
function rig(name, extra) {
  fs.mkdirSync(path.join(tmp, name));
  const file = path.join(tmp, name, 'oven.yaml');
  fs.writeFileSync(file, `${oven}\n${extra}\n`);
  return file;
}

/** Wait for url to answer `status` (default: any 2xx): a runner behind the front answers, not the front's 503. */
async function waitOk(url, status = 0) {
  const end = Date.now() + 90000;
  let last = '';
  while (Date.now() < end) {
    try {
      const r = await fetch(url);
      if (status ? r.status === status : r.ok) return;
      last = String(r.status);
    } catch (e) { last = String(e); }
    await sleep(250);
  }
  throw new Error(`${url}: not ready (${last})`);
}

/**
 * Wait for the runner behind a front with `anonymous: none`: the front answers a caller with no verb
 * itself (D-047), so an anonymous 401 no longer means the runner is up. Sign in over the API, wait for
 * a 2xx with the session, then sign out again so the page starts anonymous.
 */
async function waitSignedIn(base) {
  const hdr = { 'Content-Type': 'application/json', Origin: base };
  const r = await fetch(`${base}/api/auth/login`, { method: 'POST', headers: hdr, body: JSON.stringify({ password: PASSWORD }) });
  if (!r.ok) throw new Error(`${base}: sign-in for the readiness wait: ${r.status}`);
  const cookie = (r.headers.get('set-cookie') || '').split(';')[0];
  const end = Date.now() + 90000;
  let last = '';
  while (Date.now() < end) {
    const s = await fetch(`${base}/api/runner`, { headers: { Cookie: cookie } });
    if (s.ok) break;
    last = String(s.status);
    await sleep(250);
  }
  if (Date.now() >= end) throw new Error(`${base}/api/runner: not ready (${last})`);
  await fetch(`${base}/api/auth/logout`, { method: 'POST', headers: { ...hdr, Cookie: cookie } });
}

/** `flyball run` with runner.front `front` (YAML lines); returns its base URL, the runner ready. */
async function flyballRun(name, front) {
  const file = rig(name, front ? `runner:\n  front:\n${front.split('\n').map((l) => `    ${l}`).join('\n')}\n` : '');
  const m = await start(`${name}-front`, opt.flyball, ['run', file, '--listen', '127.0.0.1:0'], /flyball: serving rig \S+ on (http:\/\/\S+?)\/? \(/);
  return m[1];
}

async function freePort(from, to) {
  for (let port = from; port <= to; port++) {
    const ok = await new Promise((res) => {
      const s = net.createServer().once('error', () => res(false)).once('listening', () => s.close(() => res(true)));
      s.listen(port, '127.0.0.1');
    });
    if (ok) return port;
  }
  throw new Error(`no free port in ${from}-${to}`);
}


/** A fresh page with the console, sockets and main-frame navigations counted. */
async function newPage() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const s = { ctx, page, opened: 0, closed: 0, navigations: 0, authCalls: 0 };
  page.on('console', (m) => {
    const t = m.type();
    if (t !== 'error' && t !== 'warning') return;
    const line = `${m.text().slice(0, 300)} @ ${m.location().url}`;
    if (EXPECTED.some((re) => re.test(line)) || (phase === 'starting' && STARTING.some((re) => re.test(line)))) {
      log.push(`[expected ${t}] ${line}`);
      return;
    }
    counts[t]++;
    log.push(`[${t}] ${line}`);
  });
  page.on('pageerror', (e) => { counts.pageerror++; log.push(`[pageerror] ${String(e).slice(0, 300)}`); });
  page.on('websocket', (w) => { s.opened++; w.on('close', () => s.closed++); });
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) s.navigations++; });
  page.on('request', (r) => { if (new URL(r.url()).pathname === '/api/auth') s.authCalls++; });
  return s;
}
const T = (page, id) => page.getByTestId(id);
const shot = async (page, name) => { if (opt.shots) await page.screenshot({ path: path.join(opt.shots, `${name}.png`) }); };
const want = (phase) => !opt.only || opt.only === phase;

/** The cookie the page holds for base, signed out from outside the page: the session ends as a revocation would. */
async function revokeFromOutside(ctx, base) {
  const ck = (await ctx.cookies(base)).find((c) => c.name.startsWith('flyball-'));
  if (!ck) throw new Error('no session cookie to revoke');
  const r = await fetch(`${base}/api/auth/logout`, { method: 'POST', headers: { Cookie: `${ck.name}=${ck.value}`, Origin: base } });
  if (!r.ok) throw new Error(`logout from outside: ${r.status}`);
}

/** After a revocation: no reload, and /api/auth asked a bounded number of times over `ms`. */
async function noReloadLoop(s, ms) {
  const nav = s.navigations, auth = s.authCalls;
  await sleep(ms);
  if (s.navigations !== nav) throw new Error(`${s.navigations - nav} reload(s) after the revocation`);
  if (s.authCalls - auth > 6) throw new Error(`/api/auth asked ${s.authCalls - auth} times in ${ms} ms: a loop`);
}

async function stopShowsInterim(page) {
  await T(page, 'stop-button').click();
  const label = (await T(page, 'stop-button').innerText()).trim();
  if (label !== 'Software stop') throw new Error(`the button says ${JSON.stringify(label)}`);
  await page.getByRole('dialog').getByRole('button', { name: 'Software stop' }).click();
  await T(page, 'stop-result').waitFor({ timeout: 10000 });
  const text = await T(page, 'stop-result').innerText();
  // The interim report: controllers to manual, nothing written.
  if (!/^Software stop: .*nothing written/.test(text)) throw new Error(`the stop result says ${JSON.stringify(text)}`);
}

try {
  if (want('password')) {
    const base = await flyballRun('password', `auth: password\npassword: '${scrypt}'\nanonymous: read`);
    await waitOk(`${base}/api/runner`);
    const s = await newPage();
    const { page, ctx } = s;
    await step('password: anonymous sees the app read-only, with no stop button', async () => {
      await page.goto(`${base}/#/`, { waitUntil: 'networkidle' });
      await T(page, 'auth-chip').waitFor({ timeout: 15000 });
      const label = await T(page, 'auth-chip').innerText();
      if (!/read only/i.test(label)) throw new Error(`chip says ${JSON.stringify(label)}`);
      if (await T(page, 'stop-button').count()) throw new Error('a stop button without operate');
      await shot(page, 'password-anonymous');
    });
    await step('password: a wrong password is refused on the page', async () => {
      await T(page, 'auth-chip').click();
      await T(page, 'login').waitFor({ timeout: 5000 });
      await T(page, 'login-secret').fill(PASSWORD + 'x');
      await T(page, 'login-submit').click();
      await T(page, 'login-error').waitFor({ timeout: 5000 });
      await shot(page, 'password-wrong');
    });
    await step('password: sign in -- HttpOnly cookie, nothing in localStorage, sockets open', async () => {
      const before = s.opened;
      await T(page, 'login-secret').fill(PASSWORD);
      await T(page, 'login-submit').click();
      await page.getByTestId('auth-chip').filter({ hasText: /signed in/i }).waitFor({ timeout: 15000 });
      await sleep(2000);
      if (s.opened <= before) throw new Error('no socket opened after signing in');
      const ck = (await ctx.cookies(base)).find((c) => c.name.startsWith('flyball-'));
      if (!ck || !ck.httpOnly || ck.sameSite !== 'Lax') throw new Error(`session cookie ${JSON.stringify(ck)}`);
      if (await page.evaluate(() => Object.keys(localStorage).some((k) => /token|password|secret/i.test(k)))) throw new Error('a secret in localStorage');
      await shot(page, 'password-signed-in');
    });
    await step('password: the stop button shows with operate, and reports the interim stop', async () => {
      await T(page, 'stop-button').waitFor({ timeout: 5000 });
      await stopShowsInterim(page);
      await shot(page, 'password-stopped');
    });
    await step('password: sign out closes the sockets and hides the stop button', async () => {
      const closed = s.closed;
      await T(page, 'auth-chip').click();
      await T(page, 'auth-signout').click();
      await page.getByTestId('auth-chip').filter({ hasText: /read only/i }).waitFor({ timeout: 10000 });
      await sleep(1000);
      if (s.closed <= closed) throw new Error('no socket closed on sign out');
      if (await T(page, 'stop-button').count()) throw new Error('the stop button stayed');
      await shot(page, 'password-signed-out');
    });
    await step('password: a session revoked from outside turns the page read-only, no reload loop', async () => {
      await T(page, 'auth-chip').click();
      await T(page, 'login-secret').fill(PASSWORD);
      await T(page, 'login-submit').click();
      await T(page, 'stop-button').waitFor({ timeout: 15000 });
      await sleep(1500);
      await revokeFromOutside(ctx, base);
      await page.getByTestId('auth-chip').filter({ hasText: /read only/i }).waitFor({ timeout: 10000 });
      if (await T(page, 'stop-button').count()) throw new Error('the stop button stayed after the revocation');
      await noReloadLoop(s, 8000);
      await shot(page, 'password-revoked');
    });
    await ctx.close();
  }

  if (want('none')) {
    const base = await flyballRun('none', `auth: password\npassword: '${scrypt}'`);
    await waitSignedIn(base); // anonymous: none -- the front answers an anonymous 401 itself, even before the runner
    const s = await newPage();
    const { page, ctx } = s;
    await step('none: the login page replaces the app', async () => {
      await page.goto(`${base}/#/`, { waitUntil: 'networkidle' });
      await T(page, 'login').waitFor({ timeout: 15000 });
      if (await T(page, 'login-cancel').count()) throw new Error('a way past the login page with anonymous: none');
      await shot(page, 'none-login');
    });
    await step('none: sign in gets the app and the stop button', async () => {
      await T(page, 'login-secret').fill(PASSWORD);
      await T(page, 'login-submit').click();
      await T(page, 'stop-button').waitFor({ timeout: 15000 });
      await shot(page, 'none-signed-in');
    });
    await step('none: a session revoked from outside brings the login page back, no reload loop', async () => {
      await sleep(1500);
      await revokeFromOutside(ctx, base);
      await T(page, 'login').waitFor({ timeout: 10000 });
      await noReloadLoop(s, 8000);
      await shot(page, 'none-revoked');
    });
    await ctx.close();
  }

  if (want('starting')) {
    phase = 'starting';
    // The page opened the moment `flyball run` says where it serves, before its runner answers: the
    // front says 503 "starting" with Retry-After, and the app must come up once the runner does.
    const base = await flyballRun('starting', '');
    const s = await newPage();
    const { page, ctx } = s;
    await step('starting: a page opened while the runner starts comes up without a reload', async () => {
      await page.goto(`${base}/#/`);
      await waitOk(`${base}/api/runner`);
      const nav = s.navigations;
      try {
        await T(page, 'stop-button').waitFor({ timeout: 20000 });
      } finally {
        await shot(page, 'starting');
      }
      if (s.navigations !== nav) throw new Error('the page reloaded');
    });
    await ctx.close();
    phase = '';
  }

  if (want('local')) {
    const base = await flyballRun('local', '');
    await waitOk(`${base}/api/runner`);
    const s = await newPage();
    const { page, ctx } = s;
    await step('local: no login and no chip; the stop button shows', async () => {
      await page.goto(`${base}/#/`, { waitUntil: 'networkidle' });
      await T(page, 'stop-button').waitFor({ timeout: 15000 });
      if (await T(page, 'login').count()) throw new Error('a login page in the local shape');
      if (await T(page, 'auth-chip').count()) throw new Error('an auth chip in the local shape');
      await shot(page, 'local');
    });
    await ctx.close();
  }

  if (want('bare')) {
    const port = await freePort(18470, 18499);
    const token = `e2e-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
    const file = rig('bare', '');
    const m = await start('bare-runner', path.join(venv, 'flyball-runner'), [file, '--port', String(port), '--token', token],
      /(http:\/\/127\.0\.0\.1:\d+\/api\/auth\/link\?n=\S+)/);
    const link = m[1];
    const s = await newPage();
    const { page, ctx } = s;
    await step('bare: the one-time link signs in and drops the nonce', async () => {
      await page.goto(link, { waitUntil: 'networkidle' });
      if (new URL(page.url()).searchParams.has('n')) throw new Error(`the nonce is still in ${page.url()}`);
      await T(page, 'stop-button').waitFor({ timeout: 15000 });
      if (await T(page, 'login').count()) throw new Error('the login page after the link');
      await shot(page, 'bare-link');
    });
    await step('bare: the link works once', async () => {
      const r = await fetch(link, { redirect: 'manual' });
      if (r.status !== 401) throw new Error(`a second use: ${r.status}, want 401`);
    });
    await ctx.close();
  }
} catch (e) {
  failed++;
  console.log(`FAIL setup: ${String(e).split('\n').slice(0, 6).join(' | ')}`);
} finally {
  await browser.close();
  for (const p of procs) { try { process.kill(-p.pid, 'SIGTERM'); } catch {} }
  await sleep(1500);
  for (const p of procs) { try { process.kill(-p.pid, 'SIGKILL'); } catch {} }
  if (failed) for (const f of fs.readdirSync(tmp).filter((f) => f.endsWith('.log'))) {
    console.log(`---- ${f} (tail) ----\n${fs.readFileSync(path.join(tmp, f), 'utf8').split('\n').slice(-25).join('\n')}`);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

const clean = counts.error + counts.warning + counts.pageerror === 0;
console.log(`${failed || !clean ? 'SOME FAIL' : 'ALL PASS'} · errors=${counts.error} warnings=${counts.warning} pageerrors=${counts.pageerror}`);
if (log.length) console.log(log.join('\n'));
process.exit(failed || !clean ? 1 : 0);
