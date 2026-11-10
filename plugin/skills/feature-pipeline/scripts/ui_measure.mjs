// The UI gate's measuring module: one page, four browser contexts, each a new
// context and never a resized window.
//
//   measureScreen(browser, { url, storageState, screen, state, contexts,
//                            allowRedirect, settleTimeout })
//
// returns a plain object with no clock-dependent value in it, so two runs on
// one page serialise to the same bytes. Every read is confined to the screen
// file's `root` (body when absent): the shell around a screen is another
// screen's business. The root is one element, shown, with text in it, and the
// page answered below 400, at the http or https address it was asked for:
// anything else is refused, never measured as clean. What a read could not
// measure it says in a note, and never reports as a failure or passes over.
//
// This module imports nothing from Playwright. The caller hands it a browser.
// Run directly, it measures a page that needs no sign-in:
//
//   cd <project with @playwright/test> && node ui_measure.mjs --url <page> \
//     --screen <file.json> [--state <name>] [--storage-state <file>] [--out <file>] \
//     [--allow-redirect]

import { createRequire } from 'node:module';
import { lstatSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const RULES = [
  'COLOR-CONTRAST',
  'LAYOUT-CLIP',
  'A11Y-FOCUS',
  'TYPE-TRUNCATE',
  'TYPE-HEADING-ORDER',
  'A11Y-NAME',
  'A11Y-KEYBOARD',
  'UI-TRANSITION-ALL',
  'A11Y-TARGET',
];

// The viewport's width is the width the read is for: in every context the
// page must report exactly that. The flags say which reads belong to the
// context.
export const CONTEXTS = [
  { name: 'desktop-light', viewport: { width: 1280, height: 800 }, colorScheme: 'light', contrast: true, keyboard: true, placement: true },
  { name: 'desktop-dark', viewport: { width: 1280, height: 800 }, colorScheme: 'dark', contrast: true, keyboard: true },
  { name: 'phone', viewport: { width: 390, height: 844 }, colorScheme: 'light', deviceScaleFactor: 3, hasTouch: true, clip: true, touch: true, placement: true },
  { name: 'narrow', viewport: { width: 320, height: 700 }, colorScheme: 'light', deviceScaleFactor: 2, hasTouch: true, clip: true, touch: true },
];

// The numbers the rule book states (references/ui-rules.md), which
// test_ui_rules.py holds to these: text contrast, the least target side, and
// the side under which a target is a note on touch.
export const CONTRAST_MIN = 4.5;
export const TARGET_MIN = 24;
export const TOUCH_TARGET_MIN = 44;

// The most stops the keyboard walk reads. A page with more gets a note saying
// the walk was cut short.
export const MAX_STOPS = 200;

// How long a page has to settle (no request for half a second) before the
// read is refused, in milliseconds.
export const SETTLE_TIMEOUT = 30000;

// Runs in the page. Returns strings, numbers and booleans only.
function auditInPage({ rootSelector, blocks, reads, limits }) {
  // The root is one element: none is no screen, and of several only the first
  // would be read, whatever the others hold.
  const roots = document.querySelectorAll(rootSelector);
  if (roots.length === 0) return { badRoot: 'matches nothing' };
  if (roots.length > 1) return { badRoot: `matches ${roots.length} elements, and a root is one element` };
  const root = roots[0];

  // The page chooses its tag and class names, and a key is written into a
  // file that people and scripts read: so a key is letters, digits, hyphen and
  // underscore, each part cut at 80, and a page cannot put text of its own
  // choosing (a command, a thousand characters) into the output through it.
  const plain = (text) => text.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 80);
  const key = (el) => {
    const cls = typeof el.className === 'string' ? plain(el.className.trim().split(/\s+/)[0]) : '';
    return (plain(el.tagName.toLowerCase()) || 'element') + (cls ? '.' + cls : '');
  };
  const px = (n) => Math.round(n);
  const rendered = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const shown = (el) => {
    if (!rendered(el)) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  // A root nobody can see, or with no text drawn in it, has nothing to measure.
  const rootStyle = getComputedStyle(root);
  if (rootStyle.display === 'none' || rootStyle.visibility === 'hidden') return { badRoot: 'is hidden' };
  if (!(root.innerText ?? '').trim()) return { badRoot: 'holds no text that is drawn' };
  const rootBox = root.getBoundingClientRect();
  if (root.getClientRects().length === 0 || rootBox.width === 0 || rootBox.height === 0) return { badRoot: 'has a box with no size' };
  // Everything below reads from `inside` or from `root`, never from document.
  const inside = [root, ...root.querySelectorAll('*')];
  const visible = inside.filter(shown);
  const failures = [];
  const notes = [];

  // COLOR-CONTRAST: each rendered text node against the ground behind it.
  if (reads.contrast) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const rgba = (colour) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = colour;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3] / 255];
    };
    const over = (f, b) => [0, 1, 2].map((i) => f[i] * f[3] + b[i] * (1 - f[3])).concat(1);
    // The ground is whatever is painted behind, so this walk leaves the root.
    // An image or a gradient on the way to the first opaque colour is a ground
    // that is no one colour: null, and the caller says it was not measured.
    const groundOf = (el) => {
      const stack = [];
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
        const cs = getComputedStyle(e);
        if (cs.backgroundImage !== 'none') return null;
        const c = rgba(cs.backgroundColor);
        if (c[3] > 0) stack.push(c);
        if (c[3] >= 1) break;
      }
      let base = [255, 255, 255, 1];
      for (let i = stack.length - 1; i >= 0; i--) base = over(stack[i], base);
      return base;
    };
    // Opacity fades the text with everything else in its element: its own and
    // every ancestor's multiply.
    const opacityOf = (el) => {
      let opacity = 1;
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) opacity *= parseFloat(getComputedStyle(e).opacity);
      return opacity;
    };
    const lum = (c) => {
      const f = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
      return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
    };
    const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    const rgb = (c) => `rgb(${px(c[0])}, ${px(c[1])}, ${px(c[2])})`;
    const pairs = new Map();
    const unmeasured = new Map();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      const el = node.parentElement;
      if (!el || !shown(el)) continue;
      const cs = getComputedStyle(el);
      const where = `${key(el)} ${cs.fontSize}`;
      const ground = groundOf(el);
      if (ground === null) {
        unmeasured.set(where, 'contrast not measured: the text sits on an image or gradient, which is no one colour to measure against');
        continue;
      }
      // Text in an SVG is painted by its fill, and `color` says nothing of it.
      const inSvg = el.namespaceURI === 'http://www.w3.org/2000/svg';
      if (inSvg && !/^(rgb|rgba|color|hsl|hsla|lab|lch|oklab|oklch)\(/.test(cs.fill)) {
        unmeasured.set(where, 'contrast not measured: this SVG text has a fill that is no one colour');
        continue;
      }
      const paint = rgba(inSvg ? cs.fill : cs.color);
      const opacity = opacityOf(el);
      paint[3] *= opacity * (inSvg ? parseFloat(cs.fillOpacity) : 1);
      const ink = over(paint, ground);
      const value = Math.round(ratio(ink, ground) * 100) / 100;
      if (value >= limits.contrast) continue;
      if (!pairs.has(where) || pairs.get(where).value > value) {
        const faded = opacity < 1 ? `, with opacity ${Math.round(opacity * 100) / 100} applied to the text` : '';
        pairs.set(where, { value, detail: `${value} to 1, ${rgb(ink)} on ${rgb(ground)}${faded}` });
      }
    }
    for (const [where, { detail }] of pairs) failures.push({ rule: 'COLOR-CONTRAST', where, detail });
    for (const [where, detail] of unmeasured) notes.push({ rule: 'COLOR-CONTRAST', where, detail });
  }

  // LAYOUT-CLIP: content cut off, or causing sideways scroll. Two failures: the
  // document scrolls sideways; a box crosses the viewport's left or right edge
  // with no ancestor clipping it. And one note: text or a control reaches past
  // the box of an ancestor that clips it, sideways or downwards. That may be
  // lost content or a slide that peeks on purpose, and this read cannot tell
  // which, so the reviewer judges it.
  if (reads.clip) {
    // Visually hidden text: a clipped box of a pixel or less, or anything in one.
    const visuallyHidden = (el) => {
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
        const r = e.getBoundingClientRect();
        const cs = getComputedStyle(e);
        if (r.width <= 1.5 && r.height <= 1.5 && (cs.overflow !== 'visible' || cs.clip !== 'auto' || cs.clipPath !== 'none')) return true;
      }
      return false;
    };
    // Content in its own sideways scroller can be reached: a note for the
    // reviewer, who judges whether the scroller shows a cue, and no failure.
    const scrollers = inside.filter(
      (el) =>
        el !== document.body &&
        el !== document.documentElement &&
        rendered(el) &&
        /(auto|scroll)/.test(getComputedStyle(el).overflowX) &&
        el.scrollWidth > el.clientWidth + 1,
    );
    for (const el of scrollers) {
      notes.push({ rule: 'LAYOUT-CLIP', where: key(el), detail: `scrolls sideways: content ${el.scrollWidth} wide in a box of ${el.clientWidth}` });
    }
    const inScroller = (el) => scrollers.some((scroller) => scroller !== el && scroller.contains(el));
    const styles = new Map();
    const styleOf = (e) => {
      if (!styles.has(e)) {
        const cs = getComputedStyle(e);
        styles.set(e, {
          position: cs.position,
          transformed: cs.transform !== 'none',
          inline: cs.display === 'inline',
          clipsX: /^(hidden|clip)$/.test(cs.overflowX),
          clipsY: /^(hidden|clip)$/.test(cs.overflowY),
          scrollsY: /^(auto|scroll)$/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 1,
          ellipsis: cs.textOverflow === 'ellipsis',
        });
      }
      return styles.get(e);
    };
    // The ancestors inside the root whose boxes hold `el`, nearest first. A
    // positioned box is held, and so clipped, only by the ancestors that
    // contain it: an absolute one by a positioned or transformed ancestor and
    // those above it, a fixed one by a transformed ancestor.
    const holdersOf = (el) => {
      const found = [];
      if (el === root) return found;
      let position = styleOf(el).position;
      for (let e = el.parentElement; e; e = e.parentElement) {
        const style = styleOf(e);
        const holds = position === 'fixed' ? style.transformed : position === 'absolute' ? style.position !== 'static' || style.transformed : true;
        if (holds) {
          found.push(e);
          position = style.position;
        }
        if (e === root) break;
      }
      return found;
    };
    const controls = 'a[href],button,input:not([type=hidden]),select,textarea,summary';
    // What of `el` a viewer would see cut: a control's own box, or the box of
    // the text that is el's own. Anything else has nothing here to cut.
    const cuttable = (el, r) => {
      if (el.matches(controls)) return { box: r, text: false };
      let box = null;
      for (const node of el.childNodes) {
        if (node.nodeType !== 3 || !node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const t = range.getBoundingClientRect();
        if (t.width === 0) continue;
        box = box
          ? { left: Math.min(box.left, t.left), right: Math.max(box.right, t.right), top: Math.min(box.top, t.top), bottom: Math.max(box.bottom, t.bottom) }
          : { left: t.left, right: t.right, top: t.top, bottom: t.bottom };
      }
      return box ? { box, text: true } : null;
    };
    const worst = new Map();
    const cut = new Map();
    let reachesPastRight = false;
    for (const el of inside) {
      if (!rendered(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (visuallyHidden(el)) continue;
      const scrolledSideways = inScroller(el);
      const holders = holdersOf(el);
      const hiddenFromAll = el.closest('[aria-hidden="true"]') !== null;
      // Cut by an ancestor, or text by its own box. Sideways, wholly outside
      // the clip is parked (a carousel's other slides) and only a crossing is
      // noted; downwards, anything past the bottom edge is. A scroller that
      // scrolls that way between the two can bring it into view, and an
      // ellipsis is TYPE-TRUNCATE's to judge.
      const thing = hiddenFromAll ? null : cuttable(el, r);
      if (thing) {
        const { box, text } = thing;
        let sideways = !scrolledSideways;
        let downwards = true;
        for (const clipper of text ? [el, ...holders] : holders) {
          const style = styleOf(clipper);
          if (style.inline) continue;
          if (clipper !== el && style.scrollsY) downwards = false;
          if (!style.clipsX && !style.clipsY) continue;
          const c = clipper.getBoundingClientRect();
          const left = c.left + clipper.clientLeft;
          const right = left + clipper.clientWidth;
          const top = c.top + clipper.clientTop;
          const bottom = top + clipper.clientHeight;
          // Parked to one side, or scrolled away above: nothing of it is seen.
          if (style.clipsX && (box.right <= left + 1 || box.left >= right - 1)) break;
          if (style.clipsY && box.bottom <= top + 1) break;
          const past = {};
          if (downwards && style.clipsY && box.bottom - bottom > 1) past.bottom = box.bottom - bottom;
          const under = style.clipsY && box.top >= bottom - 1;
          if (sideways && style.clipsX && !under && !(text && style.ellipsis)) {
            if (left - box.left > 1) past.left = left - box.left;
            if (box.right - right > 1) past.right = box.right - right;
          }
          if (past.bottom !== undefined) downwards = false;
          if (past.left !== undefined || past.right !== undefined) sideways = false;
          if (Object.keys(past).length > 0) {
            // One note for each element key and ancestor key, with the most of each edge.
            const pair = `${key(el)}\n${key(clipper)}`;
            const seen = cut.get(pair) ?? {};
            for (const edge of Object.keys(past)) seen[edge] = Math.max(seen[edge] ?? 0, past[edge]);
            cut.set(pair, seen);
          }
          if (under || (!sideways && !downwards)) break;
        }
      }
      if (scrolledSideways) continue;
      // Past the viewport's edge inside an ancestor that clips it is the
      // carousel's track: nothing scrolls and the viewport cuts nothing.
      if (holders.some((holder) => styleOf(holder).clipsX && !styleOf(holder).inline)) continue;
      const right = r.right - innerWidth;
      const left = -r.left;
      if (right > 1) reachesPastRight = true;
      // Wholly off the viewport is parked, not clipped: only a crossing counts.
      if (r.right <= 0 || r.left >= innerWidth) continue;
      if (right <= 1 && left <= 1) continue;
      if (hiddenFromAll) continue;
      const past = Math.max(right, left);
      const where = key(el);
      if (!worst.has(where) || worst.get(where).past < past) {
        worst.set(where, { past, detail: `${px(past)}px past the ${right >= left ? 'right' : 'left'} edge of ${innerWidth}` });
      }
    }
    for (const [where, { detail }] of worst) failures.push({ rule: 'LAYOUT-CLIP', where, detail });
    for (const [pair, past] of cut) {
      const [where, clipper] = pair.split('\n');
      const edges = ['left', 'right', 'bottom'].filter((edge) => past[edge] !== undefined).map((edge) => `${px(past[edge])}px past its ${edge} edge`);
      notes.push({ rule: 'LAYOUT-CLIP', where, detail: `cut by ${clipper}: ${edges.join(' and ')}` });
    }
    // The document is what scrolls, so this one read looks past the root; when
    // nothing inside the root reaches past the right edge, it says so.
    const scrollWidth = document.scrollingElement.scrollWidth;
    if (scrollWidth > innerWidth) {
      failures.push({
        rule: 'LAYOUT-CLIP',
        where: reachesPastRight ? 'document' : 'document, outside the root',
        detail: `scrolls sideways: scrollWidth ${scrollWidth}, viewport ${innerWidth}`,
      });
    }
  }

  // TYPE-TRUNCATE: text cut by an ellipsis with no way to the rest.
  for (const el of visible) {
    if (getComputedStyle(el).textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1 && !el.title) {
      failures.push({ rule: 'TYPE-TRUNCATE', where: key(el), detail: 'text cut by an ellipsis, no title' });
    }
  }

  // TYPE-HEADING-ORDER: a level drawn larger than the nearest level above it.
  const largest = {};
  for (const h of visible) {
    if (!/^H[1-6]$/.test(h.tagName)) continue;
    const level = Number(h.tagName[1]);
    largest[level] = Math.max(largest[level] ?? 0, parseFloat(getComputedStyle(h).fontSize));
  }
  for (let level = 2; level <= 6; level++) {
    if (largest[level] === undefined) continue;
    let above = level - 1;
    while (above >= 1 && largest[above] === undefined) above--;
    if (above >= 1 && largest[level] > largest[above]) {
      failures.push({
        rule: 'TYPE-HEADING-ORDER',
        where: `h${level}`,
        detail: `h${level} at ${largest[level]}px is larger than h${above} at ${largest[above]}px`,
      });
    }
  }

  // A11Y-NAME: a field with no name. A placeholder is not a name. Buttons made
  // of an input are named by their value, so they are not fields here.
  const fields = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]),select,textarea';
  for (const el of inside) {
    if (!el.matches(fields) || !rendered(el)) continue;
    const named = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.labels?.length > 0 || el.closest('label');
    if (!named) failures.push({ rule: 'A11Y-NAME', where: key(el), detail: 'no aria-label, aria-labelledby or label' });
  }

  // A11Y-KEYBOARD: a pointer cursor of its own on something no key can reach.
  const focusable = 'a[href],button,input,select,textarea,summary,label,[contenteditable],[tabindex]:not([tabindex="-1"])';
  for (const el of visible) {
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer') continue;
    if (el.closest(focusable) || el.querySelector(focusable)) continue;
    failures.push({ rule: 'A11Y-KEYBOARD', where: key(el), detail: 'pointer cursor, not focusable' });
  }

  // UI-TRANSITION-ALL
  for (const el of visible) {
    const cs = getComputedStyle(el);
    const properties = cs.transitionProperty.split(',').map((p) => p.trim());
    const durations = cs.transitionDuration.split(',').map((d) => d.trim());
    const at = properties.findIndex((p, i) => p === 'all' && parseFloat(durations[i % durations.length]) > 0);
    if (at !== -1) failures.push({ rule: 'UI-TRANSITION-ALL', where: key(el), detail: `transition: all ${durations[at % durations.length]}` });
  }

  // A11Y-TARGET: under 24 fails everywhere; 24 to under 44 is a note on touch.
  // A link inside a sentence is exempt (WCAG 2.5.8): its block holds text of
  // its own beside the link. A link alone in its box is not.
  const blockOf = (el) => {
    let e = el;
    while (e && /^(inline|contents)$/.test(getComputedStyle(e).display)) e = e.parentElement;
    return e;
  };
  const inSentence = (link) => {
    const block = blockOf(link.parentElement);
    if (!block) return false;
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim() || node.parentElement.closest('a[href],button')) continue;
      if (blockOf(node.parentElement) === block) return true;
    }
    return false;
  };
  for (const el of visible) {
    if (!el.matches('a[href],button,input:not([type=hidden]),select,textarea,summary')) continue;
    if (el.matches('a[href]') && inSentence(el)) continue;
    const r = el.getBoundingClientRect();
    const least = Math.min(r.width, r.height);
    const size = `${px(r.width)}x${px(r.height)}`;
    if (least < limits.target) failures.push({ rule: 'A11Y-TARGET', where: key(el), detail: `${size}, under ${limits.target}px` });
    else if (reads.touch && least < limits.touchTarget) {
      notes.push({ rule: 'A11Y-TARGET', where: key(el), detail: `${size}, under ${limits.touchTarget}px on touch` });
    }
  }

  // Placement: where each declared block sits and how much room it takes.
  const placement = [];
  if (reads.placement) {
    const scrollers = [...document.body.querySelectorAll('*')].filter(
      (el) => /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 1,
    );
    const pageHeight = Math.max(document.scrollingElement.scrollHeight, ...scrollers.map((el) => el.scrollHeight), innerHeight);
    const replaced = 'img,svg,canvas,video,iframe,input,select,textarea,button';
    for (const block of blocks) {
      const head = { name: block.name, selector: block.selector, serves: block.serves ?? [] };
      const el = root.matches(block.selector) ? root : root.querySelector(block.selector);
      if (!el || !shown(el)) {
        placement.push({ ...head, absent: true });
        continue;
      }
      const r = el.getBoundingClientRect();
      const seenHeight = Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top));
      const seenWidth = Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left));
      // Content: the leaves that hold text or are drawn things, by their boxes.
      const leaves = [el, ...el.querySelectorAll('*')].filter(
        (x) => shown(x) && (x.matches(replaced) || (x.children.length === 0 && x.textContent.trim())) && (x !== el || x.children.length === 0),
      );
      const contentBottom = leaves.reduce((max, x) => Math.max(max, x === el ? r.top : x.getBoundingClientRect().bottom), r.top);
      const ownText = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
      let textBottom = r.top;
      for (const node of ownText) {
        const range = document.createRange();
        range.selectNodeContents(node);
        textBottom = Math.max(textBottom, range.getBoundingClientRect().bottom);
      }
      placement.push({
        ...head,
        top: px(r.top + scrollY),
        width: px(r.width),
        height: px(r.height),
        firstScreenShare: px((100 * seenHeight * seenWidth) / (innerHeight * innerWidth)),
        pageShare: px((100 * r.height * r.width) / (pageHeight * innerWidth)),
        fill: Math.min(100, px((100 * (Math.max(contentBottom, textBottom) - r.top)) / r.height)),
        onFirstScreen: r.top + scrollY < innerHeight,
      });
    }
  }

  return {
    innerWidth,
    innerHeight,
    scrollWidth: document.scrollingElement.scrollWidth,
    failures,
    notes,
    placement,
  };
}

