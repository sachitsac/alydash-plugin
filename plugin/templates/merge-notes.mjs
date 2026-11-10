// Written by `pnpm sync-plugin-templates` in the alydash repository, from
// packages/modules/work/src/merge-notes.ts. Do not edit it by hand: it is the desk's own
// reader of a pull request description, with its types taken out, and the next
// run of /alydash:setup replaces it.

// ALYDAS-134. The "What you'll notice" section of a pull request description,
// read the one way the sync and the CI check both read it, so the two cannot
// disagree about what a section is.
//
// The format is `.github/pull_request_template.md`'s (PR #32):
//
//   ## What you'll notice
//
//   _Written afterwards, 2026-09-25, from the pull request and its handoff._
//
//   **Headline:** Connect GitHub without ever opening a terminal
//   **What's new:** A Connections screen. ...
//   **How to try it:** Open Connections in the rail. ...
//   **What isn't done yet:** Production has no sealing key yet (ALYDAS-131).
//   **Button:** [Open Connections](/connections)
//   **Screenshot:** `docs/screenshots/2026-09-25-alydas-22-connections-added.png`
//   **By hand:** None.
//
// The section runs from its heading to the next `#` or `##` heading, or the end
// of the body. A part runs from its bold label to the next label, so a part may
// wrap onto several lines. Fenced code is skipped exactly as `shipsTrailer` in
// `github-sync.ts` skips it: a pull request that documents the section shows an
// example of it, and an example is not a section.
//
// Pure and synchronous: no hashing here, because the only hash available to a
// module that browser code also imports is `crypto.subtle`, which is
// asynchronous. The sync hashes `section`, the text this hands back.
//
// S2: every part, and `problems`, one plain sentence for each thing wrong with
// the section. The sync keeps whatever it can read, because a merge must never
// be lost to a badly written description; S5's CI check calls this same
// function and fails on any problem. Something unsafe is dropped rather than
// kept with a problem beside it: a button that is not a desk page never
// reaches the table.
// Kept only when its href is a desk page (`DESK_PATHS`); anything else is
// dropped and said in `problems`.
// What the Screenshot part said, when it said one of the three things it
// may say; null when it is missing or unreadable.
// The image paths, the same list `screenshot` carries when it is paths, and
// empty otherwise. Only png, jpg, jpeg and webp are kept.
// The sentence the screen shows where a screenshot would be, when the part
// is one of the two sentences rather than paths.
// ALYDAS-299. The By hand part's steps, in the order written. Empty for
// `None.`, and a malformed step is dropped with a sentence in `problems`.
// ALYDAS-299, S1. One step a pull request leaves for a person: done before
// merging, done after the deploy, or checked on production after the deploy.
// `text` is the step as written after its mark, and becomes the Ask's
// question. `key` is the item it lands on: the one its `(KEY criterion N)`
// names, or else the first key on the Ships line. `links` are the step's
// links, each one the Ask detail rule would draw: a desk page written
// `[label](/path)` (`desk`), or an https://github.com address.
//
// Gate 8 of ALYDAS-469, W-2 (CR-1). `place` is the step's place in the By hand
// part as written, from 1, the number this reader's own sentences use
// (`STEP_NO_MARK(n)`), so a line dropped above a step still counts and the
// desk names the step by the line a person counts to (design D1).
// `problems` is on both arms, so a caller that only wants to know what is
// wrong (S5's check) never has to look at `ok`. It is empty exactly when the
// section is complete and every part is well formed.
export const NO_SECTION_REASON = "The description has no \"What you'll notice\" section.";
export const MISSING_PART = (label)=>`The ${label} part is missing or empty.`;
export const BUTTON_NOT_A_LINK = 'The Button part is not a link written as [label](/path), so it was dropped.';
export const BUTTON_OFF_DESK = (href)=>`The Button goes to ${href}, which is not a page on the desk, so it was dropped.`;
export const SCREENSHOT_NOT_IMAGE = (path)=>`The screenshot ${path} is not a png, jpg, jpeg or webp file, so it was dropped.`;
export const SECOND_SECTION = "The description has a second \"What you'll notice\" section. Keep one, so the Timeline and this check read the same words.";
export const SCREENSHOT_NO_REASON = 'The Screenshot part says nothing visible changes but not why.';
export const NOT_TAKEN_ONLY_AFTERWARDS = 'Only an entry written afterwards may say no screenshot was taken at the time.';
export const SCREENSHOT_UNREADABLE = 'The Screenshot part names no image path in backticks and is neither "Nothing visible changes." with a reason nor "No screenshot was taken at the time."';
// ALYDAS-299, S1. A step is an Ask's question, so it is held to the question's
// limit (`QUESTION_MAX` in packages/core/src/asks.ts, pinned equal by
// merge-notes.test.ts: this file imports nothing), counted in code points as
// the Ask counts it.
export const STEP_MAX = 200;
export const STEPS_MAX = 12;
const MARKS = '"- Before merging:", "- After the deploy:" or "- After the deploy, check:"';
export const BY_HAND_MISSING = `The By hand part is missing or empty. Write **By hand:** None. when nothing is left to do by hand, or one line for each step, starting ${MARKS}.`;
export const STEP_NO_MARK = (n)=>`By hand step ${n} does not start with ${MARKS}, so it was dropped.`;
export const STEP_UNKNOWN_MARK = (n, mark)=>`By hand step ${n} is marked "${mark}", which is not one of ${MARKS}, so it was dropped.`;
export const STEP_EMPTY = (n)=>`By hand step ${n} has a mark and nothing after it, so it was dropped.`;
export const STEP_TOO_LONG = (n, length)=>`By hand step ${n} is ${length} characters, over the ${STEP_MAX} a step may be, so it was dropped. Put the rest in the For the reviewer section.`;
export const STEP_CONTROL = (n)=>`By hand step ${n} has a control character in it, so it was dropped. Keep a step to plain text.`;
export const STEPS_TOO_MANY = (count)=>`The By hand part has ${count} steps, more than the ${STEPS_MAX} a pull request may leave, so by hand step ${STEPS_MAX + 1} and any after it were dropped.`;
export const STEP_LINK_REFUSED = (n, href)=>`By hand step ${n} links to ${href}, which is neither a desk page written [label](/path) nor an https://github.com address, so the step was dropped.`;
export const STEP_KEY_NOT_SHIPPED = (n, key)=>`By hand step ${n} names ${key}, which is not on the Ships line, so it was dropped.`;
export const STEP_CRITERION_NOT_A_NUMBER = (n)=>`By hand step ${n} names criterion 0; a criterion is a whole number from 1, so the step was dropped.`;
// ALYDAS-299, Gate 8 (code #1). The largest criterion a step may name. A
// number past it would be written into the step's fingerprint as `1e+21` or
// rounded, so the step is dropped and said instead.
export const STEP_CRITERION_MAX = 999_999;
// ALYDAS-299, Gate 8 (W3). A `(KEY criterion N)` is read at the very end of a
// step, with nothing but punctuation after it; one anywhere else would pass
// as words and land the step on the wrong item.
export const STEP_CRITERION_NOT_AT_END = (n)=>`By hand step ${n} names an item and a criterion before its end. Write (KEY criterion N) once, as the step's last words, so the step lands on that item; it was dropped.`;
export const STEP_CRITERION_TOO_LARGE = (n, digits)=>`By hand step ${n} names criterion ${digits}; a criterion is a whole number from 1 to ${STEP_CRITERION_MAX}, so the step was dropped.`;
export const STEPS_NO_SHIPS = 'The By hand part has steps but the description has no Ships line, so no step has an item to land on and none was kept.';
// The desk pages a button may open, exactly. No query, no trailing slash, no
// other host: a button is a place on this desk, and a string that could be
// anything else is not kept. `/work/<KEY>` is an item, its key letters, a dash
// and digits.
//
// ALYDAS-303, R-V (modules 3). Two lists. Work's pages and the kernel's are
// on every desk Work is on. A page another module adds is on a desk only
// where that module is registered, and that module says so: it adds the page
// through its integration for Work (`workDeskPages`), and the Timeline reads
// its buttons against what was added (`timelineView`). A reader that cannot
// know which modules a desk has (the CI check, the plugin's copy of this
// file, the sync, which records what the pull request said) reads the second
// list whole, since a pull request may be for a desk that has the page.
const OWN_PATHS = [
    '/work',
    '/work/list',
    '/work/week',
    '/work/timeline',
    '/needs-you',
    '/needs-you/answered',
    '/apps',
    '/modules',
    '/connections',
    '/settings',
    // ALYDAS-216, S4 (D7). Settings' other two sections, each its own route.
    '/settings/account',
    '/settings/sign-in'
];
// ALYDAS-267. Every run, as a list: runs' page.
const ADDED_PATHS = [
    '/runs'
];
export const DESK_PATHS = [
    ...OWN_PATHS,
    ...ADDED_PATHS
];
const ITEM_PATH = /^\/work\/[A-Za-z]+-\d+$/;
// `added` is the pages the registered modules add to this desk, where the
// reader knows them (`deskPagesOf`); left out, every page a module may add.
export function isDeskPath(href, added = ADDED_PATHS) {
    return OWN_PATHS.includes(href) || ITEM_PATH.test(href) || added.includes(href);
}
// `**Label:** text` is what a part looks like, and the order below is the
// order problems are reported in.
const REQUIRED = [
    [
        'headline',
        'Headline'
    ],
    [
        'whatsNew',
        "What's new"
    ],
    [
        'tryIt',
        'How to try it'
    ],
    [
        'notDone',
        "What isn't done yet"
    ],
    [
        'screenshot',
        'Screenshot'
    ],
    [
        'byHand',
        'By hand'
    ]
];
const LINK = /^\[([^\]]+)\]\((\S+)\)$/;
// Gate 8, standards 9: exported, so the CI check, the screenshot use case and
// this parser read one pattern each rather than three copies.
export const IMAGE = /\.(?:png|jpe?g|webp)$/i;
const NOTHING_VISIBLE = /^Nothing visible changes\.\s*([\s\S]*)$/;
const NOT_TAKEN = 'No screenshot was taken at the time.';
// A `Ships:` line is the matcher's (`shipsTrailer` in `github-sync.ts`), never
// part of the section: a description that ends with its section often puts
// the trailer under it, and it must not become the end of the last part.
// Exported so the matcher reads the same pattern.
export const SHIPS_LINE = /^ {0,3}(?:[-*>#]+ *)?(?:\*\*|__)?ships(?:\*\*|__)? *:(?:\*\*|__)?(.*)$/i;
// An apostrophe may arrive straight or curly, because an editor that smartens
// quotes is an ordinary thing to have written the description in.
const Q = "['’]";
const HEADING = new RegExp(`^ {0,3}## +What you${Q}ll notice *$`, 'i');
// Gate 8, correctness 7: any ATX heading ends the section, `###` and deeper
// included, and so does a setext heading (a line of `=` or `-` directly under
// a line of text).
const ANY_HEADING = /^ {0,3}#{1,6}(?:\s|$)/;
const SETEXT_UNDERLINE = /^ {0,3}(?:=+|-+)\s*$/;
// A fence opens or closes; the lines between are an example, never text.
export const FENCE = /^ {0,3}(?:```|~~~)/;
const WRITTEN_AFTERWARDS = /^ {0,3}_Written afterwards\b.*_ *$/i;
const LABELS = [
    [
        'headline',
        /^headline$/i
    ],
    [
        'whatsNew',
        new RegExp(`^what${Q}s new$`, 'i')
    ],
    [
        'tryIt',
        /^how to try it$/i
    ],
    [
        'notDone',
        new RegExp(`^what isn${Q}t done yet$`, 'i')
    ],
    [
        'button',
        /^button$/i
    ],
    [
        'screenshot',
        /^screenshots?$/i
    ],
    [
        'byHand',
        /^by hand$/i
    ]
];
const LABEL_LINE = /^ {0,3}\*\*([^*]+?):\*\* *(.*)$/;
function partOf(label) {
    return LABELS.find(([, pattern])=>pattern.test(label.trim()))?.[0] ?? null;
}
export function readMergeNotes(body) {
    if (!body) return {
        ok: false,
        reason: NO_SECTION_REASON,
        problems: [
            NO_SECTION_REASON
        ]
    };
    const lines = body.split(/\r?\n/);
    let fenced = false;
    let start = -1;
    let end = lines.length;
    // Gate 8, correctness 8: a second section anywhere after the first is a
    // problem, so the check fails a description that says two different things;
    // the first is the one read.
    let second = false;
    for(let index = 0; index < lines.length; index += 1){
        const line = lines[index];
        if (FENCE.test(line)) {
            fenced = !fenced;
            continue;
        }
        if (fenced) continue;
        if (start === -1) {
            if (HEADING.test(line)) start = index;
        } else if (end === lines.length) {
            const previous = lines[index - 1] ?? '';
            if (ANY_HEADING.test(line)) end = index;
            else if (index - 1 > start && SETEXT_UNDERLINE.test(line) && previous.trim() !== '') end = index - 1;
            if (end !== lines.length && HEADING.test(line)) second = true;
        } else if (HEADING.test(line)) {
            second = true;
        }
    }
    if (start === -1) return {
        ok: false,
        reason: NO_SECTION_REASON,
        problems: [
            NO_SECTION_REASON
        ]
    };
    const within = lines.slice(start, end);
    const texts = new Map();
    let current = null;
    let writtenAfterwards = false;
    fenced = false;
    for (const line of within.slice(1)){
        if (FENCE.test(line)) {
            fenced = !fenced;
            continue;
        }
        if (fenced) continue;
        if (SHIPS_LINE.test(line)) {
            current = null;
            continue;
        }
        const labelled = LABEL_LINE.exec(line);
        const part = labelled ? partOf(labelled[1]) : null;
        if (labelled && part) {
            // The first of a repeated label wins, so a second Headline further down
            // cannot quietly replace the one a person reads first.
            current = texts.has(part) ? null : part;
            if (current) texts.set(current, [
                labelled[2]
            ]);
            continue;
        }
        if (current === null && WRITTEN_AFTERWARDS.test(line)) {
            writtenAfterwards = true;
            continue;
        }
        if (current) texts.get(current).push(line);
    }
    const text = (part)=>{
        const joined = (texts.get(part) ?? []).join('\n').trim();
        return joined === '' ? null : joined;
    };
    const problems = second ? [
        SECOND_SECTION
    ] : [];
    for (const [part, label] of REQUIRED){
        // ALYDAS-299: the one part whose sentence says what to write when there
        // is nothing to say, because `None.` is an answer and an empty part is not.
        if (text(part) === null) problems.push(part === 'byHand' ? BY_HAND_MISSING : MISSING_PART(label));
    }
    const button = readButton(text('button'), problems);
    const screenshot = readScreenshot(text('screenshot'), writtenAfterwards, problems);
    const steps = readSteps(text('byHand'), shipsKeys(body), problems);
    return {
        ok: true,
        section: within.join('\n').trim(),
        problems,
        notes: {
            headline: text('headline'),
            whatsNew: text('whatsNew'),
            tryIt: text('tryIt'),
            notDone: text('notDone'),
            button,
            screenshot,
            screenshots: screenshot?.kind === 'paths' ? screenshot.paths : [],
            screenshotNote: screenshot?.kind === 'nothing-visible' ? `Nothing visible changes. ${screenshot.reason}` : screenshot?.kind === 'not-taken' ? NOT_TAKEN : null,
            writtenAfterwards,
            steps
        }
    };
}
// Optional, so absent is no problem. Present, it is a link to a desk page or
// it is dropped with a sentence saying why.
function readButton(line, problems) {
    if (line === null) return null;
    const link = LINK.exec(line);
    if (!link) {
        problems.push(BUTTON_NOT_A_LINK);
        return null;
    }
    const label = link[1].trim();
    const href = link[2];
    if (label === '') {
        problems.push(BUTTON_NOT_A_LINK);
        return null;
    }
    if (!isDeskPath(href)) {
        problems.push(BUTTON_OFF_DESK(href));
        return null;
    }
    return {
        label,
        href
    };
}
// One of three things. A missing part is already reported with the others.
function readScreenshot(line, writtenAfterwards, problems) {
    if (line === null) return null;
    const nothing = NOTHING_VISIBLE.exec(line);
    if (nothing) {
        const reason = nothing[1].replace(/\s+/g, ' ').trim();
        if (reason === '') {
            problems.push(SCREENSHOT_NO_REASON);
            return null;
        }
        return {
            kind: 'nothing-visible',
            reason
        };
    }
    if (line === NOT_TAKEN) {
        if (!writtenAfterwards) {
            problems.push(NOT_TAKEN_ONLY_AFTERWARDS);
            return null;
        }
        return {
            kind: 'not-taken'
        };
    }
    const named = [
        ...line.matchAll(/`([^`\n]+)`/g)
    ].map((match)=>match[1].trim());
    if (named.length === 0) {
        problems.push(SCREENSHOT_UNREADABLE);
        return null;
    }
    const paths = [];
    for (const path of named){
        if (IMAGE.test(path)) paths.push(path);
        else problems.push(SCREENSHOT_NOT_IMAGE(path));
    }
    return paths.length > 0 ? {
        kind: 'paths',
        paths
    } : null;
}
// ALYDAS-299, S1. The By hand part, one step a line. A malformed step is
// dropped with its own sentence, so the sync never raises a step the check
// would have failed, and the steps beside it are still kept.
const NONE = /^none\.?$/i;
const BULLET = /^[-*+](?:\s+(.*))?$/;
// Indented, and not itself a bullet: a bullet is a step of its own.
const CONTINUATION = /^(?: {2,}|\t)(?![-*+](?:\s|$))\S/;
// The longer mark first, since "After the deploy" starts it.
const STEP_MARKS = [
    [
        'before',
        /^before merging *:(.*)$/i
    ],
    [
        'check',
        /^after the deploy, *check *:(.*)$/i
    ],
    [
        'after',
        /^after the deploy *:(.*)$/i
    ]
];
// Words and a colon, which a person meant as a mark.
const SOME_MARK = /^([A-Za-z][A-Za-z ,'’-]{0,39}?) *:(?:\s|$)/;
// The step's last words, with nothing after them but punctuation and space
// (Gate 8, W3: a full stop after the suffix is how a sentence ends). Any
// other `(KEY criterion N)` in the step is `NAMES_CRITERION_ANYWHERE`, and
// fails the check. `stepQuestion` in by-hand-steps.ts drops exactly this.
export const NAMES_CRITERION = /\(([A-Za-z]+-\d+) +criterion +(\d+)\)[.,;:!?]*\s*$/i;
const NAMES_CRITERION_ANYWHERE = /\([A-Za-z]+-\d+ +criterion +\d+\)/i;
// A link written in markdown, and then any address left in the words.
const STEP_LINK = /\[([^\]]*)\]\(([^()\s]*(?:\([^()\s]*)?)\)/g;
const ADDRESS = /\b[a-z][a-z0-9+.-]*:\/\/[^\s<>"'`]+/gi;
const STEP_CONTROL_CHARACTER = /[\u0000-\u0008\u000b-\u001f\u007f]/;
function readSteps(part, ships, problems) {
    if (part === null || NONE.test(part)) return [];
    // Gate 8 (W4): a line indented by two or more spaces, or a tab, under a
    // line before it is that line wrapped, and joins it with a space, unless it
    // is a bullet.
    const lines = [];
    for (const raw of part.split('\n')){
        const line = raw.trim();
        if (line === '') continue;
        if (CONTINUATION.test(raw) && lines.length > 0) lines[lines.length - 1] += ` ${line}`;
        else lines.push(line);
    }
    if (ships.length === 0) {
        problems.push(STEPS_NO_SHIPS);
        return [];
    }
    const steps = [];
    for (const [index, line] of lines.entries()){
        if (index >= STEPS_MAX) break;
        const step = readStep(index + 1, line, ships);
        if (typeof step === 'string') problems.push(step);
        else steps.push(step);
    }
    if (lines.length > STEPS_MAX) problems.push(STEPS_TOO_MANY(lines.length));
    return steps;
}
// The step, or the sentence that says why it was dropped.
function readStep(n, line, ships) {
    const bullet = BULLET.exec(line);
    if (!bullet) return STEP_NO_MARK(n);
    const words = (bullet[1] ?? '').trim();
    const marked = STEP_MARKS.map(([when, pattern])=>[
            when,
            pattern.exec(words)
        ]).find(([, match])=>match);
    if (!marked) {
        const mark = SOME_MARK.exec(words);
        return mark ? STEP_UNKNOWN_MARK(n, mark[1].trim()) : STEP_NO_MARK(n);
    }
    const [when, match] = marked;
    const text = match[1].trim();
    if (text === '') return STEP_EMPTY(n);
    const length = [
        ...text
    ].length;
    if (length > STEP_MAX) return STEP_TOO_LONG(n, length);
    if (STEP_CONTROL_CHARACTER.test(text)) return STEP_CONTROL(n);
    let key = ships[0];
    let criterion = null;
    const names = NAMES_CRITERION.exec(text);
    const before = names ? text.slice(0, names.index) : text;
    if (NAMES_CRITERION_ANYWHERE.test(before)) return STEP_CRITERION_NOT_AT_END(n);
    if (names) {
        const named = names[1].toUpperCase();
        if (!ships.includes(named)) return STEP_KEY_NOT_SHIPPED(n, named);
        criterion = Number(names[2]);
        if (criterion < 1) return STEP_CRITERION_NOT_A_NUMBER(n);
        if (criterion > STEP_CRITERION_MAX) return STEP_CRITERION_TOO_LARGE(n, names[2]);
        key = named;
    }
    const links = [];
    let rest = '';
    let at = 0;
    for (const link of text.matchAll(STEP_LINK)){
        rest += `${text.slice(at, link.index)} `;
        at = link.index + link[0].length;
        const label = link[1].trim();
        const href = link[2];
        if (label === '') return STEP_LINK_REFUSED(n, href);
        // A label that is itself an address could say one place and lead to
        // another, so it is refused whatever the link goes to.
        const shown = label.match(ADDRESS)?.[0];
        if (shown !== undefined) return STEP_LINK_REFUSED(n, withoutTail(shown));
        if (href.startsWith('/')) {
            if (!isDeskPath(href)) return STEP_LINK_REFUSED(n, href);
            links.push({
                label,
                href,
                desk: true
            });
            continue;
        }
        const github = githubAddress(href);
        if (github === null) return STEP_LINK_REFUSED(n, href);
        links.push({
            label,
            href: github,
            desk: false
        });
    }
    rest += text.slice(at);
    for (const found of rest.matchAll(ADDRESS)){
        const written = withoutTail(found[0]);
        const github = githubAddress(written);
        if (github === null) return STEP_LINK_REFUSED(n, written);
        links.push({
            label: github,
            href: github,
            desk: false
        });
    }
    return {
        when,
        place: n,
        text,
        key,
        criterion,
        links
    };
}
// The address as the Ask detail rule links it (`linkFor` in
// packages/shell/src/ask-text.tsx): https, the host github.com exactly, no
// port and no user info, with the authority read off the text as written
// before the forgiving parser sees it. Null for anything else.
function githubAddress(written) {
    if (!/^https:\/\/github\.com(?=[/?#]|$)/i.test(written)) return null;
    let url;
    try {
        url = new URL(written);
    } catch  {
        return null;
    }
    if (url.username !== '' || url.password !== '' || url.port !== '' || url.hostname !== 'github.com') return null;
    return url.href;
}
// The step's own punctuation is not the address's, read the way
// `askTextParts` (packages/shell/src/ask-text.tsx) reads a run: a full stop, a
// comma, and a bracket that closes one opened before the address began.
function withoutTail(candidate) {
    const count = (text, mark)=>text.split(mark).length - 1;
    let kept = candidate;
    for(;;){
        const last = kept.at(-1) ?? '';
        const closes = {
            ')': '(',
            ']': '[',
            '}': '{'
        }[last];
        if (last !== '' && '.,;:!?*'.includes(last)) kept = kept.slice(0, -1);
        else if (closes !== undefined && count(kept, closes) < count(kept, last)) kept = kept.slice(0, -1);
        else return kept;
    }
}
// ---------------------------------------------------------------------------
// The Ships trailer, moved here from `github-sync.ts` (Gate 8, standards N1)
// ---------------------------------------------------------------------------
// Whether a title or a branch names this item. The key as a whole word, case
// folded because branches are usually lower case: `ALYDAS-13` matches
// `alydas-13-github-readback` and `feat: read back (ALYDAS-13)`, and never
// `ALYDAS-130`.
export function mentionsIdentifier(text, key) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`(?:^|[^A-Za-z0-9])${escaped}(?![A-Za-z0-9])`, 'i').test(text);
}
// The keys a pull request declares it ships, read from `Ships:` lines in its
// description and nowhere else.
//
// ALYDAS-53. A wave of work is one branch and one pull request carrying a dozen
// items, so rules 2 and 3 below never fire for it: the branch is named after the
// wave and the title names no item. Reading the whole body instead would be
// worse than useless. PR #1's prose names seventeen keys and shipped eleven of
// them; the other six sit under "Deliberately left out, now ideas on the board"
// and a body-wide match would have marched all six to shipped. So only a line
// the author wrote for this purpose counts, and prose is ignored however many
// keys it mentions.
//
// One line, one declaration, anywhere in the body:
//
//   Ships: ALYDAS-38, ALYDAS-39, ALYDAS-13
//
// Leading markdown (`- `, `* `, `> `, `#`) and bold around the label are
// tolerated because people write lists, and several such lines are unioned. The
// keys themselves are matched by `mentionsIdentifier` on the joined text, which
// is why `**ALYDAS-38**`, backticks and "and" between keys all work already and
// `ALYDAS-130` still does not match `ALYDAS-13`. A trailer that wraps onto a
// second line declares only what is on the first: a line is the unit, so that
// the prose under a list item cannot be swept in.
//
// Code is not a declaration. A pull request that documents this convention
// shows an example of it, and the first one that did (PR #2, which introduced
// the rule) claimed to ship the three items in its own fenced sample. So a
// fenced block is skipped, and the label may be indented at most three spaces,
// which is CommonMark's own line between a paragraph and an indented code
// block. Explaining the convention and using it have to look different, or
// every pull request about the read-back ships whatever its examples name.
export function shipsTrailer(body) {
    if (!body) return null;
    const declared = [];
    let fenced = false;
    for (const line of body.split(/\r?\n/)){
        // A fence opens or closes; either way the line itself declares nothing.
        if (FENCE.test(line)) {
            fenced = !fenced;
            continue;
        }
        if (fenced) continue;
        const match = SHIPS_LINE.exec(line);
        if (match) declared.push(match[1]);
    }
    return declared.length > 0 ? declared.join(' ') : null;
}
// ALYDAS-299, S1. The keys the Ships lines declare, upper case, in the order
// written and once each: what a By hand step may name, and the first is where
// a step that names none lands.
export function shipsKeys(body) {
    const trailer = shipsTrailer(body);
    if (trailer === null) return [];
    const keys = [];
    for (const match of trailer.matchAll(/(?:^|[^A-Za-z0-9])([A-Za-z]+-\d+)(?![A-Za-z0-9])/g)){
        const key = match[1].toUpperCase();
        if (!keys.includes(key)) keys.push(key);
    }
    return keys;
}
// ---------------------------------------------------------------------------
// A screenshot's path (Gate 8, standards 9)
// ---------------------------------------------------------------------------
//
// One rule, read by the CI check (`scripts/src/merge-notes-check.ts`) and by
// the screenshot use case (`timeline-screenshots.ts`): a repository-relative
// image path. Not absolute on any system a path could have been written on,
// no `..` segment, and an image extension. The check turns each answer into
// its own sentence; the use case refuses anything but null.
//
// ALYDAS-135, S6: the first two answers are `repositoryPathProblem`, which the
// run file rule below shares, so the two cannot drift apart on what "inside the
// repository" means.
export function screenshotPathProblem(path) {
    const escapes = repositoryPathProblem(path);
    if (escapes !== null) return escapes;
    return IMAGE.test(path) ? null : 'not-image';
}
// What both rules ask: is the path somewhere other than inside the repository?
// Absolute on any system a path could have been written on (a leading slash or
// backslash, or a drive letter), or a `..` segment under either separator.
function repositoryPathProblem(path) {
    if (/^[\\/]/.test(path) || /^[A-Za-z]:/.test(path)) return 'absolute';
    if (path.split(/[\\/]/).includes('..')) return 'climbs-out';
    return null;
}
// ALYDAS-135, S6. A file a run's phase produced (`runFile`), which may be any
// kind of file: the use case decides what it can show. Stricter than the
// screenshot rule where the path goes to GitHub whole, in a contents URL: no
// backslash anywhere (GitHub's separator is `/`, and a backslash is a name
// character there that reads as a separator everywhere else), and no NUL.
export function runFilePathProblem(path) {
    if (path === '') return 'empty';
    const escapes = repositoryPathProblem(path);
    if (escapes !== null) return escapes;
    if (path.includes('\\')) return 'backslash';
    if (path.includes('\u0000')) return 'nul';
    return null;
}
// ALYDAS-135, S6. What a run file's extension says it is, or null for a file
// shown as a path only. Here, in the file with no imports, so the pane (a
// client component) decides what to offer by the rule `runFile` reads with.
export function runFileKind(path) {
    if (IMAGE.test(path)) return 'image';
    if (/\.md$/i.test(path)) return 'markdown';
    // S7: `.htm` is the same document as `.html`.
    if (/\.html?$/i.test(path)) return 'html';
    return null;
}
export function isScreenshotPath(path) {
    return screenshotPathProblem(path) === null;
}
