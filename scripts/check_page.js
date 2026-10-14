#!/usr/bin/env node
/* Headless check of index.html: runs the page's own <script> against the real
   JSON with a tiny DOM shim, then asserts every section actually rendered.
   Usage: node scripts/check_page.js  (from the repo root) */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FAIL: no <script> in index.html'); process.exit(1); }
const code = m[1];

const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, innerHTML: '', textContent: '', value: '',
                            classList: { add(){}, remove(){} }, dataset: {}, onclick: null, oninput: null };
  return els[id];
}
const document = {
  getElementById: el,
  querySelectorAll: () => [],
};
const store = {
  'data/calendar.json':   JSON.parse(fs.readFileSync(path.join(root, 'data/calendar.json'))),
  'data/indicators.json': JSON.parse(fs.readFileSync(path.join(root, 'data/indicators.json'))),
  'data/meta.json':       JSON.parse(fs.readFileSync(path.join(root, 'data/meta.json'))),
};
const fetch = async (u) => {
  const k = Object.keys(store).find(x => u.includes(x));
  if (!k) throw new Error('404 ' + u);
  return { json: async () => store[k] };
};
const setInterval = () => 0;
const console_ = console;

(async () => {
  const fn = new Function('document', 'fetch', 'setInterval', 'console', code + '\nreturn {renderCards,renderNext,renderRows,boot};');
  const api = fn(document, fetch, setInterval, console_);
  await api.boot();

  let bad = 0;
  const check = (name, html_, min) => {
    const ok = html_ && html_.length >= min;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${name.padEnd(14)} ${html_.length} chars`);
    if (!ok) bad++;
  };
  check('cards',      el('cards').innerHTML, 500);
  check('next',       el('next').innerHTML, 500);
  check('rows',       el('rows').innerHTML, 2000);
  check('stamp',      el('stamp').innerHTML, 10);

  const rows = el('rows').innerHTML;
  for (const [tag, want] of [['tag hot', 'HOT'], ['tag cool', 'COOL'], ['tag same', 'ON TARGET']]) {
    const has = rows.includes(want) || el('next').innerHTML.includes(want);
    console.log(`${has ? 'OK  ' : 'FAIL'} badge ${want}`);
    if (!has) bad++;
  }
  const n = (rows.match(/<tr>/g) || []).length;
  console.log(`     table rows rendered: ${n}`);
  if (n < 20) { console.log('FAIL too few rows'); bad++; }

  // spot-check a known event against the raw JSON
  const cal = store['data/calendar.json'];
  const cpi = cal.find(e => e.title === 'Core Inflation Rate YoY' && e.actual !== null);
  if (cpi) {
    const inPage = rows.includes(cpi.title);
    console.log(`${inPage ? 'OK  ' : 'WARN'} Core Inflation Rate YoY present (actual ${cpi.actual} vs fc ${cpi.forecast})`);
  }
  console.log(bad ? `\n${bad} CHECK(S) FAILED` : '\nALL CHECKS PASSED');
  process.exit(bad ? 1 : 0);
})();