// Before the first Tab: whatever has focus at load (an autofocus field), the
// walk starts from the top of the document. Blurring alone leaves the browser's
// starting point where the focus was, so the body is focused for a moment.
function startWalkInPage() {
  const body = document.body;
  const had = body.getAttribute('tabindex');
  body.setAttribute('tabindex', '-1');
  body.focus();
  if (had === null) body.removeAttribute('tabindex');
  else body.setAttribute('tabindex', had);
}

// One Tab press, read in the page: the walk's end, a stop, or something the
// walk cannot read inside (`held`: a frame, or an element focus did not leave).
// Transitions are run to their end first, so a ring that fades in is read as
// drawn.
function readStopInPage(rootSelector) {
  // The focused element is the one inside the shadow roots, not their host.
  let el = document.activeElement;
  while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { kind: 'end' };
  const gate = (window.__uiGate ??= { stops: [], held: [], last: null });
  const stayed = gate.last === el;
  gate.last = el;
  const root = document.querySelector(rootSelector);
  // `contains` stops at a shadow root, so the hosts are asked in turn.
  const inRoot = (x) => {
    for (let n = x; n; n = n.getRootNode().host) if (root?.contains(n)) return true;
    return false;
  };
  // The same key as the audit's, bounded for the same reason.
  const plain = (text) => text.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 80);
  const cls = typeof el.className === 'string' ? plain(el.className.trim().split(/\s+/)[0]) : '';
  const where = (plain(el.tagName.toLowerCase()) || 'element') + (cls ? '.' + cls : '');
  const frame = /^(IFRAME|FRAME|OBJECT|EMBED)$/.test(el.tagName);
  const at = gate.stops.findIndex((stop) => stop.el === el);
  // Focus is where the last Tab left it: what has focus is inside this
  // element, a frame's document or a closed shadow root, out of reach from here.
  if (stayed || frame) {
    const entered = !gate.held.includes(el);
    // A frame the walk has come round to again is the end of the walk.
    if (!entered && !stayed) return { kind: 'end' };
    if (entered) gate.held.push(el);
    return { kind: 'held', entered, where, inside: inRoot(el), stop: at };
  }
  if (at !== -1) return { kind: 'end' };
  const settle = (x) => {
    void getComputedStyle(x).outlineStyle;
    for (const animation of document.getAnimations()) {
      try {
        animation.finish();
      } catch {
        // An endless animation has no end to jump to.
      }
    }
  };
  const ring = (x) => {
    if (!x) return false;
    const cs = getComputedStyle(x);
    return cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 1 && cs.outlineColor !== 'rgba(0, 0, 0, 0)';
  };
  const paint = (x) => {
    if (!x) return '';
    const cs = getComputedStyle(x);
    return [cs.boxShadow, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor, cs.backgroundColor].join(' | ');
  };
  settle(el);
  const r = el.getBoundingClientRect();
  const unseen = el.matches('input') && (getComputedStyle(el).opacity === '0' || r.width <= 1.5 || r.height <= 1.5);
  const near = unseen ? [el, el.nextElementSibling, el.parentElement] : [el];
  gate.stops.push({ el, near, paint: near.map(paint).join(' || ') });
  return {
    kind: 'stop',
    inside: inRoot(el),
    where,
    // A checkbox's value is "on", which names nothing: its label does. The
    // name is the page's own text and stays so: it is cut at 40 and its
    // control characters are taken out (an escape sequence would reach the
    // terminal of whoever prints the file), and nothing more is promised of it.
    name: (el.getAttribute('aria-label') || el.textContent || el.labels?.[0]?.textContent || (el.matches('[type=checkbox],[type=radio]') ? '' : el.value) || '')
      .replace(/\s+/g, ' ')
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u001f\u007f-\u009f]/g, '')
      .trim()
      .slice(0, 40),
    outline: near.some(ring),
  };
}

