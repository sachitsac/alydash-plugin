// Tests for ui_measure.mjs, the UI gate's measuring module.
//
// Run from a directory whose package.json can resolve @playwright/test:
//   cd <project> && node --test <skill>/scripts/test_ui_measure.mjs
//
// Each test serves a small static page from a local http server. Every page
// holds one planted failure and at least one control that must not be reported.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { execFile } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, symlinkSync, lstatSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const here = dirname(fileURLToPath(import.meta.url));
const MODULE = join(here, 'ui_measure.mjs');
const load = () => import('./ui_measure.mjs');
const { chromium } = createRequire(join(process.cwd(), 'package.json'))('@playwright/test');

const RULE_IDS = [
  'A11Y-FOCUS',
  'A11Y-KEYBOARD',
  'A11Y-NAME',
  'A11Y-TARGET',
  'COLOR-CONTRAST',
  'LAYOUT-CLIP',
  'TYPE-HEADING-ORDER',
  'TYPE-TRUNCATE',
  'UI-TRANSITION-ALL',
];
const FOUR = ['desktop-light', 'desktop-dark', 'phone', 'narrow'];

// One fragment per read: css, what sits outside the root, what sits inside it.
const FRAGMENTS = {
  contrast: {
    css: `.low, .outside-low { color: #999 } .ok { color: #222 }
      @media (prefers-color-scheme: dark) { .panel { background: #222 } .darkonly { color: #333 } }`,
    outside: `<p class="outside-low">low contrast, outside the root</p>`,
    inside: `<p class="low">low contrast</p><p class="ok">passing text</p>
      <div class="panel"><p class="darkonly">fine in light, dim in dark</p></div>`,
  },
  clip: {
    css: `.wide { width: 360px; height: 20px } .left { position: relative; left: -30px; width: 100px; height: 20px }
      .skip { position: absolute; left: -9999px; top: 0; width: 200px; height: 40px }
      .sr-only { position: absolute; left: 300px; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap }
      .hush { overflow: hidden } .ghost { width: 400px; height: 10px }`,
    inside: `<a class="skip" href="#main">Skip to content</a><div class="wide">wide</div><div class="left">left</div>
      <span class="sr-only"><b class="sr-inner">a long line for screen readers only</b></span>
      <div class="hush" aria-hidden="true"><div class="ghost"></div></div><p class="fits">fits</p>`,
  },
  focus: {
    // .slow changes its border after a delay: read too early, it looks like no indicator.
    css: `.bare:focus, .outside-bare:focus { outline: none }
      .shadow:focus { outline: none; box-shadow: 0 0 0 3px #06c }
      .slow { border: 2px solid #888; transition: border-color 300ms 3s } .slow:focus { outline: none; border-color: #06c }
      .sr { opacity: 0; position: absolute } .sr:focus { outline: none } .sr:focus + .box { outline: 2px solid #06c }
      .box { display: inline-block; width: 30px; height: 30px }`,
    outside: `<button class="outside-bare">outside</button>`,
    inside: `<button class="bare">bare</button><button class="plain">plain</button>
      <button class="shadow">shadow</button><button class="slow">slow</button>
      <label class="toggle"><input type="checkbox" class="sr"><span class="box"></span>toggle</label>`,
  },
  truncate: {
    css: `.cell { width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }`,
    inside: `<div class="cut cell">a long line of text that cannot fit in eighty pixels</div>
      <div class="titled cell" title="a long line of text that cannot fit">a long line of text that cannot fit</div>
      <div class="short cell">fits</div>`,
  },
  headingBad: {
    css: `h1 { font-size: 20px } h2 { font-size: 28px }`,
    inside: `<h1>Small first</h1><h2>Large second</h2>`,
  },
  headingGood: {
    css: `h1 { font-size: 32px } h2 { font-size: 24px } h3 { font-size: 18px }`,
    inside: `<h1>One</h1><h2>Two</h2><h3>Three</h3>`,
  },
  name: {
    css: `input, select { height: 44px }`,
    inside: `<input class="bare" placeholder="Search"><select class="bare-select"><option>one</option></select>
      <input class="aria" aria-label="Search"><label for="fl">For</label><input id="fl" class="forlabel">
      <label>Wrapped <input class="wrapped"></label>`,
  },
  keyboard: {
    css: `.fake, .inner { cursor: pointer }`,
    inside: `<span class="fake" onclick="this.dataset.hit = 1">click <b class="child">me</b></span>
      <button class="real"><span class="inner">inside a button</span></button>`,
  },
  transition: {
    css: `.all { transition: all 600ms } .one { transition: opacity 150ms } .zero { transition: all 0s }`,
    inside: `<div class="all">all</div><div class="one">one</div><div class="zero">zero</div>`,
  },
  target: {
    css: `.tiny { display: inline-block; width: 80px; height: 18px }
      .mid { display: block; width: 120px; height: 30px; padding: 0 } .big { display: block; width: 120px; height: 48px }`,
    inside: `<div class="meta"><a class="tiny" href="#x">tiny</a></div><button class="mid">mid</button><button class="big">big</button>
      <p class="prose">Read the <a class="inline" href="#y">manual</a> first.</p>`,
  },
  scroller: {
    css: `.scroller { overflow-x: auto } table { width: 600px; border-collapse: collapse } td { padding: 0 }`,
    inside: `<div class="scroller"><table class="t-wrapped"><tr><td class="c-wrapped">wide, in a scroller</td></tr></table></div>
      <table class="t-bare"><tr><td class="c-bare">wide, bare</td></tr></table>`,
  },
  shellScroll: {
    css: `.shell-wide { width: 500px; height: 10px }`,
    outside: `<div class="shell-wide"></div>`,
    inside: `<p class="fits">fits</p>`,
  },
  placement: {
    css: `.top { height: 200px } .half { height: 400px } .half p { height: 200px; margin: 0 } .far { margin-top: 2000px; height: 100px }`,
    inside: `<div class="top">top</div><div class="half"><p>half full</p></div><div class="far">far</div>`,
  },
};

