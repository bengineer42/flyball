// Text that does not fit its box, and boxes that spill out of their parent — measured, not eyeballed.
//   overflow.mjs <url> [--width W --height H --dark --wait ms]
// Prints one line per offender: kind, a CSS-ish path, the box, and the text; ends with a count.
// kinds: clipped  = an element's content is wider than its box and the box hides it (ellipsis or cut)
//        spill    = a child's box extends past its parent's box (parent not scrollable)
//        hscroll  = the page itself scrolls horizontally
import { chromium } from '/home/ben/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.mjs';
const args = process.argv.slice(2);
const url = args[0];
const opt = (k, d) => { const i = args.indexOf(k); return i < 0 ? d : args[i + 1]; };
const width = Number(opt('--width', 1440)), height = Number(opt('--height', 900)), wait = Number(opt('--wait', 3000));
const browser = await chromium.launch({ executablePath: '/home/ben/.cache/ms-playwright/chromium-1169/chrome-linux/chrome' });
const page = await browser.newPage({ viewport: { width, height }, colorScheme: args.includes('--dark') ? 'dark' : 'light' });
await page.goto(url); await page.waitForTimeout(wait);
const out = await page.evaluate(() => {
  const path = (el) => { const parts = []; for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) { const cls = [...e.classList].filter(c => !c.startsWith('css-') && !c.startsWith('Mui')).slice(0, 2).join('.'); parts.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : '')); } return parts.join(' > '); };
  const box = (r) => `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}×${Math.round(r.height)}`;
  const found = [];
  const all = [...document.querySelectorAll('body *')];
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    // clipped text: content wider than the box, and the box does not scroll
    const hidesX = ['hidden', 'clip'].includes(cs.overflowX) || cs.textOverflow === 'ellipsis';
    if (hidesX && el.scrollWidth > el.clientWidth + 2 && text && el.children.length <= 3 && !el.matches('canvas, svg, .u-wrap, .u-over, .u-under, .u-legend')) {
      found.push({ kind: 'clipped', path: path(el), box: box(r), text, by: el.scrollWidth - el.clientWidth });
    }
    // spill: a child past its parent's right edge while the parent is not scrollable
    const p = el.parentElement; if (!p) continue;
    const pcs = getComputedStyle(p); const pr = p.getBoundingClientRect();
    const parentScrolls = ['auto', 'scroll'].includes(pcs.overflowX) || ['auto', 'scroll'].includes(pcs.overflowY);
    if (!parentScrolls && pr.width > 0 && r.right > pr.right + 2 && cs.position !== 'absolute' && cs.position !== 'fixed' && !el.closest('.MuiPopover-root, .MuiTooltip-popper, .MuiDrawer-root, [role="presentation"]')) {
      found.push({ kind: 'spill', path: path(el), box: box(r), text, by: Math.round(r.right - pr.right) });
    }
  }
  if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 2) found.push({ kind: 'hscroll', path: 'html', box: `${document.documentElement.scrollWidth}`, text: '', by: document.documentElement.scrollWidth - document.documentElement.clientWidth });
  // de-duplicate nested spills (a spilling child inside a spilling child)
  const seen = new Set();
  return found.filter(f => { const k = f.kind + f.path + f.text; if (seen.has(k)) return false; seen.add(k); return true; });
});
for (const f of out) console.log(`${f.kind}\t${f.by}px\t${f.box}\t${f.path}\t${JSON.stringify(f.text)}`);
console.log(`${url} @${width}: ${out.length} offender(s)`);
await browser.close();
