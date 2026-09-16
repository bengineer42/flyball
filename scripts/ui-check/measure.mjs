import { chromium } from '/home/ben/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.mjs';
const [url, sel] = process.argv.slice(2);
const b = await chromium.launch({ executablePath: '/home/ben/.cache/ms-playwright/chromium-1169/chrome-linux/chrome' });
const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await p.goto(url, { waitUntil: 'networkidle' }); await p.waitForTimeout(5000);
const r = await p.evaluate((sel) => [...document.querySelectorAll(sel)].slice(0, 12).map(e => { const c = e.querySelector('canvas'); const r = e.getBoundingClientRect(); return { box: [Math.round(r.width), Math.round(r.height)], canvas: c ? [c.width, c.height] : null, cls: e.className }; }), sel);
console.log(JSON.stringify(r, null, 0)); await b.close();