const html = (...names) => {
  const parts = names.map((n) => FRAGMENTS[n]);
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>t</title>
<style>body { margin: 0; font: 16px/1.4 sans-serif; background: #fff; color: #000 } p { margin: 0 }
${parts.map((p) => p.css).join('\n')}</style></head>
<body>${parts.map((p) => p.outside ?? '').join('\n')}
<main class="screen">${parts.map((p) => p.inside).join('\n')}</main></body></html>`;
};

// Gate 6. One planted failure for each read, OUTSIDE the root, keyed by the
// rule that read reports, with the place it is reported at when the root is
// body. The root itself holds nothing wrong. The test below holds these keys
// to the module's exported RULES, so a tenth read without a case here fails.
const OUTSIDE = {
  'COLOR-CONTRAST': { css: `.o-low { color: #999 }`, body: `<p class="o-low">low contrast</p>`, where: 'p.o-low 16px' },
  'LAYOUT-CLIP': {
    css: `.o-left { position: relative; left: -30px; width: 100px; height: 20px }`,
    body: `<div class="o-left">left</div>`,
    where: 'div.o-left',
  },
  'A11Y-FOCUS': { css: `.o-bare { height: 30px } .o-bare:focus { outline: none }`, body: `<button class="o-bare">outside</button>`, where: 'button.o-bare' },
  'TYPE-TRUNCATE': {
    css: `.o-cut { width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }`,
    body: `<div class="o-cut">a long line of text that cannot fit in eighty pixels</div>`,
    where: 'div.o-cut',
  },
  'TYPE-HEADING-ORDER': { css: `h1 { font-size: 20px; margin: 0 } h2 { font-size: 28px; margin: 0 }`, body: `<h1>Small first</h1><h2>Large second</h2>`, where: 'h2' },
  'A11Y-NAME': { css: `.o-input { height: 44px; width: 200px; display: block }`, body: `<input class="o-input" placeholder="Search">`, where: 'input.o-input' },
  'A11Y-KEYBOARD': { css: `.o-fake { cursor: pointer }`, body: `<span class="o-fake">click me</span>`, where: 'span.o-fake' },
  'UI-TRANSITION-ALL': { css: `.o-all { transition: all 600ms }`, body: `<div class="o-all">all</div>`, where: 'div.o-all' },
  'A11Y-TARGET': { css: `.o-tiny { display: inline-block; width: 80px; height: 18px }`, body: `<div><a class="o-tiny" href="#x">tiny</a></div>`, where: 'a.o-tiny' },
};

// A whole page, for the cases that are about the root itself.
const raw = (body, css = '') => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>t</title>
<style>body { margin: 0; font: 16px/1.4 sans-serif; background: #fff; color: #000 } p { margin: 0 }
${css}</style></head><body>${body}</body></html>`;

const EDGE_BUTTON = `button { display: block; box-sizing: border-box; border: 0; padding: 0; margin: 4px 0; width: 120px }`;
const buttons = (count) => Array.from({ length: count }, (_, i) => `<button class="b${i + 1}">b${i + 1}</button>`).join('');
// A class a page chose to be read as a command, and long: 505 characters.
const LOUD = `$(id)${'a'.repeat(500)}`;
const WALK_CSS = `button { box-sizing: border-box; height: 30px; width: 60px }`;

const PAGES = {
  ...Object.fromEntries(Object.keys(FRAGMENTS).map((n) => [n, html(n)])),
  everything: html('contrast', 'clip', 'focus', 'truncate', 'headingBad', 'name', 'keyboard', 'transition', 'target', 'placement'),
  outside: raw(
    `${Object.values(OUTSIDE).map((o) => o.body).join('\n')}<main class="screen"><p class="fits">nothing wrong in here</p></main>`,
    Object.values(OUTSIDE).map((o) => o.css).join('\n'),
  ),
  // 4.49 to 1 and 4.5 to 1 on white, to two places as the module rounds.
  contrastEdge: raw(
    `<main class="screen"><p class="under">just under</p><p class="exact">exactly</p></main>`,
    `.under { color: rgb(120, 120, 102) } .exact { color: rgb(121, 116, 136) }`,
  ),
  targetEdge: raw(
    `<main class="screen"><button class="t23">23</button><button class="t24">24</button><button class="t43">43</button><button class="t44">44</button></main>`,
    `${EDGE_BUTTON} .t23 { height: 23px } .t24 { height: 24px } .t43 { height: 43px } .t44 { height: 44px }`,
  ),
  headingEqual: raw(`<main class="screen"><h1>One</h1><h2>Two</h2></main>`, `h1, h2 { font-size: 24px; margin: 0 }`),
  twoRoots: raw(`<div class="pane"><p>first</p></div><div class="pane"><p class="low">second</p></div>`, `.low { color: #999 }`),
  hiddenRoot: raw(`<main class="screen" style="display: none"><p class="low">hidden</p></main><p>beside</p>`, `.low { color: #999 }`),
  unseenRoot: raw(`<main class="screen" style="visibility: hidden"><p>unseen</p></main><p>beside</p>`),
  flatRoot: raw(`<main class="screen" style="height: 0; overflow: hidden"><p>flat</p></main><p>beside</p>`),
  emptyRoot: raw(`<main class="screen"></main><p>beside</p>`),
  blankRoot: raw(`<main class="screen"> <div style="height: 40px"></div> </main><p>beside</p>`),
  walk200: raw(`<main class="screen">${buttons(200)}</main>`, WALK_CSS),
  walk201: raw(`<main class="screen">${buttons(201)}</main>`, WALK_CSS),
  // Gate 8. The walk: a ringless button sits where each of these shapes used to hide it.
  walkAutofocus: raw(
    `<main class="screen"><button class="first">first</button><button class="lost">no ring</button><input class="auto" aria-label="auto" autofocus><button class="after">after</button></main>`,
    `${WALK_CSS} input { height: 30px } .lost:focus { outline: none }`,
  ),
  walkShadow: raw(
    `<main class="screen"><button class="first">first</button><x-box class="host"></x-box><button class="last">last</button></main>
    <script>document.querySelector('x-box').attachShadow({ mode: 'open' }).innerHTML =
      '<style>button { height: 30px; width: 60px } .ringless:focus { outline: none }</style><button class="ringless">no ring</button><button class="ringed">ring</button>';</script>`,
    WALK_CSS,
  ),
  walkClosed: raw(
    `<main class="screen"><button class="first">first</button><x-box class="sealed"></x-box><button class="lost">no ring</button></main>
    <script>document.querySelector('x-box').attachShadow({ mode: 'closed' }).innerHTML = '<button>one</button><button>two</button>';</script>`,
    `${WALK_CSS} .lost:focus { outline: none }`,
  ),
  walkFrame: raw(
    `<main class="screen"><button class="first">first</button><iframe class="pane" title="pane" srcdoc="<button>in one</button><button>in two</button>"></iframe><button class="lost">no ring</button></main>`,
    `${WALK_CSS} .lost:focus { outline: none }`,
  ),
  // Gate 8. Contrast: what the ground is, which colour paints the text, and how faded it is.
  contrastGround: raw(
    `<main class="screen"><div class="hero"><p class="onhero">white on a dark gradient</p></div>
    <div class="pic"><p class="onpic">white on a dark image</p></div>
    <div class="flat"><p class="onflat">white on a dark colour</p></div>
    <div class="under"><div class="card"><p class="oncard">dim on a white card that covers a gradient</p></div></div></main>`,
    `.hero, .under { background: linear-gradient(#000, #222) } .flat { background: #111 } .card { background: #fff }
    .pic { background-image: url("data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7") }
    .onhero, .onpic, .onflat { color: #fff } .oncard { color: #999 }`,
  ),
  contrastSvg: raw(
    `<main class="screen"><p>a chart</p><svg width="400" height="120" style="color: #ddd">
    <defs><linearGradient id="g"><stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#333"/></linearGradient></defs>
    <text x="0" y="30" font-size="20" fill="#ddd">faint by its fill</text>
    <text x="0" y="60" font-size="16" fill="#000">dark by its fill, under a light color</text>
    <text x="0" y="100" font-size="24" fill="url(#g)">filled with a gradient</text></svg></main>`,
  ),
  contrastOpacity: raw(
    `<main class="screen"><p class="faded">black at a fifth</p><div class="dim"><p class="child">black in a faded parent</p></div>
    <p class="slight">black, barely faded</p><p class="solid">black</p></main>`,
    `.faded { opacity: 0.2 } .dim { opacity: 0.3 } .slight { opacity: 0.9 }`,
  ),
  // Gate 8. Clipping: cut by an ancestor is a failure, crossing the viewport inside a clipping frame is not.
  clipCut: raw(
    `<main class="screen"><div class="frame"><button class="save">Save changes</button></div>
    <div class="box"><span class="total">Total owed: 1,234,567 and a long tail of words</span></div>
    <div class="direct">Total owed: 1,234,567 and a long tail of words</div>
    <div class="roomy"><button class="whole">Fits</button><span class="short">fits</span></div>
    <div class="cell" title="a long line of text that cannot fit">a long line of text that cannot fit</div>
    <div class="outer"><div class="scroll"><span class="reach">a long line that its own scroller can bring into view</span></div></div>
    <div class="outer"><div class="still"><span class="lost">Total owed: 1,234,567 and more</span></div></div>
    <div class="low"><button class="buried">Save changes</button></div><div class="squat"><button class="tall">Tall</button></div>
    <div class="lowclip"><div class="yscroll"><p class="deep">one<br>two<br>three<br>four<br>five<br>six</p></div></div>
    <span class="sr-only"><button class="sr-btn">for screen readers only, and long enough to be cut</button></span></main>`,
    `button { box-sizing: border-box; height: 44px; margin: 0; padding: 0 }
    .frame { width: 100px; overflow: hidden } .save { width: 160px }
    .box, .direct, .cell { width: 150px; overflow: hidden; white-space: nowrap } .cell { text-overflow: ellipsis }
    .low { height: 20px; overflow: hidden } .buried { margin-top: 40px; height: 30px; display: block } .squat { height: 30px; overflow: hidden } .tall { display: block }
    .lowclip { height: 50px; overflow: hidden } .yscroll { height: 50px; overflow-y: auto }
    .roomy { width: 200px; overflow: hidden } .whole { width: 80px }
    .outer { width: 150px; overflow: hidden } .scroll { overflow-x: auto; white-space: nowrap } .still { overflow-x: auto; width: 600px; white-space: nowrap }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap }`,
  ),
  clipCarousel: raw(
    `<main class="screen"><div class="strip"><div class="track"><div class="card"><button class="go">Slide one</button></div>
    <div class="card"><button class="go">Slide two</button></div><div class="card"><button class="go">Slide three</button></div></div></div>
    <div class="peekstrip"><div class="peektrack"><div class="slide"><span class="cap">Slide caption</span></div>
    <div class="slide"><span class="cap">Slide caption</span></div><div class="slide"><span class="cap">Slide caption</span></div></div></div>
    <div class="left">left</div></main>`,
    `.strip { width: 100%; overflow: hidden } .track { display: flex; width: 300% } .card { flex: 0 0 33.3333% }
    .peekstrip { width: 250px; overflow: hidden } .peektrack { display: flex; width: 600px } .slide { flex: 0 0 200px }
    .go { height: 44px; width: 120px } .left { position: relative; left: -30px; width: 100px; height: 20px }`,
  ),
  // Gate 8. Where the page ended up, a page that never settles, and what a page may put in a key.
  login: raw(`<form><label>Name <input class="who"></label><button class="in">Sign in</button></form>`, `input, button { height: 44px }`),
  poll: raw(`<main class="screen"><p>busy</p></main><script>setInterval(() => fetch('/headingGood.html'), 200);</script>`),
  loudKeys: raw(
    `<main class="screen"><p class="${LOUD} low">low</p><p class="$$$ low">low</p><x-$(id) class="low">low</x-$(id)>
    <button class="${LOUD}">no ring</button></main>`,
    `p, x-\\$\\(id\\) { color: #999; display: block } button { height: 44px; width: 120px } button:focus { outline: none }`,
  ),
  loudName: raw(
    `<main class="screen"><button class="esc"></button><button class="calm">calm</button></main>
    <script>document.querySelector('.esc').textContent = 'a\\u001b[31mred\\u0007\\u009b' + 'x'.repeat(60);</script>`,
    WALK_CSS,
  ),
  walkFrameTrap: raw(
    `<main class="screen"><button class="first">first</button><iframe class="trap" title="trap" srcdoc="<button onkeydown='event.preventDefault()'>held</button>"></iframe><button class="lost">no ring</button></main>`,
    `${WALK_CSS} .lost:focus { outline: none }`,
  ),
};

const SCREEN = { screen: 'test page', route: '/t', root: '.screen', jobs: ['read it'], blocks: [] };

let dir, server, other, base, elsewhere, browser;

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'ui-measure-'));
  for (const [name, body] of Object.entries(PAGES)) writeFileSync(join(dir, `${name}.html`), body);
  for (const status of [400, 404, 500]) writeFileSync(join(dir, `status${status}.html`), PAGES.headingGood);
  writeFileSync(join(dir, 'status399.html'), PAGES.headingGood);
  const answer = (req, res) => {
    // Gate 8. Redirects, to another path and to another port, and a page only a signed-in browser gets.
    const to = { '/go-path': '/headingGood.html', '/go-login': '/login.html', '/go-port': `${elsewhere}/headingGood.html` }[req.url];
    if (to) return res.writeHead(302, { location: to }).end();
    if (req.url === '/members' && !/\bsession=held\b/.test(req.headers.cookie ?? '')) return res.writeHead(401).end('sign in');
    if (req.url === '/members') req.url = '/headingGood.html';
    try {
      const body = readFileSync(join(dir, req.url.split('?')[0].replace(/^\//, '').replace(/[^\w.-]/g, '')));
      // Gate 6. A page whose name starts `status` answers that status, with a body a browser draws.
      const status = Number(/^\/status(\d+)/.exec(req.url)?.[1] ?? 200);
      res.writeHead(status, { 'content-type': 'text/html; charset=utf-8' }).end(body);
    } catch {
      res.writeHead(404).end('not found');
    }
  };
  server = createServer(answer);
  other = createServer(answer);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  await new Promise((resolve) => other.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  elsewhere = `http://127.0.0.1:${other.address().port}`;
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
  server?.close();
  other?.close();
  if (dir) rmSync(dir, { recursive: true, force: true });
});

// `via` is the browser handed to the module, when a case hands it another.
const measure = async (page, extra = {}) => {
  const { measureScreen } = await load();
  const { screen, via, url, ...rest } = extra;
  return measureScreen(via ?? browser, { url: url ?? `${base}/${page}.html`, screen: { ...SCREEN, ...screen }, state: 'static', ...rest });
};
const wheres = (result, context, rule, list = 'failures') =>
  result.contexts[context][list].filter((f) => f.rule === rule).map((f) => f.where);

test('the result has the four contexts at their true sizes, each with every list', async () => {
  const result = await measure('headingGood');
  assert.deepEqual(Object.keys(result), ['screen', 'route', 'state', 'root', 'contexts']);
  assert.deepEqual([result.screen, result.route, result.state, result.root], ['test page', '/t', 'static', '.screen']);
  assert.deepEqual(Object.keys(result.contexts), FOUR);
  assert.deepEqual(FOUR.map((c) => [result.contexts[c].innerWidth, result.contexts[c].innerHeight]), [[1280, 800], [1280, 800], [390, 844], [320, 700]]);
  for (const c of FOUR) {
    assert.deepEqual(Object.keys(result.contexts[c]), ['innerWidth', 'innerHeight', 'scrollWidth', 'failures', 'notes', 'keyboard', 'placement']);
    assert.deepEqual(result.contexts[c].failures, [], `${c} reports nothing on a clean page`);
  }
});

test('COLOR-CONTRAST: low-contrast text inside the root is reported, passing text and text outside the root are not', async () => {
  const result = await measure('contrast');
  assert.deepEqual(wheres(result, 'desktop-light', 'COLOR-CONTRAST'), ['p.low 16px']);
  assert.deepEqual(wheres(result, 'desktop-dark', 'COLOR-CONTRAST'), ['p.darkonly 16px', 'p.low 16px']);
  assert.match(result.contexts['desktop-light'].failures[0].detail, /2\.85/);
  assert.deepEqual(wheres(result, 'phone', 'COLOR-CONTRAST'), []);
  assert.deepEqual(wheres(result, 'narrow', 'COLOR-CONTRAST'), []);
});

test('with no root in the screen file the root is body, and the outside text is then inside', async () => {
  const result = await measure('contrast', { screen: { root: undefined } });
  assert.equal(result.root, 'body');
  assert.deepEqual(wheres(result, 'desktop-light', 'COLOR-CONTRAST'), ['p.low 16px', 'p.outside-low 16px']);
});

test('a root that matches nothing is refused, not measured as an empty pass', async () => {
  await assert.rejects(measure('contrast', { screen: { root: '.no-such-root' } }), /\.no-such-root/);
});

test('LAYOUT-CLIP: a box crossing an edge is reported; hidden text, aria-hidden and a parked skip link are not', async () => {
  const result = await measure('clip');
  assert.deepEqual(wheres(result, 'narrow', 'LAYOUT-CLIP'), ['div.left', 'div.wide', 'document']);
  const wide = result.contexts.narrow.failures.find((f) => f.where === 'div.wide');
  assert.match(wide.detail, /40px past the right edge/);
  const scroll = result.contexts.narrow.failures.find((f) => f.where === 'document');
  assert.match(scroll.detail, /360/);
  assert.match(scroll.detail, /320/);
  assert.equal(result.contexts.narrow.scrollWidth, 360);
  // 360 fits in 390: only the element past the left edge remains.
  assert.deepEqual(wheres(result, 'phone', 'LAYOUT-CLIP'), ['div.left']);
  assert.deepEqual(wheres(result, 'desktop-light', 'LAYOUT-CLIP'), []);
});

test('LAYOUT-CLIP: content in its own sideways scroller is a note, the same content with no scroller fails', async () => {
  const result = await measure('scroller');
  for (const c of ['phone', 'narrow']) {
    const failed = wheres(result, c, 'LAYOUT-CLIP');
    assert.ok(failed.includes('table.t-bare') && failed.includes('td.c-bare'), `${c}: ${failed}`);
    assert.ok(!failed.includes('table.t-wrapped') && !failed.includes('td.c-wrapped'), `${c}: ${failed}`);
    assert.deepEqual(wheres(result, c, 'LAYOUT-CLIP', 'notes'), ['div.scroller']);
    assert.match(result.contexts[c].notes.find((n) => n.where === 'div.scroller').detail, /scrolls sideways/);
  }
  assert.deepEqual(result.contexts['desktop-light'].notes, []);
});

test('LAYOUT-CLIP: sideways scroll caused outside the root still fails, and says where it comes from', async () => {
  const result = await measure('shellScroll');
  assert.deepEqual(wheres(result, 'narrow', 'LAYOUT-CLIP'), ['document, outside the root']);
  assert.deepEqual(wheres(result, 'phone', 'LAYOUT-CLIP'), ['document, outside the root']);
});

test('A11Y-FOCUS: a stop with no indicator fails; an outline, a shadow, a late border and a sibling outline pass', async () => {
  const result = await measure('focus');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(result.contexts[c].keyboard, [
      { where: 'button.bare', name: 'bare', indicator: 'none' },
      { where: 'button.plain', name: 'plain', indicator: 'outline' },
      { where: 'button.shadow', name: 'shadow', indicator: 'paint change' },
      { where: 'button.slow', name: 'slow', indicator: 'paint change' },
      { where: 'input.sr', name: 'toggle', indicator: 'outline' },
    ]);
    assert.deepEqual(wheres(result, c, 'A11Y-FOCUS'), ['button.bare']);
  }
  assert.deepEqual(result.contexts.phone.keyboard, []);
  assert.deepEqual(wheres(result, 'phone', 'A11Y-FOCUS'), []);
});

test('TYPE-TRUNCATE: an ellipsis cut with no title is reported; one with a title and one that fits are not', async () => {
  const result = await measure('truncate');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'TYPE-TRUNCATE'), ['div.cut']);
});