// The same paint with nothing focused: a ring may be a shadow, not an outline.
function readUnfocusedInPage() {
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  focused?.blur();
  void getComputedStyle(document.body).outlineStyle;
  for (const animation of document.getAnimations()) {
    try {
      animation.finish();
    } catch {
      // As above.
    }
  }
  const paint = (x) => {
    if (!x) return '';
    const cs = getComputedStyle(x);
    return [cs.boxShadow, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor, cs.backgroundColor].join(' | ');
  };
  return (window.__uiGate?.stops ?? []).map((stop) => stop.paint !== stop.near.map(paint).join(' || '));
}

// The keyboard walk: Tab through the whole page from its top, keep the stops
// inside root. It reads MAX_STOPS stops. One Tab more says whether the page had
// another: when it had, the walk was cut short, and the caller is told.
//
// Where focus goes somewhere the walk cannot read (a frame's document, a closed
// shadow root), that element is no stop and no failure: it is `held`, a note.
// The walk presses on, and when MAX_STOPS presses do not bring focus back out,
// it ends there and the note says so.
async function keyboardWalk(page, rootSelector) {
  const stops = [];
  const held = [];
  let cutShort = false;
  let pressesInside = 0;
  await page.evaluate(startWalkInPage);
  for (;;) {
    await page.keyboard.press('Tab');
    const read = await page.evaluate(readStopInPage, rootSelector);
    if (read.kind === 'end') break;
    if (read.kind === 'held') {
      // A stop that turned out to keep focus was never a stop of its own.
      if (read.stop !== -1) stops[read.stop].held = true;
      if (read.entered) {
        pressesInside = 0;
        held.push({ where: read.where, inside: read.inside, ended: false });
      }
      if (++pressesInside > MAX_STOPS) {
        held.at(-1).ended = true;
        break;
      }
      continue;
    }
    if (stops.length === MAX_STOPS) {
      cutShort = true;
      break;
    }
    stops.push(read);
  }
  const changed = await page.evaluate(readUnfocusedInPage);
  return {
    cutShort,
    held,
    stops: stops
      .map((stop, i) => ({ ...stop, indicator: stop.outline ? 'outline' : changed[i] ? 'paint change' : 'none' }))
      .filter((stop) => stop.inside && !stop.held)
      .map(({ where, name, indicator }) => ({ where, name, indicator })),
  };
}

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const sorted = (list) => {
  const seen = new Map();
  for (const item of list) seen.set([item.rule, item.where, item.detail].join('\n'), item);
  return [...seen.keys()].sort(byText).map((k) => seen.get(k));
};

