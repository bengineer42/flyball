// For Phase 3: needs passkeys in the Go front (the /api/auth/passkey/* routes). It does not run
// against Phase 1, where nothing serves those routes and the passkey UI stays hidden.
// Passkey/WebAuthn login, end to end, headless. Usage:
//   node passkey-e2e.mjs <ui-url> <password> [--shots <dir>]
// Against a runner started with --password: password-login (to bootstrap), open "Manage passkeys",
// register a passkey with a real CDP virtual authenticator, confirm it lists; log out; sign in with
// the passkey alone; revoke it; confirm a login attempt with a revoked credential fails gracefully.
// <ui-url> MUST be served as http://localhost:<port> -- WebAuthn's secure-context/RP-ID matching
// needs the literal hostname "localhost" (confirmed by the 18 Sep spike; 127.0.0.1 is a different
// origin from what the runner's rp_id derivation would see through the vite proxy).
// Prints PASS/FAIL per step and the console counts.
import { chromium } from '/home/ben/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const [ui, password, ...rest] = process.argv.slice(2);
const opt = { shots: null };
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === '--shots') opt.shots = rest[++i];
}
if (!ui || !password) {
  console.error('usage: node passkey-e2e.mjs <ui-url> <password> [--shots <dir>]');
  process.exit(2);
}
if (!/^https?:\/\/localhost[:/]/.test(ui)) {
  console.error(`FAIL preflight: <ui-url> must be http://localhost:<port> for WebAuthn, got ${ui}`);
  process.exit(2);
}
if (opt.shots) fs.mkdirSync(opt.shots, { recursive: true });
const browser = await chromium.launch({ executablePath: '/home/ben/.cache/ms-playwright/chromium-1169/chrome-linux/chrome' });
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
const shot = async (name) => { if (opt.shots) await page.screenshot({ path: `${opt.shots}/${name}.png` }); };
const T = (id) => page.getByTestId(id);

// The CDP virtual authenticator: a real ctap2 device with resident keys and user verification,
// presence simulated so no human gesture is needed. Confirmed working by the 18 Sep spike.
const cdp = await ctx.newCDPSession(page);
await cdp.send('WebAuthn.enable');
const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
  options: {
    protocol: 'ctap2',
    transport: 'internal',
    hasResidentKey: true,
    hasUserVerification: true,
    isUserVerified: true,
    automaticPresenceSimulation: true,
  },
});

let registeredId = null;

await step('password login bootstraps a session', async () => {
  await page.goto(`${ui}/#/`, { waitUntil: 'networkidle' });
  await T('login').waitFor({ timeout: 15000 });
  await T('login-secret').fill(password);
  await T('login-submit').click();
  await T('auth-chip').waitFor({ timeout: 15000 });
  await shot('password-signed-in');
});

await step('the passkey manager opens with no passkeys yet', async () => {
  await T('auth-chip').click();
  await T('auth-manage-passkeys').waitFor({ timeout: 5000 });
  await T('auth-manage-passkeys').click();
  await T('passkey-manager').waitFor({ timeout: 5000 });
  await T('passkey-list').waitFor({ timeout: 5000 });
  await shot('passkey-manager-empty');
});

await step('registering a passkey drives a real ceremony and lists it', async () => {
  await T('passkey-label').fill("Ben's laptop");
  await T('passkey-register').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="passkey-list"]')?.textContent?.includes("Ben's laptop"), null, { timeout: 15000 });
  const creds = await cdp.send('WebAuthn.getCredentials', { authenticatorId });
  if (creds.credentials.length !== 1) throw new Error(`authenticator holds ${creds.credentials.length} credentials, expected 1`);
  // The item's own data-testid ("passkey-<id>") sits on the ListItem's inner root div, not the <li>.
  registeredId = await page.evaluate(() => {
    const el = Array.from(document.querySelectorAll('[data-testid="passkey-list"] [data-testid^="passkey-"]'))
      .find((e) => /^passkey-\d+$/.test(e.getAttribute('data-testid') || '') && e.textContent?.includes("Ben's laptop"));
    return el?.getAttribute('data-testid')?.replace('passkey-', '') ?? null;
  });
  if (!registeredId) throw new Error('could not read the registered passkey id from the DOM');
  await shot('passkey-registered');
});