test('TYPE-HEADING-ORDER: an h2 larger than the h1 is reported; descending sizes are not', async () => {
  const bad = await measure('headingBad');
  assert.deepEqual(wheres(bad, 'desktop-light', 'TYPE-HEADING-ORDER'), ['h2']);
  assert.match(bad.contexts['desktop-light'].failures[0].detail, /h2 at 28px is larger than h1 at 20px/);
  const good = await measure('headingGood');
  assert.deepEqual(wheres(good, 'desktop-light', 'TYPE-HEADING-ORDER'), []);
});

test('A11Y-NAME: a placeholder is not a name; aria-label, label for and a wrapping label are', async () => {
  const result = await measure('name');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'A11Y-NAME'), ['input.bare', 'select.bare-select']);
});

test('A11Y-KEYBOARD: a pointer-cursor span nobody can focus is reported; one inside a button is not', async () => {
  const result = await measure('keyboard');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'A11Y-KEYBOARD'), ['span.fake']);
});

test('UI-TRANSITION-ALL: transition all with a duration is reported; a named property and a zero duration are not', async () => {
  const result = await measure('transition');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'UI-TRANSITION-ALL'), ['div.all']);
  assert.match(result.contexts.phone.failures[0].detail, /0\.6s/);
});

test('A11Y-TARGET: a lone link under 24 fails everywhere, a link in a sentence does not; 24 to under 44 is a note on touch only', async () => {
  const result = await measure('target');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'A11Y-TARGET'), ['a.tiny'], c);
  assert.deepEqual(wheres(result, 'phone', 'A11Y-TARGET', 'notes'), ['button.mid']);
  assert.deepEqual(wheres(result, 'narrow', 'A11Y-TARGET', 'notes'), ['button.mid']);
  assert.deepEqual(result.contexts['desktop-light'].notes, []);
  assert.deepEqual(result.contexts['desktop-dark'].notes, []);
});