export async function measureScreen(
  browser,
  { url, storageState, screen, state = null, contexts = CONTEXTS, allowRedirect = false, settleTimeout = SETTLE_TIMEOUT },
) {
  // A file: address reads the disk with the stored session beside it, and no
  // screen is served from one.
  let address = null;
  try {
    address = new URL(url);
  } catch {
    // Not an address at all: refused below.
  }
  if (!address || !/^https?:$/.test(address.protocol)) {
    throw new Error(`ui-measure: only http and https addresses are measured, and ${url} is not one; no result is reported for this screen`);
  }
  const root = screen.root || 'body';
  const result = { screen: screen.screen ?? null, route: screen.route ?? null, state, root, contexts: {} };
  const standard = Object.fromEntries(CONTEXTS.map((c) => [c.name, c.viewport.width]));
  for (const spec of contexts) {
    // One of the four names is that context's width, so a caller's own list
    // cannot report a phone from a window of another size by calling it one.
    const asked = spec.viewport?.width;
    if (standard[spec.name] !== undefined && asked !== standard[spec.name]) {
      throw new Error(
        `ui-measure: the ${spec.name} read is ${standard[spec.name]} wide and was asked for at ${asked}; no result is reported for this screen`,
      );
    }
    const context = await browser.newContext({
      viewport: spec.viewport,
      colorScheme: spec.colorScheme ?? 'light',
      deviceScaleFactor: spec.deviceScaleFactor ?? 1,
      hasTouch: spec.hasTouch ?? false,
      storageState,
    });
    try {
      const page = await context.newPage();
      let response;
      try {
        response = await page.goto(url, { waitUntil: 'networkidle', timeout: settleTimeout });
      } catch (error) {
        if (error?.name !== 'TimeoutError') throw error;
        throw new Error(
          `ui-measure: ${url} did not settle within ${settleTimeout}ms (state ${state ?? 'not named'}, ${spec.name}): it loaded and kept the network busy, and a page still loading is not measured; no result is reported for this screen`,
        );
      }
      // A redirect, to a sign-in page for one, ends on another page than the
      // screen: measuring it would say nothing about the screen.
      const ended = new URL(page.url());
      if (!allowRedirect && (ended.origin !== address.origin || ended.pathname !== address.pathname)) {
        throw new Error(
          `ui-measure: asked for ${url} and the page ended at ${page.url()} (${spec.name}), ${ended.origin !== address.origin ? 'another origin' : 'another path'}; allowRedirect (--allow-redirect) measures the page it ended at; no result is reported for this screen`,
        );
      }
      // An error page is a page, and measuring it says nothing about the screen.
      if (response && response.status() >= 400) {
        throw new Error(`ui-measure: ${url} answered ${response.status()} (${spec.name}); no result is reported for this screen`);
      }
      await page.evaluate(() => document.fonts.ready.then(() => true));
      // A result from a window that is not the width asked for is not a
      // result, in any context and in either direction.
      const width = await page.evaluate(() => innerWidth);
      if (width !== asked) {
        throw new Error(
          `ui-measure: the ${spec.name} read was asked for at ${asked} wide and the page reads ${width}; no result is reported for this screen`,
        );
      }
      const audit = await page.evaluate(auditInPage, {
        rootSelector: root,
        blocks: screen.blocks ?? [],
        reads: { contrast: !!spec.contrast, clip: !!spec.clip, touch: !!spec.touch, placement: !!spec.placement },
        limits: { contrast: CONTRAST_MIN, target: TARGET_MIN, touchTarget: TOUCH_TARGET_MIN },
      });
      if (audit.badRoot) {
        throw new Error(`ui-measure: the root ${root} ${audit.badRoot}, at ${url} (${spec.name}); no result is reported for this screen`);
      }
      const walk = spec.keyboard ? await keyboardWalk(page, root) : { stops: [], held: [], cutShort: false };
      const keyboard = walk.stops;
      if (walk.cutShort) {
        audit.notes.push({
          rule: 'A11Y-FOCUS',
          where: 'keyboard walk',
          detail: `cut short at ${MAX_STOPS} stops: the page has more, and the stops after them were not read`,
        });
      }
      for (const { where, inside, ended } of walk.held) {
        // Outside the root it is another screen's, unless the walk ended there:
        // then stops inside the root may have gone unread.
        if (!inside && !ended) continue;
        audit.notes.push({
          rule: 'A11Y-FOCUS',
          where,
          detail: ended
            ? `the keyboard walk reached ${where}, did not measure inside it, and the walk ended there: ${MAX_STOPS} presses of Tab did not leave it, and the stops after it were not read`
            : `the keyboard walk reached ${where} and did not measure inside it: it is a frame, or it kept focus, and the stops in it were not read`,
        });
      }
      for (const stop of keyboard) {
        if (stop.indicator === 'none') {
          audit.failures.push({ rule: 'A11Y-FOCUS', where: stop.where, detail: `no focus indicator on "${stop.name}"` });
        }
      }
      result.contexts[spec.name] = {
        innerWidth: audit.innerWidth,
        innerHeight: audit.innerHeight,
        scrollWidth: audit.scrollWidth,
        failures: sorted(audit.failures),
        notes: sorted(audit.notes),
        keyboard,
        placement: audit.placement,
      };
    } finally {
      await context.close();
    }
  }
  return result;
}