await step('close the manager', async () => {
  await T('passkey-manager-close').click();
  await T('passkey-manager').waitFor({ state: 'hidden', timeout: 5000 });
});

await step('sign out gets the login page back', async () => {
  await T('auth-chip').click();
  await T('auth-signout').click();
  await T('login').waitFor({ timeout: 10000 });
  await shot('signed-out');
});

await step('signing in with the passkey alone reaches the signed-in app', async () => {
  await T('login-passkey').waitFor({ timeout: 5000 });
  await T('login-passkey').click();
  await T('auth-chip').waitFor({ timeout: 15000 });
  const cookies = await ctx.cookies();
  const session = cookies.find((c) => c.name === 'flyball_session');
  if (!session || !session.httpOnly) throw new Error('no HttpOnly session cookie after passkey login');
  await shot('passkey-signed-in');
});

await step('revoking the passkey you are signed in with ends that session: the login page is back', async () => {
  // The session cookie names its credential and the runner checks it on every request, so
  // the revoke's own follow-up (the list refetch) is the first 401 -- the app shows the door.
  await T('auth-chip').click();
  await T('auth-manage-passkeys').click();
  await T('passkey-manager').waitFor({ timeout: 5000 });
  await T(`passkey-revoke-${registeredId}`).click();
  await T('login').waitFor({ timeout: 10000 });
  await shot('passkey-revoked-signed-out');
});

await step('the revoked credential is gone from the server, not just the session', async () => {
  await T('login-secret').fill(password);
  await T('login-submit').click();
  await T('auth-chip').waitFor({ timeout: 10000 });
  await T('auth-chip').click();
  await T('auth-manage-passkeys').click();
  await T('passkey-manager').waitFor({ timeout: 5000 });
  await page.waitForFunction(() => document.querySelector('[data-testid="passkey-list"]')?.textContent?.includes('No passkeys registered'), null, { timeout: 10000 });
  await shot('passkey-revoked-list-empty');
  await T('passkey-manager-close').click();
  await T('auth-chip').click();
  await T('auth-signout').click();
  await T('login').waitFor({ timeout: 10000 });
});

await step('a login attempt with no registered passkey fails gracefully', async () => {
  // With no passkeys left server-side, /passkey/login/challenge answers with an empty
  // allowCredentials (confirmed via a network trace) -- the browser refuses the ceremony
  // itself (NotAllowedError) before any /passkey/login call is made; the server is never
  // asked to verify a stale credential. Login.tsx treats a cancelled/NotAllowedError
  // ceremony as nothing-to-say (no error text), so the real assertion is on app state:
  // still on the login page, the button usable again (not stuck busy), no session minted.
  await T('login-passkey').waitFor({ timeout: 5000 });
  let loginCall = false;
  page.on('request', (r) => { if (r.url().includes('/api/auth/passkey/login') && !r.url().includes('challenge')) loginCall = true; });
  await T('login-passkey').click();
  await page.waitForTimeout(3000);
  await T('login').waitFor({ timeout: 2000 }); // still on the login page, not signed in
  const disabled = await T('login-passkey').isDisabled();
  if (disabled) throw new Error('the passkey button is stuck disabled after the refused ceremony');
  const cookies = await ctx.cookies();
  if (cookies.some((c) => c.name === 'flyball_session')) throw new Error('a session cookie was minted despite the refusal');
  if (loginCall) throw new Error('the server was asked to verify a credential it does not have -- expected the browser to refuse first');
  await shot('passkey-login-refused-no-credential');
});

console.log(`${failed ? 'SOME FAIL' : 'ALL PASS'} · errors=${counts.error} warnings=${counts.warning} pageerrors=${counts.pageerror}`);
if (log.length) console.log(log.join('\n'));
await browser.close();
process.exit(failed ? 1 : 0);
