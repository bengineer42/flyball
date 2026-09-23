// Steady-state cost of one page over N seconds. Usage:
//   node perf.mjs <ui-url> <hash-route> [seconds] [--width 1440] [--height 900] [--settle 4000] [--scroll <px>] [--json]
// Prints CDP Performance.getMetrics TaskDuration delta, JS heap at start/end, long tasks
// (PerformanceObserver 'longtask'), app-root renders (`window.__fb.renders`), chart redraws
// (`window.__fb.chartsById`), and console error/warning counts over the window.
import { chromium } from '../../ui/node_modules/playwright-core/index.mjs';

const args = process.argv.slice(2);
const [uiUrl, route] = args;
const opt = { seconds: 10, width: 1440, height: 900, settle: 4000, scroll: 0, json: false, gc: false };
for (let i = 2; i < args.length; i++) {
  const a = args[i];
  if (a === '--width') opt.width = +args[++i];
  else if (a === '--height') opt.height = +args[++i];
  else if (a === '--settle') opt.settle = +args[++i];
  else if (a === '--scroll') opt.scroll = +args[++i];
  else if (a === '--json') opt.json = true;
  else if (a === '--gc') opt.gc = true; // collect garbage before each heap reading, so the delta is retained memory, not GC timing
  else if (/^\d+$/.test(a)) opt.seconds = +a;
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const ctx = await browser.newContext({ viewport: { width: opt.width, height: opt.height } });
const page = await ctx.newPage();
const counts = { error: 0, warning: 0, pageerror: 0 };
const log = [];
page.on('console', (m) => {
  const t = m.type();
  if (t === 'error' || t === 'warning') {
    counts[t]++;
    log.push(`[${t}] ${m.text().slice(0, 200)}`);
  }
});
page.on('pageerror', (e) => {
  counts.pageerror++;
  log.push(`[pageerror] ${String(e).slice(0, 200)}`);
});
const cdp = await ctx.newCDPSession(page);
await cdp.send('Performance.enable');
await cdp.send('HeapProfiler.enable');

// Long-task observer installed before any page script runs.
await page.addInitScript(() => {
  window.__longTasks = [];
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) window.__longTasks.push({ start: e.startTime, duration: e.duration });
    }).observe({ type: 'longtask', buffered: true });
  } catch {}
});

await page.goto(`${uiUrl}/${route}`, { waitUntil: 'networkidle', timeout: 60000 }).catch((e) => log.push(`[goto] ${e}`));
await page.waitForTimeout(opt.settle);
if (opt.scroll) {
  await page.evaluate((y) => window.scrollTo(0, y), opt.scroll);
  await page.waitForTimeout(1000);
}

const snapshot = async () => {
  if (opt.gc) await cdp.send('HeapProfiler.collectGarbage');
  const m = await cdp.send('Performance.getMetrics');
  const get = (n) => m.metrics.find((x) => x.name === n)?.value ?? 0;
  const inPage = await page.evaluate(() => ({
    now: performance.now(),
    longTasks: window.__longTasks?.length ?? 0,
    longTaskMs: (window.__longTasks ?? []).reduce((s, t) => s + t.duration, 0),
    renders: window.__fb?.renders ?? 0,
    rendersById: { ...(window.__fb?.rendersById ?? {}) },
    charts: window.__fb?.charts ?? 0,
    chartsById: { ...(window.__fb?.chartsById ?? {}) },
    messages: { ...(window.__fb?.messages ?? {}) },
    sockets: window.__fb?.sockets ?? null,
  }));
  return { task: get('TaskDuration'), heap: get('JSHeapUsedSize'), script: get('ScriptDuration'), layout: get('LayoutDuration'), nodes: get('Nodes'), ...inPage };
};

const errBefore = counts.error, warnBefore = counts.warning;
const a = await snapshot();
await page.waitForTimeout(opt.seconds * 1000);
const b = await snapshot();

const delta = (k) => b[k] - a[k];
const byId = (k) => Object.fromEntries(Object.keys({ ...a[k], ...b[k] }).map((id) => [id, (b[k][id] ?? 0) - (a[k][id] ?? 0)]));
const chartsById = byId('chartsById');
const rendersById = byId('rendersById');
const messages = byId('messages');
const elapsed = (b.now - a.now) / 1000;
const result = {
  url: `${uiUrl}/${route}`,
  seconds: +elapsed.toFixed(1),
  taskS: +delta('task').toFixed(3),
  scriptS: +delta('script').toFixed(3),
  layoutS: +delta('layout').toFixed(3),
  cpuPct: +((delta('task') / elapsed) * 100).toFixed(1),
  heapStartMB: +(a.heap / 1048576).toFixed(1),
  heapEndMB: +(b.heap / 1048576).toFixed(1),
  heapPct: +(((b.heap - a.heap) / a.heap) * 100).toFixed(1),
  longTasks: delta('longTasks'),
  longTaskMs: +delta('longTaskMs').toFixed(0),
  appRenders: delta('renders'),
  rendersById,
  chartRedraws: delta('charts'),
  chartsById,
  messages,
  nodes: b.nodes,
  errors: counts.error - errBefore,
  warnings: counts.warning - warnBefore,
  errorsTotal: counts.error,
  warningsTotal: counts.warning,
  pageerrors: counts.pageerror,
};
if (opt.json) console.log(JSON.stringify(result));
else {
  console.log(`${result.url} over ${result.seconds}s`);
  console.log(`  TaskDuration ${result.taskS}s (${result.cpuPct}% of a core; script ${result.scriptS}s, layout ${result.layoutS}s)`);
  console.log(`  heap ${result.heapStartMB} → ${result.heapEndMB} MB (${result.heapPct >= 0 ? '+' : ''}${result.heapPct}%)`);
  console.log(`  long tasks ${result.longTasks} (${result.longTaskMs} ms)`);
  console.log(`  App renders ${result.appRenders}; by id ${JSON.stringify(rendersById)}`);
  console.log(`  chart redraws ${result.chartRedraws}; by id ${JSON.stringify(chartsById)}`);
  if (Object.keys(messages).length) console.log(`  store messages ${JSON.stringify(messages)}`);
  console.log(`  DOM nodes ${result.nodes}; console errors ${result.errors} (total ${result.errorsTotal}), warnings ${result.warnings} (total ${result.warningsTotal}), pageerrors ${result.pageerrors}`);
  if (log.length) console.log('  ' + [...new Set(log.map((l) => l.slice(0, 120)))].slice(0, 5).join('\n  '));
}
await browser.close();