test('placement: each block reports where it sits, how much room it takes and how full it is', async () => {
  const blocks = [
    { name: 'top', selector: '.top', serves: [1] },
    { name: 'half', selector: '.half', serves: [] },
    { name: 'far', selector: '.far', serves: [1] },
    { name: 'gone', selector: '.gone', serves: [] },
  ];
  const result = await measure('placement', { screen: { blocks } });
  const [top, half, far, gone] = result.contexts['desktop-light'].placement;
  // One line of text in a 200px box: the exact fill depends on the font.
  const { fill, ...rest } = top;
  assert.deepEqual(rest, {
    name: 'top', selector: '.top', serves: [1], top: 0, width: 1280, height: 200,
    firstScreenShare: 25, pageShare: 7, onFirstScreen: true,
  });
  assert.ok(fill > 5 && fill < 20, `one line fills ${fill}% of 200px`);
  assert.equal(half.top, 200);
  assert.equal(half.fill, 50);
  assert.equal(half.onFirstScreen, true);
  assert.equal(far.top, 2600);
  assert.equal(far.onFirstScreen, false);
  assert.equal(far.firstScreenShare, 0);
  assert.deepEqual(gone, { name: 'gone', selector: '.gone', serves: [], absent: true });
  const phone = result.contexts.phone.placement;
  assert.deepEqual(phone.map((b) => b.width), [390, 390, 390, undefined]);
  assert.equal(phone[1].fill, 50);
  assert.deepEqual(result.contexts['desktop-dark'].placement, []);
  assert.deepEqual(result.contexts.narrow.placement, []);
});