const USAGE =
  'usage: node ui_measure.mjs --url <page> --screen <file.json> [--state <name>] [--storage-state <file>] [--out <file>] [--allow-redirect]';
const VALUE_FLAGS = ['--url', '--screen', '--state', '--storage-state', '--out'];

// Nothing is written through a symbolic link: a repository can plant one at
// the name a measurement is saved under, pointing at a file outside it.
function refuseLink(out) {
  let found = null;
  try {
    found = lstatSync(out);
  } catch {
    return;
  }
  if (found.isSymbolicLink()) throw new Error(`ui-measure: --out ${out} is a symbolic link, and nothing is written through one; nothing was written`);
}

async function main(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--allow-redirect') {
      flags['allow-redirect'] = true;
      continue;
    }
    if (!VALUE_FLAGS.includes(flag)) throw new Error(`ui-measure: ${flag} is not a flag of this command\n${USAGE}`);
    const value = argv[++i];
    if (value === undefined || value.startsWith('--')) throw new Error(`ui-measure: ${flag} needs a value\n${USAGE}`);
    flags[flag.slice(2)] = value;
  }
  if (!flags.url || !flags.screen) throw new Error(`ui-measure: --url and --screen are both needed\n${USAGE}`);
  if (flags.state !== undefined && !/^[a-z0-9][a-z0-9-]*$/.test(flags.state)) {
    throw new Error(
      `ui-measure: --state ${JSON.stringify(flags.state)} is not lower-case letters, digits and hyphens; a state is part of the name of the file its measurement is saved in, so it is held to that shape`,
    );
  }
  if (flags.out) refuseLink(flags.out);
  const screen = JSON.parse(readFileSync(flags.screen, 'utf8'));
  // Playwright is the project's own: resolved from where the command is run.
  const { chromium } = createRequire(join(process.cwd(), 'package.json'))('@playwright/test');
  const browser = await chromium.launch();
  try {
    const result = await measureScreen(browser, {
      url: flags.url,
      storageState: flags['storage-state'],
      screen,
      state: flags.state ?? null,
      allowRedirect: flags['allow-redirect'] === true,
    });
    const text = JSON.stringify(result, null, 2) + '\n';
    if (flags.out) {
      // Written beside the name and renamed over it: a rename replaces a link
      // planted since the check, and never writes through it.
      refuseLink(flags.out);
      const temporary = join(dirname(flags.out), `.${basename(flags.out)}.${process.pid}.tmp`);
      try {
        writeFileSync(temporary, text, { flag: 'wx' });
        renameSync(temporary, flags.out);
      } catch (error) {
        rmSync(temporary, { force: true });
        throw error;
      }
    } else process.stdout.write(text);
  } finally {
    await browser.close();
  }
}

const direct = (() => {
  try {
    return process.argv[1] !== undefined && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
})();
if (direct) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
