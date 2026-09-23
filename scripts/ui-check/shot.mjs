// Headless screenshot + console capture. Usage:
//   node shot.mjs <url> <out.png> [--width 1440] [--height 900] [--wait 4000] [--dark] [--full] [--click "text=Edit"]
// Writes <out>.log beside the png with every console error/warning and pageerror, and prints a one-line summary.
import { chromium } from '../../ui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const [url, out, ...rest] = process.argv.slice(2);
const opt = { width: 1440, height: 900, wait: 4000, dark: false, full: false, click: [] };
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a === '--width') opt.width = +rest[++i];
  else if (a === '--height') opt.height = +rest[++i];
  else if (a === '--wait') opt.wait = +rest[++i];
  else if (a === '--dark') opt.dark = true;
  else if (a === '--full') opt.full = true;
  else if (a === '--click') opt.click.push(rest[++i]);
}
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const ctx = await browser.newContext({ viewport: { width: opt.width, height: opt.height }, colorScheme: opt.dark ? 'dark' : 'light' });
const page = await ctx.newPage();
const log = [];
const counts = { error: 0, warning: 0, pageerror: 0 };
page.on('console', m => { const t = m.type(); if (t === 'error' || t === 'warning') { counts[t]++; log.push(`[${t}] ${m.text().slice(0, 400)}`); } });
page.on('pageerror', e => { counts.pageerror++; log.push(`[pageerror] ${String(e).slice(0, 400)}`); });
await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch(e => log.push(`[goto] ${e}`));
for (const c of opt.click) { await page.click(c, { timeout: 5000 }).catch(e => log.push(`[click ${c}] ${e}`)); await page.waitForTimeout(500); }
await page.waitForTimeout(opt.wait);
await page.screenshot({ path: out, fullPage: opt.full });
fs.writeFileSync(out.replace(/\.png$/, '') + '.log', log.join('\n') + '\n');
const dedup = new Map(); for (const l of log) dedup.set(l.slice(0, 80), (dedup.get(l.slice(0, 80)) ?? 0) + 1);
console.log(`${out}: errors=${counts.error} warnings=${counts.warning} pageerrors=${counts.pageerror}` + (dedup.size ? ' | ' + [...dedup].slice(0, 3).map(([k, n]) => `${n}× ${k}`).join(' | ') : ''));
await browser.close();