test('the width guard: a phone read on a 500 wide window is refused, with both numbers', async () => {
  const { CONTEXTS } = await load();
  const phone = CONTEXTS.find((c) => c.name === 'phone');
  const wrong = [{ ...phone, viewport: { width: 500, height: 844 } }];
  await assert.rejects(measure('headingGood', { contexts: wrong }), (error) => {
    assert.match(error.message, /390/);
    assert.match(error.message, /500/);
    return true;
  });
  // The same override at the true width is accepted.
  const right = await measure('headingGood', { contexts: [phone] });
  assert.deepEqual(Object.keys(right.contexts), ['phone']);
});

test('two runs on one page serialise to the same bytes', async () => {
  const first = JSON.stringify(await measure('everything'));
  const second = JSON.stringify(await measure('everything'));
  assert.equal(first, second);
  assert.ok(first.length > 2000, 'the page with everything on it is not an empty result');
});

test('reads carry their rule ids exactly as in the table', async () => {
  const { RULES } = await load();
  assert.deepEqual([...RULES].sort(), RULE_IDS);
  const result = await measure('everything');
  const seen = new Set();
  for (const c of FOUR) for (const f of [...result.contexts[c].failures, ...result.contexts[c].notes]) seen.add(f.rule);
  assert.deepEqual([...seen].sort(), RULE_IDS);
  for (const c of FOUR) {
    for (const f of result.contexts[c].failures) assert.deepEqual(Object.keys(f), ['rule', 'where', 'detail']);
    const sorted = [...result.contexts[c].failures].sort((a, b) => {
      const x = [a.rule, a.where, a.detail].join('\n');
      const y = [b.rule, b.where, b.detail].join('\n');
      return x < y ? -1 : x > y ? 1 : 0;
    });
    assert.deepEqual(result.contexts[c].failures, sorted, `${c} failures are sorted`);
  }
});

test('run directly, the command prints the JSON, or writes it to --out', async () => {
  const run = promisify(execFile);
  const screenFile = join(dir, 'screen.json');
  writeFileSync(screenFile, JSON.stringify(SCREEN));
  const printed = await run(process.execPath, [MODULE, '--url', `${base}/target.html`, '--screen', screenFile]);
  const result = JSON.parse(printed.stdout);
  assert.equal(result.state, null);
  const named = await run(process.execPath, [MODULE, '--url', `${base}/target.html`, '--screen', screenFile, '--state', 'finished']);
  assert.equal(JSON.parse(named.stdout).state, 'finished');
  assert.deepEqual(Object.keys(result.contexts), FOUR);
  assert.deepEqual(wheres(result, 'narrow', 'A11Y-TARGET'), ['a.tiny']);
  const out = join(dir, 'out.json');
  await run(process.execPath, [MODULE, '--url', `${base}/target.html`, '--screen', screenFile, '--out', out]);
  assert.equal(readFileSync(out, 'utf8'), printed.stdout);
});

// ---------- ALYDAS-223, Gate 6: what the mutation pass found ----------

test('every read is confined to the root: a failure planted outside it, for each of the module\'s rules, is not reported', async () => {
  const { RULES } = await load();
  // The cases are the module's rules: a read added to RULES with no case here fails this line.
  assert.deepEqual(Object.keys(OUTSIDE).sort(), [...RULES].sort());
  const result = await measure('outside');
  for (const rule of RULES) {
    for (const c of FOUR) {
      assert.deepEqual(wheres(result, c, rule), [], `${rule} outside the root is reported in ${c}`);
      assert.deepEqual(wheres(result, c, rule, 'notes'), [], `${rule} outside the root is noted in ${c}`);
    }
  }
  for (const c of FOUR) assert.deepEqual(result.contexts[c].failures, [], c);
  assert.deepEqual(result.contexts['desktop-light'].keyboard, [], 'the stop outside the root is not kept');
  // Control: the same page with body as its root reports every one of them, so each plant is a real failure.
  const whole = await measure('outside', { screen: { root: 'body' } });
  for (const rule of RULES) {
    const seen = FOUR.flatMap((c) => wheres(whole, c, rule));
    assert.ok(seen.includes(OUTSIDE[rule].where), `${rule}: ${OUTSIDE[rule].where} is not among ${JSON.stringify(seen)}`);
  }
});

test('COLOR-CONTRAST, both sides of the floor: 4.49 to 1 fails and exactly 4.5 to 1 passes', async () => {
  const { CONTRAST_MIN } = await load();
  assert.equal(CONTRAST_MIN, 4.5);
  const result = await measure('contrastEdge');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST'), ['p.under 16px'], c);
    assert.match(result.contexts[c].failures[0].detail, /^4\.49 to 1/);
  }
});

test('A11Y-TARGET, both sides of both bounds: 23 fails and 24 does not; on touch 43 is a note and 44 is not', async () => {
  const { TARGET_MIN, TOUCH_TARGET_MIN } = await load();
  assert.deepEqual([TARGET_MIN, TOUCH_TARGET_MIN], [24, 44]);
  const result = await measure('targetEdge');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'A11Y-TARGET'), ['button.t23'], c);
  for (const c of ['phone', 'narrow']) assert.deepEqual(wheres(result, c, 'A11Y-TARGET', 'notes'), ['button.t24', 'button.t43'], c);
  for (const c of ['desktop-light', 'desktop-dark']) assert.deepEqual(wheres(result, c, 'A11Y-TARGET', 'notes'), [], c);
  assert.match(result.contexts.phone.failures[0].detail, /120x23, under 24px/);
  assert.match(result.contexts.phone.notes[1].detail, /120x43, under 44px on touch/);
});

// A browser that opens every window `delta` pixels wider than it was asked to.
const off = (delta) => ({
  newContext: (options) => browser.newContext({ ...options, viewport: { ...options.viewport, width: options.viewport.width + delta } }),
});

test('the width guard is every context\'s: a page that reads wider or narrower than was asked is refused, with both numbers', async () => {
  const { CONTEXTS } = await load();
  assert.deepEqual(CONTEXTS.map((c) => c.name), FOUR);
  for (const spec of CONTEXTS) {
    for (const delta of [110, -90]) {
      const asked = spec.viewport.width;
      await assert.rejects(
        measure('headingGood', { contexts: [spec], via: off(delta) }),
        (error) => {
          assert.match(error.message, new RegExp(`\\b${spec.name}\\b`), error.message);
          assert.match(error.message, new RegExp(`\\b${asked}\\b`), error.message);
          assert.match(error.message, new RegExp(`\\b${asked + delta}\\b`), error.message);
          return true;
        },
        `${spec.name} at ${asked + delta} for ${asked}`,
      );
    }
    // Control: the same context from a browser that does as it is asked.
    const right = await measure('headingGood', { contexts: [spec] });
    assert.equal(right.contexts[spec.name].innerWidth, spec.viewport.width);
  }
});

