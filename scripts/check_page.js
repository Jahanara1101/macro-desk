#!/usr/bin/env node
/* Headless render check: run the page's own <script> against the real data files
   with a tiny DOM shim, then call render() for EVERY sidebar page and assert none
   of them fell through to an error/empty placeholder. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO = process.argv[2] || '/root/macro-desk';
const html = fs.readFileSync(path.join(REPO, 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  // top-level const/let in vm.runInContext are lexical, not sandbox props.
  // getters, not values: boot() REBINDS SERIES/COT/... after its awaits resolve.
  + '\n;globalThis.__X={get FLAT(){return FLAT},get state(){return state},render,' +
    'get CAL(){return CAL},get SERIES(){return SERIES},get COT(){return COT},' +
    'get OPT(){return OPT},get WALLS(){return WALLS},get META(){return META}};';

function El(tag) {
  return {
    tagName: tag, innerHTML: '', textContent: '', dataset: {}, style: {},
    children: [], value: '',
    classList: { _s: new Set(), add(c){this._s.add(c);}, remove(c){this._s.delete(c);},
                 toggle(c,f){ f===undefined ? (this._s.has(c)?this._s.delete(c):this._s.add(c)) : (f?this._s.add(c):this._s.delete(c)); },
                 contains(c){return this._s.has(c);} },
    querySelector(){ return null; },
    querySelectorAll(){ return []; },
    addEventListener(){}, prepend(){}, appendChild(){},
  };
}
const store = {};
const document = {
  querySelector(sel) { return store[sel] || (store[sel] = El('div')); },
  querySelectorAll() { return []; },
  getElementById(id) { return store['#' + id] || (store['#' + id] = El('div')); },
  createElement(t) { return El(t); },
};
const sandbox = {
  document, console,
  location: { hash: '' },
  alert() {}, setTimeout, Date, Math, JSON, Object, Array, Number, String, isNaN, parseFloat,
  Intl,
  window: { print() {} },
  fetch: async (u) => {
    const f = path.join(REPO, u);
    if (!fs.existsSync(f)) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(f, 'utf8')) };
  },
};
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox, { filename: 'page.js' });

(async () => {
  for (let i = 0; i < 80; i++) {
    if (sandbox.__X && sandbox.__X.META && sandbox.__X.SERIES.length) break;
    await new Promise(r => setTimeout(r, 100));
  }
  const X = sandbox.__X;
  const dump = (process.argv[3] || '').replace('--dump=', '');
  if (dump) {
    X.state.page = dump;
    X.render();
    const h = (store['#body'].innerHTML || '')
      .replace(/<svg[\s\S]*?<\/svg>/g, '[CHART]')
      .replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ')
      .replace(/\s+/g, ' ').trim();
    console.log('=== ' + dump + ' ===\n' + h.slice(0, 1400));
    return;
  }
  const bad = [];
  const pages = X.FLAT.map(p => p.id);
  for (const id of pages) {
    X.state.page = id;
    try { X.render(); }
    catch (e) { bad.push([id, 'THREW ' + e.message]); continue; }
    const h = store['#body'].innerHTML || '';
    const err = h.match(/class="load"[^>]*>([\s\S]{0,90})/);
    if (err) bad.push([id, 'placeholder: ' + err[1].replace(/\s+/g, ' ').trim()]);
    else if (h.length < 120) bad.push([id, 'suspiciously short (' + h.length + ' chars)']);
  }
  console.log('pages checked:', pages.length);
  console.log('data: series=' + X.SERIES.length + ' cot=' + X.COT.length +
              ' walls=' + X.WALLS.length + ' opt=' + (X.OPT ? 'yes' : 'no') +
              ' cal_us=' + ((X.CAL || {}).cal_us || []).length);
  if (bad.length) { console.log('FAILURES:'); bad.forEach(b => console.log('  ' + b[0] + ' -> ' + b[1])); process.exit(1); }
  console.log('ALL PAGES RENDER OK');
})();