test('a read cannot borrow a name: one of the four names at another width is refused, and a new name is taken at its own', async () => {
  const { CONTEXTS } = await load();
  for (const spec of CONTEXTS) {
    // No `width` field to leave out: the name alone says how wide the read is.
    const { name, colorScheme } = spec;
    await assert.rejects(measure('headingGood', { contexts: [{ name, colorScheme, viewport: { width: 500, height: 800 } }] }), (error) => {
      assert.match(error.message, new RegExp(`\\b${spec.viewport.width}\\b`), error.message);
      assert.match(error.message, /\b500\b/, error.message);
      return true;
    });
  }
  const tablet = await measure('headingGood', { contexts: [{ name: 'tablet', viewport: { width: 768, height: 1024 } }] });
  assert.equal(tablet.contexts.tablet.innerWidth, 768);
});

test('the phone and narrow contexts are opened with touch and their device scale factor, the desktops without', async () => {
  const opened = [];
  const watching = { newContext: (options) => (opened.push(options), browser.newContext(options)) };
  await measure('headingGood', { via: watching });
  assert.deepEqual(
    opened.map((o) => [o.viewport.width, o.hasTouch, o.deviceScaleFactor, o.colorScheme]),
    [[1280, false, 1, 'light'], [1280, false, 1, 'dark'], [390, true, 3, 'light'], [320, true, 2, 'light']],
  );
});

test('a root is one real thing: none, several, hidden, flat or empty is refused, and the message says which', async () => {
  const refused = (page, root, says) =>
    assert.rejects(measure(page, { screen: { root } }), (error) => {
      assert.match(error.message, says, error.message);
      assert.ok(error.message.includes(root), error.message);
      assert.match(error.message, /no result is reported/);
      return true;
    }, `${page} ${root}`);
  await refused('contrast', '.no-such-root', /matches nothing/);
  await refused('twoRoots', '.pane', /matches 2 elements/);
  await refused('hiddenRoot', '.screen', /is hidden/);
  await refused('unseenRoot', '.screen', /is hidden/);
  await refused('flatRoot', '.screen', /has a box with no size/);
  await refused('emptyRoot', '.screen', /holds no text/);
  await refused('blankRoot', '.screen', /holds no text/);
  // Control: the second pane by a selector that matches it alone is measured, and its failure is found.
  const second = await measure('twoRoots', { screen: { root: '.pane + .pane' } });
  assert.deepEqual(wheres(second, 'desktop-light', 'COLOR-CONTRAST'), ['p.low 16px']);
});

test('a page that answers 400 or more is refused with its status, and one that answers less is measured', async () => {
  for (const status of [400, 404, 500]) {
    await assert.rejects(measure(`status${status}`), (error) => {
      assert.match(error.message, new RegExp(`\\b${status}\\b`), error.message);
      return true;
    }, `status ${status}`);
  }
  await assert.rejects(measure('no-such-page', { screen: { root: 'body' } }), /\b404\b/);
  const below = await measure('status399');
  assert.deepEqual(Object.keys(below.contexts), FOUR);
});

test('TYPE-HEADING-ORDER: a heading the same size as the one above it is not larger than it', async () => {
  const result = await measure('headingEqual');
  for (const c of FOUR) assert.deepEqual(wheres(result, c, 'TYPE-HEADING-ORDER'), [], c);
});

test('the keyboard walk reads 200 stops, and with a 201st it says the walk was cut short', async () => {
  const { MAX_STOPS } = await load();
  assert.equal(MAX_STOPS, 200);
  const cut = (result, c) => result.contexts[c].notes.filter((n) => n.where === 'keyboard walk');
  const all = await measure('walk200');
  const more = await measure('walk201');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.equal(all.contexts[c].keyboard.length, 200, c);
    assert.equal(all.contexts[c].keyboard.at(-1).where, 'button.b200');
    assert.deepEqual(cut(all, c), [], 'every stop was read, so nothing is said');
    assert.equal(more.contexts[c].keyboard.length, 200, c);
    assert.equal(cut(more, c).length, 1, JSON.stringify(more.contexts[c].notes));
    assert.equal(cut(more, c)[0].rule, 'A11Y-FOCUS');
    assert.match(cut(more, c)[0].detail, /cut short at 200 stops/);
  }
  for (const c of ['phone', 'narrow']) assert.deepEqual(cut(more, c), [], 'no walk, so no note');
});

// ---------- ALYDAS-223, Gate 8: what the review found ----------

const focusNotes = (result, c) => result.contexts[c].notes.filter((n) => n.rule === 'A11Y-FOCUS');

test('the keyboard walk starts at the top whatever has focus at load: the stops before an autofocused field are walked, and so is the field', async () => {
  const result = await measure('walkAutofocus');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(result.contexts[c].keyboard, [
      { where: 'button.first', name: 'first', indicator: 'outline' },
      { where: 'button.lost', name: 'no ring', indicator: 'none' },
      { where: 'input.auto', name: 'auto', indicator: 'outline' },
      { where: 'button.after', name: 'after', indicator: 'outline' },
    ]);
    assert.deepEqual(wheres(result, c, 'A11Y-FOCUS'), ['button.lost']);
    assert.deepEqual(focusNotes(result, c), []);
  }
});

test('the keyboard walk reads through an open shadow root: a stop inside is itself, with or without its ring, and the host is no stop', async () => {
  const result = await measure('walkShadow');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(result.contexts[c].keyboard, [
      { where: 'button.first', name: 'first', indicator: 'outline' },
      { where: 'button.ringless', name: 'no ring', indicator: 'none' },
      { where: 'button.ringed', name: 'ring', indicator: 'outline' },
      { where: 'button.last', name: 'last', indicator: 'outline' },
    ]);
    assert.deepEqual(wheres(result, c, 'A11Y-FOCUS'), ['button.ringless']);
    assert.deepEqual(focusNotes(result, c), []);
  }
});

test('the keyboard walk says what it cannot read: a frame, and an element that keeps focus, are notes and never failures, and the stop after them is walked', async () => {
  for (const [page, held] of [['walkFrame', 'iframe.pane'], ['walkClosed', 'x-box.sealed']]) {
    const result = await measure(page);
    for (const c of ['desktop-light', 'desktop-dark']) {
      assert.deepEqual(result.contexts[c].keyboard, [
        { where: 'button.first', name: 'first', indicator: 'outline' },
        { where: 'button.lost', name: 'no ring', indicator: 'none' },
      ], `${page} ${c}`);
      // The ringless button after it is the planted failure; the frame or host is not one.
      assert.deepEqual(wheres(result, c, 'A11Y-FOCUS'), ['button.lost'], `${page} ${c}`);
      assert.deepEqual(focusNotes(result, c).map((n) => n.where), [held], `${page} ${c}`);
      assert.match(focusNotes(result, c)[0].detail, /did not measure inside it/);
      assert.doesNotMatch(focusNotes(result, c)[0].detail, /ended/);
    }
    assert.deepEqual(focusNotes(result, 'phone'), [], 'no walk, so no note');
  }
});

test('a frame the walk cannot leave ends it, and the note says the stops after it were not read', async () => {
  const result = await measure('walkFrameTrap');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(result.contexts[c].keyboard, [{ where: 'button.first', name: 'first', indicator: 'outline' }]);
    assert.deepEqual(wheres(result, c, 'A11Y-FOCUS'), []);
    assert.deepEqual(focusNotes(result, c).map((n) => n.where), ['iframe.trap']);
    assert.match(focusNotes(result, c)[0].detail, /did not measure inside it.*the walk ended there.*not read/);
  }
});

test('COLOR-CONTRAST: text on a gradient or an image is a note and no failure; text on a colour, or on a card that covers the gradient, is measured', async () => {
  const result = await measure('contrastGround');
  for (const c of ['desktop-light', 'desktop-dark']) {
    // The planted failure sits on an opaque card: the gradient behind the card is not its ground.
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST'), ['p.oncard 16px'], c);
    assert.match(result.contexts[c].failures.find((f) => f.where === 'p.oncard 16px').detail, /^2\.85 to 1/);
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST', 'notes'), ['p.onhero 16px', 'p.onpic 16px'], c);
    for (const note of result.contexts[c].notes) assert.match(note.detail, /not measured.*image or gradient/);
  }
  assert.deepEqual(wheres(result, 'phone', 'COLOR-CONTRAST', 'notes'), [], 'no contrast read, so no note');
});

test('COLOR-CONTRAST: text in an SVG is measured by its fill, not its color, and a fill that is no colour is a note', async () => {
  const result = await measure('contrastSvg');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST'), ['text 20px'], c);
    assert.match(result.contexts[c].failures[0].detail, /^1\.36 to 1, rgb\(221, 221, 221\) on rgb\(255, 255, 255\)$/);
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST', 'notes'), ['text 24px'], c);
    assert.match(result.contexts[c].notes[0].detail, /not measured.*fill/);
  }
});

test('COLOR-CONTRAST: opacity on the text or above it fades the text colour, and the detail says so', async () => {
  const result = await measure('contrastOpacity');
  for (const c of ['desktop-light', 'desktop-dark']) {
    assert.deepEqual(wheres(result, c, 'COLOR-CONTRAST'), ['p.child 16px', 'p.faded 16px'], c);
    const [child, faded] = result.contexts[c].failures;
    assert.match(faded.detail, /^1\.61 to 1, rgb\(204, 204, 204\) on rgb\(255, 255, 255\), with opacity 0\.2 applied to the text$/);
    assert.match(child.detail, /with opacity 0\.3 applied to the text$/);
  }
});

test('LAYOUT-CLIP: a control or text cut by a clipping ancestor, sideways or downwards, is a note naming both and the edge, never a failure; what fits, an ellipsis, a scroller and hidden text are not noted', async () => {
  const result = await measure('clipCut');
  for (const c of ['phone', 'narrow']) {
    // A cut may be meant (a slide that peeks), so the reviewer judges it: nothing here fails.
    assert.deepEqual(wheres(result, c, 'LAYOUT-CLIP'), [], c);
    assert.deepEqual(wheres(result, c, 'LAYOUT-CLIP', 'notes'), ['button.buried', 'button.save', 'button.tall', 'div.direct', 'div.scroll', 'span.lost', 'span.total'], c);
    const detail = (where) => result.contexts[c].notes.find((f) => f.rule === 'LAYOUT-CLIP' && f.where === where).detail;
    assert.equal(detail('button.save'), 'cut by div.frame: 60px past its right edge');
    assert.match(detail('span.total'), /^cut by div\.box: \d+px past its right edge$/);
    assert.match(detail('div.direct'), /^cut by div\.direct: \d+px past its right edge$/);
    // A scroller with nothing to scroll brings nothing into view: what is in it is cut by the box outside it.
    assert.match(detail('span.lost'), /^cut by div\.outer: \d+px past its right edge$/);
    // Downwards: a control wholly under the fold of its box, and one half under it.
    assert.equal(detail('button.buried'), 'cut by div.low: 50px past its bottom edge');
    assert.equal(detail('button.tall'), 'cut by div.squat: 14px past its bottom edge');
    assert.match(detail('div.scroll'), /scrolls sideways/);
    assert.equal(result.contexts[c].scrollWidth, result.contexts[c].innerWidth);
  }
  assert.deepEqual(wheres(result, 'desktop-light', 'LAYOUT-CLIP', 'notes'), [], 'no clip read on the desktop');
});

test('LAYOUT-CLIP: a carousel track wider than the viewport inside a clipping frame is no failure, nor are its parked slides; a box past the edge beside it still is', async () => {
  const result = await measure('clipCarousel');
  for (const c of ['phone', 'narrow']) {
    assert.deepEqual(wheres(result, c, 'LAYOUT-CLIP'), ['div.left'], c);
    // The second slide peeks, on purpose or not: its caption is a note. The third is parked and is nothing.
    const notes = result.contexts[c].notes.filter((n) => n.rule === 'LAYOUT-CLIP');
    assert.deepEqual(notes.map((n) => n.where), ['span.cap'], c);
    assert.match(notes[0].detail, /^cut by div\.peekstrip: \d+px past its right edge$/);
    assert.equal(result.contexts[c].scrollWidth, result.contexts[c].innerWidth);
  }
});

test('a page that ends somewhere other than the address asked for is refused with both addresses, unless the caller allows the redirect', async () => {
  const refused = (url, ended, extra = {}) =>
    assert.rejects(measure('unused', { url, ...extra }), (error) => {
      assert.ok(error.message.includes(url), error.message);
      assert.ok(error.message.includes(ended), error.message);
      assert.match(error.message, /^ui-measure: /);
      assert.match(error.message, /no result is reported/);
      return true;
    }, url);
  await refused(`${base}/go-path`, `${base}/headingGood.html`);
  await refused(`${base}/go-port`, `${elsewhere}/headingGood.html`);
  // The reviewer's case: a sign-in page, and a screen file with no root to miss.
  await refused(`${base}/go-login`, `${base}/login.html`, { screen: { root: undefined } });
  // Controls: allowed, the page it ended at is measured; and a page that stays where it was asked for is, whatever its query.
  const allowed = await measure('unused', { url: `${base}/go-path`, allowRedirect: true });
  assert.deepEqual(Object.keys(allowed.contexts), FOUR);
  const stayed = await measure('unused', { url: `${base}/headingGood.html?tab=1#top` });
  assert.deepEqual(Object.keys(stayed.contexts), FOUR);
});

test('only http and https addresses are measured: a file address is refused, and says so', async () => {
  for (const url of [`file://${join(dir, 'headingGood.html')}`, 'about:blank', 'not an address']) {
    await assert.rejects(measure('unused', { url }), (error) => {
      assert.match(error.message, /^ui-measure: .*http/, error.message);
      assert.ok(error.message.includes(url), error.message);
      return true;
    }, url);
  }
});

test('a page that never settles is refused in the module\'s own words, with the address, the state, the context and the time', async () => {
  await assert.rejects(measure('poll', { settleTimeout: 1500 }), (error) => {
    assert.match(error.message, /^ui-measure: .*did not settle within 1500ms/, error.message);
    assert.ok(error.message.includes(`${base}/poll.html`), error.message);
    assert.match(error.message, /\bstatic\b/, error.message);
    assert.match(error.message, /\bdesktop-light\b/, error.message);
    assert.doesNotMatch(error.message, /page\.goto|Call log/, error.message);
    return true;
  });
  // Control: the same short wait is enough for a page that settles.
  const quiet = await measure('headingGood', { settleTimeout: 1500 });
  assert.deepEqual(Object.keys(quiet.contexts), FOUR);
});

test('a page cannot choose the text of a key: the tag and class are plain letters, digits, hyphen and underscore, and the class is cut at 80', async () => {
  const result = await measure('loudKeys');
  const bounded = `id${'a'.repeat(78)}`;
  assert.deepEqual(wheres(result, 'desktop-light', 'COLOR-CONTRAST'), ['p 16px', `p.${bounded} 16px`, 'x-id.low 16px']);
  assert.deepEqual(result.contexts['desktop-light'].keyboard.map((stop) => stop.where), [`button.${bounded}`]);
  assert.deepEqual(wheres(result, 'desktop-light', 'A11Y-FOCUS'), [`button.${bounded}`]);
  for (const c of FOUR) {
    for (const item of [...result.contexts[c].failures, ...result.contexts[c].notes, ...result.contexts[c].keyboard]) {
      assert.match(item.where, /^[a-z0-9_-]{1,80}(\.[A-Za-z0-9_-]{1,80})?( [\d.]+px)?$/, item.where);
    }
  }
  // Control: an ordinary class comes out whole.
  const plain = await measure('contrast');
  assert.deepEqual(wheres(plain, 'desktop-light', 'COLOR-CONTRAST'), ['p.low 16px']);
});

// The command, run as a person runs it. Never rejects: the exit code is the answer.
const cli = (...args) =>
  new Promise((resolve) => {
    execFile(process.execPath, [MODULE, ...args], (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr }));
  });
const screenFile = () => {
  const path = join(dir, 'screen.json');
  writeFileSync(path, JSON.stringify(SCREEN));
  return path;
};

test('--out is never written through a symbolic link: the command refuses, names it, and the file it points at is untouched', async () => {
  const target = join(dir, 'target.txt');
  const link = join(dir, 'linked.json');
  writeFileSync(target, 'keep me\n');
  symlinkSync(target, link);
  const refused = await cli('--url', `${base}/target.html`, '--screen', screenFile(), '--out', link);
  assert.equal(refused.code, 1);
  assert.match(refused.stderr, /^ui-measure: .*symbolic link/);
  assert.ok(refused.stderr.includes(link), refused.stderr);
  assert.equal(readFileSync(target, 'utf8'), 'keep me\n');
  assert.ok(lstatSync(link).isSymbolicLink());
  // Control: a plain file already at the name is replaced whole, and nothing else is left beside it.
  const plain = join(dir, 'plain.json');
  writeFileSync(plain, 'an older measurement, longer than nothing\n');
  const before = readdirSync(dir).sort();
  const written = await cli('--url', `${base}/target.html`, '--screen', screenFile(), '--out', plain);
  assert.equal(written.code, 0, written.stderr);
  assert.deepEqual(Object.keys(JSON.parse(readFileSync(plain, 'utf8')).contexts), FOUR);
  assert.ok(!lstatSync(plain).isSymbolicLink());
  assert.deepEqual(readdirSync(dir).sort(), before);
});

const VALUE_FLAGS = ['--url', '--screen', '--state', '--storage-state', '--out'];
const ALL_FLAGS = [...VALUE_FLAGS, '--allow-redirect'];

test('the command\'s flags: each that takes a value is refused without one, by name, and an unknown one is refused with all six named', async () => {
  const good = ['--url', `${base}/target.html`, '--screen', screenFile()];
  for (const flag of VALUE_FLAGS) {
    // At the end of the line, and with another flag where its value should be.
    for (const args of [[...good.filter((_, i) => good[i - (i % 2)] !== flag), flag], [flag, ...good.filter((_, i) => good[i - (i % 2)] !== flag)]]) {
      const refused = await cli(...args);
      assert.equal(refused.code, 1, `${flag}: ${args.join(' ')}`);
      assert.ok(refused.stderr.includes(`${flag} needs a value`), `${flag}: ${refused.stderr}`);
      assert.equal(refused.stdout, '');
    }
  }
  for (const args of [[...good, '--bogus', 'x'], [...good, '--allow-redirect', 'true'], ['--screen', screenFile()], ['--url', `${base}/target.html`]]) {
    const refused = await cli(...args);
    assert.equal(refused.code, 1, args.join(' '));
    for (const flag of ALL_FLAGS) assert.ok(refused.stderr.includes(flag), `${flag} is not in: ${refused.stderr}`);
  }
});

test('the command\'s flags: --storage-state signs the browser in, and --allow-redirect lets a redirect be measured', async () => {
  const state = join(dir, 'signed-in.json');
  const cookie = { name: 'session', value: 'held', domain: '127.0.0.1', path: '/', expires: -1, httpOnly: false, secure: false, sameSite: 'Lax' };
  writeFileSync(state, JSON.stringify({ cookies: [cookie], origins: [] }));
  const members = ['--url', `${base}/members`, '--screen', screenFile()];
  const out = await cli(...members);
  assert.equal(out.code, 1);
  assert.match(out.stderr, /\b401\b/);
  const signedIn = await cli(...members, '--storage-state', state);
  assert.equal(signedIn.code, 0, signedIn.stderr);
  assert.deepEqual(Object.keys(JSON.parse(signedIn.stdout).contexts), FOUR);

  const moved = ['--url', `${base}/go-path`, '--screen', screenFile()];
  const refused = await cli(...moved);
  assert.equal(refused.code, 1);
  assert.ok(refused.stderr.includes(`${base}/headingGood.html`), refused.stderr);
  const allowed = await cli(...moved, '--allow-redirect');
  assert.equal(allowed.code, 0, allowed.stderr);
  assert.deepEqual(Object.keys(JSON.parse(allowed.stdout).contexts), FOUR);
});

test('--state is held to lower-case letters, digits and hyphens, and the refusal says why', async () => {
  const good = ['--url', `${base}/target.html`, '--screen', screenFile()];
  for (const state of ['Finished', 'needs you', '../../etc/x', 'a_b', '-told', '']) {
    const refused = await cli(...good, '--state', state);
    assert.equal(refused.code, 1, state);
    assert.match(refused.stderr, /^ui-measure: --state .*lower-case letters, digits and hyphens.*file/, refused.stderr);
  }
  const taken = await cli(...good, '--state', 'finished-told-2');
  assert.equal(taken.code, 0, taken.stderr);
  assert.equal(JSON.parse(taken.stdout).state, 'finished-told-2');
});

test('a stop\'s name is the page\'s text with its control characters taken out, cut at 40', async () => {
  const result = await measure('loudName');
  const expected = `a[31mred${'x'.repeat(32)}`;
  assert.equal(expected.length, 40);
  assert.deepEqual(result.contexts['desktop-light'].keyboard.map((stop) => stop.name), [expected, 'calm']);
  // eslint-disable-next-line no-control-regex
  assert.doesNotMatch(JSON.stringify(result), /\\u00(1b|07|9b)/i);
});
