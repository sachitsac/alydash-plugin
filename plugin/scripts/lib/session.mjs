// ALYDAS-18, S4, design §3. What a Claude Code hook does about the desk, as
// two pure steps with no I/O, so every event can be read off a table:
//
//   decide(event, input, state, now, env) -> { beat, state }
//     whether this hook sends a heartbeat, and the body it sends;
//   applyReply(event, reply, state)       -> { output, state, followUp }
//     what the desk's answer makes the hook print, and whether an ended beat
//     must follow.
//
// `hook.mjs` is the shell around them: it reads stdin, the credential and the
// session file, posts, prints, and spawns the detached ended beat.
//
// | Hook             | Beat                             | Directive                                              |
// | SessionStart     | always, idle                     | message -> additionalContext; stop -> continue:false;  |
// |                  |                                  | a 401 -> systemMessage, once: the desk's sentence when |
// |                  |                                  | it says the switch is off, else the reconnect notice   |
// | UserPromptSubmit | 30 s since the last beat         | message -> additionalContext; stop -> continue:false   |
// | PostToolUse      | 30 s since the last beat         | the same                                               |
// | Stop             | 30 s since the last beat, or at  | message -> decision:block; stop -> systemMessage       |
// |                  | once if the last beat was not idle |                                                      |
// | SessionEnd       | always, ended: true              | nothing printed; a message is queued, then dropped     |
//
// Gate 8: SessionStart reads idle, since the session has not worked yet, and a
// Stop always tells the desk the session went idle (COR-2). A stop that lands
// on Stop is told to the person (COR-3).
//
// ALYDAS-262. Every beat but the ended one says `answers: true`, and the
// desk's reply then carries what a person answered to the Asks this session
// raised. An answer is shown where a message is: in front of the model, or as
// a Stop block, so a session that has just ended its turn carries on with it.
// A PostToolUse that sees its session raise an Ask through the desk's MCP
// server (`raise_ask`, under the MCP connection's own token) beats at once and
// names the Ask in `heard`, until a beat lands, so the desk knows whose it is.
//
// ALYDAS-263. Every beat but the ended one also says `idle`, true or false,
// so the desk knows when the session went idle; the status line's last word
// says the same to a person. No hook fires while a session waits, so the
// beats in between are the watcher's pulses (lib/watch.mjs).
//
// ALYDAS-266. A beat names a work item from ALYDASH_WORK_ITEM when that is
// set, and otherwise the first key the branch's name carries (`branchItems`).
// The hook does not know its app's prefix, so anything shaped like a key is a
// guess and the desk is the judge: a guess it refuses is dropped by the retry
// (`itemRetry`), remembered, and never sent again this session, and the next
// key on the branch, if there is one, is the next beat's guess. A session
// that ends up linked to nothing is told so once (`UNLINKED_NOTICE`), in one
// sentence whatever the reason.
//
// ALYDAS-264. Every beat names the Claude Code session it is (`session`, the
// hook's own `session_id`). A pipeline run started in the session names the
// same id from CLAUDE_CODE_SESSION_ID (`inSession`, pipeline.py), and the desk
// then treats the two runs as one: one card on Now, one Stop, one slot of the
// run cap. The id goes only to the desk this session already reports to.
//
// ALYDAS-265. Every beat but the ended one also says what the session is
// doing, cut here, before anything is put in a body or a state file: the
// first line of the last prompt (`promptLine`; never when
// ALYDASH_SEND_PROMPT turns it off, and never before the person has been told
// what is sent, `SENDS_NOTICE`), and the name of the tool it last used
// (`toolName`; never the tool's input or output). And every beat carries the
// session's tokens and estimated cost, counted from its transcript
// (lib/usage.mjs). A budget stop (`reason: 'budget'`) stops the turn as a
// desk Stop does and ends nothing: the run waits, over its budget, for a
// person to extend it on the desk, and every hook asks again until they do.
//
// ALYDAS-515, S1. The Stop and the ended beat also say what the session came
// to: the opening of its last reply (`replyLine`, under the prompt line's
// consent, cleared by the next prompt) and the commit, how far its branch is
// ahead of origin's default branch, and whether origin has the commit
// (lib/outcome.mjs). No other beat carries them, and none of them goes before
// the laptop is told of SENDS_VERSION 2 (Gate 8, W6).
//
// ALYDAS-524 (design D4). With them, `onDefault`: whether the session
// committed straight onto origin's default branch, against the HEAD the
// SessionStart recorded in the state (`startHead`), told-gated as the other
// git facts are.
//
// ALYDAS-523 (design D3). `diedBeat` is the ended beat the laptop runner
// sends for a session it started whose process exited with no SessionEnd.
//
// ALYDAS-526 c3 (design D6). With ALYDASH_SEND_PROMPT off, every beat but the
// ended one sends a blank prompt, and the Stop and the ended beat a blank
// reply, so a run resumed with prompts off clears what the desk showed.

import { restartUsage, usageFigures } from './usage.mjs';

// The events hooks/hooks.json registers, and the only ones hook.mjs answers.
export const HOOK_EVENTS = Object.freeze(['SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop', 'SessionEnd']);

export const AGENT = 'claude-code';
export const THROTTLE_MS = 30_000;
// HEARTBEAT_BOUNDS in packages/modules/runs/src/heartbeat-body.ts: run, statusLine and
// branch are 200 code points each. tests/plugin/session.test.ts pins these to
// runs' table.
export const STATUS_LINE_MAX = 200;
export const BRANCH_MAX = 200;

// ALYDAS-265. HEARTBEAT_BOUNDS' `prompt` and `tool`, pinned as the two above.
export const PROMPT_LINE_MAX = 120;
export const TOOL_MAX = 100;
// ALYDAS-515, S1 (design D1). The opening of the last reply, as long as the
// desk keeps it (HEARTBEAT_BOUNDS' `reply`, slice S2).
export const REPLY_LINE_MAX = 280;
// A tool's name as Claude Code and MCP servers spell one. Anything else is
// not a name, and is not sent.
const TOOL_NAME = /^[A-Za-z0-9_.:-]+$/;
// The values of ALYDASH_SEND_PROMPT that turn prompt sending off.
const OFF = new Set(['0', 'false', 'off', 'no']);
// What a connected session sends, in the words `/alydash:connect` prints
// (connect.mjs) and the desk's approval page shows (`WHAT_IS_SENT` in
// apps/desk/app/device/device-form.tsx; tests/plugin/connect.test.ts pins the
// two together).
export const WHAT_IS_SENT =
  `A connected session sends the desk its branch, the first line of each prompt you type (up to ${PROMPT_LINE_MAX} characters), the opening of its last reply (up to ${REPLY_LINE_MAX} characters), the name of the tool it last used, its last commit, how many commits its branch has that the default branch on origin does not, and whether origin has that commit, and its token count and estimated cost. Nothing else you type, and nothing a tool reads or writes, is sent. Set ALYDASH_SEND_PROMPT=0 to send no prompt or reply text.`;
// Said once on a laptop, by the first hook that can show a person a line,
// since a laptop connected before this version never saw the connect step
// say it. Until it has been said, no prompt line is sent.
export const SENDS_NOTICE = `alydash now shows more of a session on its run page. ${WHAT_IS_SENT}`;
// The version of what is sent that a laptop has been told of
// (`told.json` in the plugin's data folder). Raised when a beat sends more:
// 2 for ALYDAS-515's reply opening and git facts.
export const SENDS_VERSION = 2;

export const STOP_REASON = 'Stopped from the desk.';
export const CAP_NOTICE =
  'The desk is already showing twenty sessions from this laptop, so this one will not appear on Now.';
export const MESSAGE_PREFIX = 'A message from the desk: ';
export const ANSWER_PREFIX = 'An answer from the desk. You asked: ';
// HEARD_MAX and HEARD_ID in packages/modules/runs/src/heartbeat-body.ts;
// tests/plugin/session.test.ts pins both to runs'.
export const HEARD_MAX = 10;
const ASK_ID = '[A-Za-z0-9_-]{1,100}';
export const HEARD_ID = new RegExp(`^${ASK_ID}$`);
// ALYDAS-264. SESSION_KEY in packages/modules/runs/src/heartbeat-body.ts, pinned as the
// two above are.
export const SESSION_KEY = /^[A-Za-z0-9_-]{1,128}$/;
// The desk's MCP tool that raises an Ask, under whatever name the server was
// added with, and the Ask's id in what it answered: `{"ask":{"id":"..."`.
const RAISE_TOOL = /^mcp__.+__raise_ask$/;
const RAISED_ID = new RegExp(`"ask"\\s*:\\s*\\{\\s*"id"\\s*:\\s*"(${ASK_ID})"`);
// Gate 8, COR-7: a 401 at SessionStart, or an expired credential skipped there.
export const DISCONNECTED_NOTICE = "The desk no longer accepts this laptop's connection. Run /alydash:connect again.";
// ALYDAS-342. A 401 is also what the desk answers a connection it still
// holds while Agent access is switched off for the app, and for that one
// "connect again" is wrong: nothing was ended, and the same credential works
// when the switch goes back on. The desk marks that answer with a word beside
// its sentence, `"reason": "switched_off"`, and the sentence says what
// happened and where the switch is. So the plugin reads the word, and says
// the desk's sentence after the few words below: it holds no copy of the
// sentence and never reads its text. A desk from before the word answers
// without it, and is told the reconnect notice as it always was.
export const SWITCHED_OFF_REASON = 'switched_off';
export const REFUSED_PREFIX = 'The desk is not showing this session: ';
// As much of the desk's sentence as is said. Its own is under 300 characters
// with the longest app name a desk takes.
export const REFUSED_SENTENCE_MAX = 600;

// ALYDAS-449. The word the desk puts beside its 403 to a beat for an app runs
// is switched off for (runs' heartbeat.ts). The plugin reads the word, never
// the sentence, and says one of its own two lines, once each way: to the
// person, as CAP_NOTICE is, since these are the plugin's words and not the
// desk's message to the model.
export const RUNS_OFF_REASON = 'runs_off';
export const runsOffNotice = (app) => `Runs is off for ${app}: this session no longer shows on Now.`;
export const runsOnNotice = (app) => `Runs is on again for ${app}: this session shows on Now.`;
// Gate 8 (W4). A desk without runs takes no beat, so the session start's probe
// is what hears agent access switched off for the app (its 403); said once,
// in the words the desk's refusals use for where the switch is.
export const accessOffNotice = (app) =>
  `Agent access is switched off for ${appNameOf({ appName: app })} on the desk, so this connection does nothing for now. Switch it back on for ${appNameOf({ appName: app })} in Modules, and it works again.`;
// The app a line names, from the credential hook.mjs beat with.
const APP_NAME_MAX = 100;
const appNameOf = (context) => {
  const name = typeof context?.appName === 'string' ? cleanMessage(context.appName) : null;
  return name === null ? 'this app' : cut(name.trim(), APP_NAME_MAX);
};

// ALYDAS-449. A 403 that says runs is switched off for the app.
export function isRunsOffRefusal(reply) {
  return reply?.status === 403 && reply.json?.reason === RUNS_OFF_REASON;
}

// Gate 8, SEC-2. What the card never draws must not reach the model as the
// desk speaking: format characters (\p{Cf}: zero-width, bidi controls, the tag
// block U+E0000 to U+E007F among them), variation selectors (U+FE00 to U+FE0F,
// U+E0100 to U+E01EF) and the braille blank U+2800.
const INVISIBLE = /[\p{Cf}\u{E0000}-\u{E007F}\u{FE00}-\u{FE0F}\u{E0100}-\u{E01EF}\u2800]/gu;

// A desk message with those removed, or null when nothing printable is left.
export function cleanMessage(text) {
  const clean = String(text).replace(INVISIBLE, '');
  return clean.trim() === '' ? null : clean;
}

// ALYDAS-342. The desk's sentence from a 401 that says Agent access is
// switched off, cleaned as a desk message is and cut to length, or null: for
// any other word, no word, or no sentence to say.
export function switchedOffSentence(reply) {
  if (reply?.status !== 401 || reply.json?.reason !== SWITCHED_OFF_REASON) return null;
  if (typeof reply.json.error !== 'string') return null;
  const sentence = cleanMessage(reply.json.error);
  return sentence === null ? null : [...sentence.trim()].slice(0, REFUSED_SENTENCE_MAX).join('');
}

// ALYDAS-265. Whether ALYDASH_SEND_PROMPT leaves prompt sending on. Unset or
// anything else is on.
export function sendsPrompt(env) {
  return !OFF.has(String(env?.ALYDASH_SEND_PROMPT ?? '').trim().toLowerCase());
}

// The first line of a prompt, as a beat carries it: everything before the
// first line break or other control character (a tab reads as a space), the
// characters a card never draws removed, the ends trimmed, cut to
// PROMPT_LINE_MAX code points. Blank when the first line is: a later line is
// never read in its place.
export function promptLine(prompt) {
  if (typeof prompt !== 'string') return '';
  return firstLine(prompt, PROMPT_LINE_MAX);
}

// ALYDAS-544. Whether a prompt is a system wake and not something a person
// typed: trimmed, it opens with `<task-notification>` (a background task
// finished), or it is only `<system-reminder>` blocks and whitespace. A typed
// prompt that quotes either tag later in its text is a prompt. The desk has
// only the first line, so its rule is runs' `isWakeLine` (system-wake.ts);
// tests/plugin/session.test.ts holds the two to each other.
const REMINDER_BLOCK = /<system-reminder>[\s\S]*?<\/system-reminder>/g;

export function isSystemWake(prompt) {
  if (typeof prompt !== 'string') return false;
  // Invisible characters first, as promptLine strips them (Gate 6).
  const text = prompt.replace(INVISIBLE, '').trim();
  if (text.startsWith('<task-notification>')) return true;
  return text.startsWith('<system-reminder>') && text.replace(REMINDER_BLOCK, '').trim() === '';
}

// What promptLine keeps of a text: everything before the first control
// character or line or paragraph separator, tabs as spaces, invisible
// characters removed, trimmed, cut to `max` code points.
function firstLine(text, max) {
  const first = text.replaceAll('\t', ' ').split(/[\p{Cc}\u2028\u2029]/u)[0];
  return cut(first.replace(INVISIBLE, '').trim(), max).trim();
}

// ALYDAS-515, S1 (design D1). The opening of a reply, as a beat carries it:
// its first paragraph (the text before the first blank line), its line breaks
// folded to spaces, then cut as promptLine cuts, so any other control
// character ends it, at REPLY_LINE_MAX code points. Null when nothing
// printable is left, or it is not text.
export function replyLine(text) {
  if (typeof text !== 'string') return null;
  const paragraph = text.replace(/^\s+/u, '').split(/\r?\n[ \t]*\r?\n/u)[0];
  const line = firstLine(paragraph.replace(/[ \t]*\r?\n[ \t]*/gu, ' '), REPLY_LINE_MAX);
  return line === '' ? null : line;
}

// The name of the tool a PostToolUse reports, or null when it names none.
export function toolName(input) {
  const name = input?.tool_name;
  return typeof name === 'string' && TOOL_NAME.test(name) ? cut(name, TOOL_MAX) : null;
}

// "$5.12", or null for what is not an amount.
function dollars(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `$${value.toFixed(2)}` : null;
}

// What a session over its budget is told, with the stop's own amounts when
// the desk sent them.
export function budgetStopReason(directive) {
  const spent = dollars(directive?.spentUsd);
  const budget = dollars(directive?.budgetUsd);
  const amounts = spent !== null && budget !== null ? `: about ${spent} of ${budget}` : '';
  return `This session has spent past its budget on the desk${amounts}. The desk has asked whether to extend it. Approve there to carry on; until then each turn is stopped.`;
}

// The id of the Ask a PostToolUse saw this session raise, or null. The tool's
// answer is read as text whatever it is wrapped in (an object, a list of
// content blocks, or JSON inside a string, whose quotes arrive escaped).
export function heardAsk(input) {
  if (typeof input?.tool_name !== 'string' || !RAISE_TOOL.test(input.tool_name)) return null;
  const response = input.tool_response;
  if (response === undefined || response === null) return null;
  let text = typeof response === 'string' ? response : JSON.stringify(response);
  for (let pass = 0; pass < 3; pass += 1) {
    const found = RAISED_ID.exec(text);
    if (found) return found[1];
    text = text.replaceAll('\\"', '"').replaceAll('\\\\', '\\');
  }
  return null;
}

// An answer as the session is told it: the question, the choice in words and
// by its id, and the note when there is one. Null when the desk's entry is
// not an answer, or nothing printable is left of it.
export function answerLine(answer) {
  if (typeof answer?.question !== 'string' || typeof answer?.answer !== 'string') return null;
  const question = cleanMessage(answer.question);
  const choice = cleanMessage(answer.answer);
  if (question === null || choice === null) return null;
  const label = typeof answer.answerLabel === 'string' ? cleanMessage(answer.answerLabel) : null;
  const note = typeof answer.note === 'string' ? cleanMessage(answer.note) : null;
  const said = label === null || label === choice ? choice : `${label} (choice id: ${choice})`;
  return `${ANSWER_PREFIX}${question}\nThe answer: ${said}${note === null ? '' : `\nTheir note: ${note}`}`;
}

// The hooks that can put text in front of the model.
const SHOWS_CONTEXT = new Set(['SessionStart', 'UserPromptSubmit', 'PostToolUse']);
// The hooks whose `continue: false` stops the turn (probe Q1, and
// SessionStart). At Stop the turn is already stopping, so a systemMessage
// tells the person instead.
const STOPS_TURN = new Set(['SessionStart', 'UserPromptSubmit', 'PostToolUse']);
const ALWAYS_BEATS = new Set(['SessionStart', 'SessionEnd']);

// ALYDAS-266. A key in a branch's name: an app's prefix in either case, a
// dash and the item's number, standing clear of letters and digits on both
// sides, so `release-2026` (seven letters) and `v2-10` name none. The bounds
// are core's IDENTIFIER_PATTERN (packages/core/src/identifier.ts): a prefix of
// two to six letters, a number with no leading zero that JavaScript counts
// exactly. tests/plugin/session.test.ts pins it to core's `parseIdentifier`.
const BRANCH_KEY = /(?<![A-Za-z0-9])([A-Za-z]{2,6})-([1-9]\d{0,14})(?![A-Za-z0-9])/g;
// How many of a branch's keys are ever tried, and how many refused guesses a
// session remembers.
export const BRANCH_KEYS_MAX = 3;
const GUESSES_MAX = 10;
export const UNLINKED_NOTICE =
  "This session is not linked to a work item: its branch names none this desk could link. Put the item's key in the branch name, or set ALYDASH_WORK_ITEM.";

// The keys a branch's name carries, as the desk spells them (`ALYDAS-266`), in
// the order they stand, each once.
export function branchItems(branch) {
  const keys = [];
  for (const found of String(branch ?? '').matchAll(BRANCH_KEY)) {
    const key = `${found[1].toUpperCase()}-${found[2]}`;
    if (!keys.includes(key)) keys.push(key);
    if (keys.length === BRANCH_KEYS_MAX) break;
  }
  return keys;
}

export function itemRefusedNotice(item) {
  return `The desk could not link this session to ${[...item].slice(0, 40).join('')}, so it shows on Now without a work item. Check ALYDASH_WORK_ITEM.`;
}

const codePoints = (text) => [...text];
const cut = (text, max) => codePoints(text).slice(0, max).join('');

// `cc-<first 8 of session_id>-<start ms in base 36>`: `cc-3f9a2b1c-mulqnh3z`.
export function runName(sessionId, startMs) {
  return `cc-${codePoints(String(sessionId)).slice(0, 8).join('')}-${Math.floor(startMs).toString(36)}`;
}

// `Claude Code · <branch> · working`, with the branch cut so the line stays
// within STATUS_LINE_MAX. No branch (a repository with no commit yet) leaves
// its part out.
export function statusLine(branch, idle) {
  const head = 'Claude Code · ';
  const tail = ` · ${idle ? 'idle' : 'working'}`;
  if (!branch) return `Claude Code${tail}`;
  const room = STATUS_LINE_MAX - codePoints(head).length - codePoints(tail).length;
  return `${head}${cut(branch, room)}${tail}`;
}

function freshState(sessionId, now) {
  return {
    sessionId,
    run: runName(sessionId, now),
    startedAt: now,
    lastBeatAt: null,
    idle: false,
    stopped: false,
    capped: false,
    itemRefused: false,
    pending: [],
    answers: [],
    heard: [],
    last: null,
    prompt: null,
    reply: null,
    tool: '',
    usage: null,
    held: false,
    // ALYDAS-524. The commit HEAD was at when the run's session started.
    startHead: null,
    // Gate 8 (G8W2). Origin's default tip as the laptop knew it then.
    startOrigin: null,
    // ALYDAS-523. Whether the SessionEnd hook ran, so the runner sends no
    // ended beat of its own.
    ended: false,
  };
}

// What a beat names, and on whose word: `{ by, key, spare }`. The variable
// wins whenever it is set, refused or not: a refused variable does not fall
// back to the branch. `spare` is whether the branch has another key to try
// should the desk refuse this one.
//
// ALYDAS-451, F11. A desk without Work (`work: false`, hook.mjs reading
// hasWork off the entry) has no item to link, so nothing is named, from the
// variable or the branch, and `by` is neither: the session is never told it
// is linked to no work item.
function linkOf(env, state, branch, work) {
  if (work === false) return { by: 'none', key: null, spare: false };
  const named = String(env?.ALYDASH_WORK_ITEM ?? '').trim();
  if (named !== '') return { by: 'variable', key: state.itemRefused ? null : named, spare: false };
  const open = branchItems(branch).filter((key) => !(state.guessRefused ?? []).includes(key));
  return { by: 'branch', key: open[0] ?? null, spare: open.length > 1 };
}

function bodyOf(state, branch, idle, link, ended, outcome) {
  return withOutcome(withDoing(bodyBefore(state, branch, idle, link, ended), state, ended), outcome);
}

// ALYDAS-515, S1 (design D1, D2). What a session that stopped came to, on
// the Stop and the ended beat only (`outcome` is null on every other): the
// opening of its last reply, when it may be sent (`state.reply`, null
// otherwise), and the three git facts. A commit that is not one is left out,
// as the desk keeps the run's when a beat names none; `ahead` and `pushed`
// go as null when they are not known.
const COMMIT = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
function withOutcome(body, outcome) {
  if (outcome === null) return body;
  if (typeof outcome.reply === 'string') body.reply = outcome.reply;
  const git = outcome.git;
  if (git !== null && typeof git === 'object') {
    if (typeof git.commit === 'string' && COMMIT.test(git.commit)) body.commit = git.commit;
    body.ahead = Number.isInteger(git.ahead) && git.ahead >= 0 ? git.ahead : null;
    body.pushed = typeof git.pushed === 'boolean' ? git.pushed : null;
    // ALYDAS-524. Null when it is not known.
    body.onDefault = typeof git.onDefault === 'boolean' ? git.onDefault : null;
  }
  return body;
}

// What a Stop last told the desk it came to, so a Stop with something new
// beats whatever the throttle.
const outcomeKey = (outcome) =>
  JSON.stringify([outcome.reply ?? null, outcome.git?.commit ?? null, outcome.git?.pushed ?? null, outcome.git?.onDefault ?? null]);

// ALYDAS-265. The beat with what the session is doing and what it has spent.
// The numbers ride the ended beat too, since the desk takes a beat's token
// count as the run's. The prompt line is `state.prompt`, which is only ever
// what `promptLine` cut: null (never sent: the laptop not told, or no prompt
// yet) leaves the desk's alone, and blank clears it, as sending switched off
// sends (ALYDAS-526 c3).
function withDoing(body, state, ended) {
  if (!ended) {
    if (typeof state.prompt === 'string') body.prompt = state.prompt;
    body.tool = typeof state.tool === 'string' ? state.tool : '';
  }
  const figures = usageFigures(state.usage);
  if (figures !== null) {
    body.tokens = figures.tokens;
    if (figures.costUsd !== null) {
      body.costUsd = figures.costUsd;
      // An estimate: the desk says "at least".
      body.costExact = false;
    }
  }
  return body;
}

function bodyBefore(state, branch, idle, link, ended) {
  const body = {
    run: state.run,
    agent: AGENT,
    statusLine: statusLine(branch, idle),
    branch: branch ? cut(branch, BRANCH_MAX) : null,
  };
  // ALYDAS-264. Which session this run is, on every beat, the ended one too.
  // Only an id the desk would take (its SESSION_KEY); hook.mjs lets no other
  // session id this far.
  if (SESSION_KEY.test(String(state.sessionId ?? ''))) body.session = state.sessionId;
  // Never on the ended beat: the desk keeps a run's link when a beat names
  // none, and a stale item would otherwise refuse the one beat that ends the
  // run.
  const item = ended ? null : link.key;
  if (item !== null) body.workItem = item;
  if (ended) body.ended = true;
  // ALYDAS-262. Never on the ended beat: the desk carries nothing to a run
  // that is ending, and an answer taken there would reach nobody.
  if (!ended) {
    // ALYDAS-263.
    body.idle = Boolean(idle);
    body.answers = true;
    if ((state.heard ?? []).length > 0) body.heard = state.heard.slice(0, HEARD_MAX);
  }
  return body;
}

// ALYDAS-127, S5 (found while building S8). The desk's start a session was
// started for, from ALYDASH_RUN_START, which the laptop runner sets
// (lib/runner.mjs): named as `start` on every beat while it is set, as
// pipeline.py names it, because a session that never runs the pipeline (a
// fix, a chore) has no other run to join the start with. The desk honours it
// only on the beat that inserts a run, and joins the start to whichever run of
// the session names it first. Only an id the desk would take (runs'
// `START_ID`, copied), so a stray value is never a refused beat.
const START_ID = /^[A-Za-z0-9_-]{1,100}$/;

export function runStartOf(env) {
  const named = String(env?.ALYDASH_RUN_START ?? '').trim();
  return START_ID.test(named) ? named : null;
}

export function decide(event, input, state, now, env = {}) {
  const sessionId = String(input?.session_id ?? '');
  let next =
    state && state.sessionId === sessionId
      ? { ...state, pending: [...(state.pending ?? [])], answers: [...(state.answers ?? [])], heard: [...(state.heard ?? [])] }
      : null;

  // ALYDAS-265. The session's count as hook.mjs just read it, or the last one.
  const usage = input?.usage ?? next?.usage ?? null;
  if (event === 'SessionStart') {
    // A resumed (or compacted, or cleared) session keeps its run, so its card
    // stays one card. A stopped run is never reused: the desk answers stop on
    // every beat of it once pressed (runs.ts nextDirective), so reusing it
    // would stop the resumed session for good. A fresh start is a new run.
    const keep = next !== null && !next.stopped && input?.source !== 'startup';
    // A new run in a session that already had one counts its spend from here.
    const again = next !== null && !keep;
    next = keep ? next : freshState(sessionId, now);
    next.usage = again && usage !== null ? restartUsage(usage) : usage;
    // ALYDAS-524. HEAD as the run starts, which hook.mjs read (`head`). A
    // resumed run keeps the one its first start recorded, so a commit made
    // before the resume still counts as the session's.
    // Gate 8 (G8W2). And origin's default tip as the laptop knew it then
    // (hook.mjs `originHead`), recorded with it and kept by the same rule.
    if (typeof next.startHead !== 'string') {
      next.startHead = typeof input?.head === 'string' && COMMIT.test(input.head) ? input.head : null;
      next.startOrigin = typeof input?.originHead === 'string' && COMMIT.test(input.originHead) ? input.originHead : null;
    }
    // Gate 8 (code F3). The desk and repository this run beats to, as the
    // hook picked them for the session's directory, so stage.mjs beats the
    // run with that credential and no other. Each start says them again.
    if (typeof input?.desk === 'string' && input.desk !== '') next.desk = input.desk;
    if (typeof input?.repo === 'string' && input.repo !== '') next.repo = input.repo;
  } else if (next === null) {
    // No session file: the session began before this repository was
    // connected (or the file was lost). A run starts now, except at the end,
    // where there is nothing to end.
    if (event === 'SessionEnd') return { beat: null, state: null };
    next = freshState(sessionId, now);
    next.usage = usage;
  } else {
    next.usage = usage;
  }

  // ALYDAS-265. What the session is doing, kept in the state as it will be
  // sent and no fuller: a prompt's first line (null when it may not be sent),
  // and the last tool's name, which a new prompt and the end of a turn clear.
  // ALYDAS-544. A wake leaves the line a person typed as it was, and never
  // blanks it (a blank clears the desk's); the turn is still a new turn.
  const wake = event === 'UserPromptSubmit' && isSystemWake(input?.prompt);
  if (event === 'UserPromptSubmit') {
    if (!wake) next.prompt = sendsPrompt(env) && input?.told === true ? promptLine(input?.prompt) : null;
    // ALYDAS-515. A new turn's reply is not written yet.
    next.reply = null;
    next.tool = '';
  } else if (event === 'PostToolUse') {
    next.tool = toolName(input) ?? next.tool ?? '';
  } else if (event === 'Stop' || event === 'SessionStart') {
    next.tool = '';
  }
  // ALYDAS-515, S1. The opening of the last reply, read by hook.mjs on Stop
  // and SessionEnd (`lastReply`), kept as it will be sent and only where the
  // prompt line may be.
  const outcomeEvent = event === 'Stop' || event === 'SessionEnd';
  if (outcomeEvent) next.reply = sendsPrompt(env) && input?.told === true ? replyLine(input?.lastReply) : null;
  // ALYDAS-526 c3. Prompts off: blank, which the desk clears what it shows
  // on, and never the text.
  if (!sendsPrompt(env)) {
    next.prompt = '';
    next.reply = outcomeEvent ? '' : null;
  }
  // Gate 8 (W6). The git facts are part of what SENDS_VERSION 2 tells a
  // laptop, so they wait for the notice as the reply does; the prompt switch
  // holds back text only, as the notice says.
  const outcome = outcomeEvent ? { reply: next.reply ?? null, git: input?.told === true ? (input?.git ?? null) : null } : null;

  // ALYDAS-523. The SessionEnd hook ran, whatever it sends: the runner, which
  // reads this state when the session's process is gone, sends nothing more.
  next.ended = event === 'SessionEnd';

  // Idle once a turn is over, and at the start, before the first prompt.
  if (event === 'Stop' || event === 'SessionStart') next.idle = true;
  else if (event !== 'SessionEnd') next.idle = false;

  // Stopped from the desk, or turned away by the run cap: this session sends
  // nothing more.
  if (next.stopped || next.capped) return { beat: null, state: next };

  // ALYDAS-262. An Ask this session just raised is named to the desk at once,
  // whatever the throttle, so it is the session's before anybody answers it.
  const raised = event === 'PostToolUse' ? heardAsk(input) : null;
  if (raised !== null && !next.heard.includes(raised)) next.heard = [...next.heard, raised].slice(-HEARD_MAX);

  // A Stop whose last beat did not already say idle beats whatever the
  // throttle, so the card never says working while the session waits.
  const turningIdle = event === 'Stop' && !String(next.last?.statusLine ?? '').endsWith(' · idle');
  // ALYDAS-515. A Stop whose reply or commit is not what the last Stop sent
  // beats too, so the page has the turn's last reply.
  // A Stop that knows neither is no reason to beat.
  const newOutcome =
    event === 'Stop' &&
    ((typeof outcome.reply === 'string' && outcome.reply !== '') || typeof outcome.git?.commit === 'string') &&
    outcomeKey(outcome) !== next.outcomeSent;
  const due =
    ALWAYS_BEATS.has(event) ||
    turningIdle ||
    newOutcome ||
    raised !== null ||
    // ALYDAS-265. Over its budget, so every hook asks whether it was extended.
    next.held === true ||
    next.lastBeatAt === null ||
    now - next.lastBeatAt >= THROTTLE_MS ||
    // ALYDAS-449. Runs is off for the app: each prompt asks again whatever
    // the throttle, so a switch back on is heard at the next prompt. A tool
    // use does not, so a session that is not shown is not a stream of
    // refusals either.
    (event === 'UserPromptSubmit' && next.runsOff === true);
  if (!due) return { beat: null, state: next };

  const ended = event === 'SessionEnd';
  // ALYDAS-266. Kept beside the beat, so applyReply knows whose word a
  // refused item was.
  next.link = linkOf(env, next, input?.branch ?? null, input?.work);
  const beat = bodyOf(next, input?.branch ?? null, next.idle, next.link, ended, outcome);
  // ALYDAS-544. The wake's own beat says nothing of the prompt; the turn's
  // later beats carry the kept line, as any turn's do. Prompts off still
  // sends its blank.
  if (wake && sendsPrompt(env)) delete beat.prompt;
  if (outcome !== null) next.outcomeSent = outcomeKey(outcome);
  const start = runStartOf(env);
  if (start !== null) beat.start = start;
  next.lastBeatAt = now;
  next.last = { ...beat };
  delete next.last.workItem;
  delete next.last.ended;
  delete next.last.answers;
  delete next.last.heard;
  delete next.last.idle;
  delete next.last.prompt;
  delete next.last.tool;
  // ALYDAS-515. Said on the beat that read them, and on no other.
  delete next.last.reply;
  delete next.last.commit;
  delete next.last.ahead;
  delete next.last.pushed;
  delete next.last.onDefault;
  return { beat, state: next };
}

// The body of the ended beat that follows a stop: the last beat, ended.
export function endedBeat(state) {
  const last = state?.last ?? { run: state.run, agent: AGENT, statusLine: statusLine(null, false), branch: null };
  return { ...last, ended: true };
}

// ALYDAS-523 (design D3). The ended beat the laptop runner sends for a
// session it started whose `claude` process exited with no SessionEnd: the
// hook's ended beat (`endedBeat`, the last beat, ended), with no reply and the
// git facts the runner read of the session's worktree, or null for none (not
// told, as the hook holds them back). `start` is the desk's start the runner
// began the session for, named when the last beat did not.
//
// Null when there is nothing to end: no state, the SessionEnd hook already
// ran (`ended`), or the run was stopped from the desk (whose ended beat went
// then) or never shown (the run cap).
export function diedBeat(state, git, start = null) {
  if (state === null || typeof state !== 'object' || Array.isArray(state)) return null;
  if (state.ended === true || state.stopped === true || state.capped === true) return null;
  if (typeof state.run !== 'string' || state.run === '') return null;
  const body = withOutcome(endedBeat(state), { reply: null, git: git ?? null });
  if (body.start === undefined && typeof start === 'string' && START_ID.test(start)) body.start = start;
  return body;
}

// The retry a beat that named a work item gets when the desk refused it with
// 400, 403 or 404: the same beat without the item. Null when there is none to
// make, and for runs switched off (ALYDAS-449), which no item caused.
export function itemRetry(beat, reply) {
  if (!beat || beat.workItem === undefined || !reply) return null;
  if (![400, 403, 404].includes(reply.status) || isRunsOffRefusal(reply)) return null;
  const retry = { ...beat };
  delete retry.workItem;
  return retry;
}

// The run cap's refusal (heartbeat.ts RUN_CAP_REFUSAL), told apart from the
// per-token rate limit's 429 (limits.ts), which is "try later" and not "this
// session will not show". tests/plugin/session.test.ts pins both sentences.
export function isRunCapRefusal(reply) {
  return reply?.status === 429 && /runs going/.test(String(reply?.json?.error ?? ''));
}

const ok = (reply) => reply !== null && reply !== undefined && reply.status >= 200 && reply.status < 300;

// `context` is what hook.mjs knows of the credential: `appName`, which the
// runs lines name (ALYDAS-449).
export function applyReply(event, reply, state, context = {}) {
  if (state === null || state === undefined) return { output: null, state, followUp: null };
  const next = { ...state, pending: [...(state.pending ?? [])], answers: [...(state.answers ?? [])] };
  const notices = [];
  let followUp = null;
  let stop = false;
  let budget = null;

  // ALYDAS-449. Runs switched off for the app, said once; and once a beat
  // lands again, switched back on, said once.
  if (isRunsOffRefusal(reply) && next.runsOff !== true) {
    next.runsOff = true;
    notices.push(runsOffNotice(appNameOf(context)));
  } else if (ok(reply) && next.runsOff === true) {
    next.runsOff = false;
    notices.push(runsOnNotice(appNameOf(context)));
  }
  if (isRunCapRefusal(reply) && !next.capped) {
    next.capped = true;
    notices.push(CAP_NOTICE);
  }
  // `droppedItem` is hook.mjs saying this answer is the retry without the
  // item (itemRetry), and that it landed: the item was what the desk refused.
  const dropped = ok(reply) && typeof reply.droppedItem === 'string' ? reply.droppedItem : null;
  const guessed = next.link?.by === 'branch';
  if (dropped !== null && guessed) {
    // ALYDAS-266. A guess from the branch: remembered, so it is not sent again.
    next.guessRefused = [...(next.guessRefused ?? []).filter((key) => key !== dropped), dropped].slice(-GUESSES_MAX);
  } else if (dropped !== null && !next.itemRefused) {
    next.itemRefused = true;
    notices.push(itemRefusedNotice(reply.droppedItem));
  }
  // ALYDAS-266. A beat that named an item and landed as sent linked the run,
  // and the desk keeps that link when a later beat names none.
  if (ok(reply) && dropped === null && typeof next.link?.key === 'string') next.linked = true;
  // The branch named nothing the desk would link, there is no other key on it
  // to try, and the run was never linked: said once, in the same words
  // whether the branch had no key or the desk refused the one it had.
  const nothingNamed = ok(reply) && guessed && (dropped !== null || next.link.key === null) && !next.link.spare;
  if (nothingNamed && !next.linked && !next.unlinkedSaid) {
    next.unlinkedSaid = true;
    notices.push(UNLINKED_NOTICE);
  }

  // The desk no longer knows this token (disconnected, or expired on its
  // side). Said at the start of a session, once.
  //
  // ALYDAS-342. Unless the desk says the 401 is the switch, which ends
  // nothing: then its own sentence is said, once, and kept apart from the
  // notice above, so a session told one is still told the other.
  const switchedOff = event === 'SessionStart' ? switchedOffSentence(reply) : null;
  if (switchedOff !== null) {
    if (!next.switchedOffSaid) {
      next.switchedOffSaid = true;
      notices.push(`${REFUSED_PREFIX}${switchedOff}`);
    }
  } else if (event === 'SessionStart' && reply?.status === 401 && !next.disconnectedSaid) {
    next.disconnectedSaid = true;
    notices.push(DISCONNECTED_NOTICE);
  }

  const directive = ok(reply) ? reply.json?.directive : null;
  // ALYDAS-265. Held while the desk's answer is the budget stop, and no
  // longer once a beat is answered anything else (the budget was extended, or
  // a person's plain stop took its place).
  // A hook the desk did not answer stops nothing, and asks again next time.
  const over = directive?.kind === 'stop' && directive.reason === 'budget';
  if (ok(reply) && (over || next.held)) next.held = over;
  if (over && !next.stopped) {
    // The turn stops as it does for a desk Stop, and nothing ends: the run
    // stays, waiting on the person the desk has asked.
    budget = budgetStopReason(directive);
    stop = STOPS_TURN.has(event);
    if (!stop && event === 'Stop') notices.push(budget);
  } else if (directive?.kind === 'stop' && !next.stopped) {
    next.stopped = true;
    stop = STOPS_TURN.has(event);
    // At Stop the turn is already over, so the person is told instead.
    if (event === 'Stop') notices.push(STOP_REASON);
    // The desk is told the run ended, at Stop too (the stop was obeyed there,
    // and without it the card would wait ten minutes to go quiet), except at
    // SessionEnd, whose own beat already said so.
    if (event !== 'SessionEnd') followUp = 'ended-beat';
  } else if (directive?.kind === 'message' && typeof directive.text === 'string') {
    const text = cleanMessage(directive.text);
    if (text !== null) next.pending.push(text);
  }
  // ALYDAS-262. The beat landed, so the desk has the Asks it named; and what
  // it answered with is queued, as a message is. The desk carries none beside
  // a stop.
  if (ok(reply)) {
    next.heard = [];
    const carried = Array.isArray(reply.json?.answers) ? reply.json.answers : [];
    for (const answer of carried) {
      const line = answerLine(answer);
      if (line !== null) next.answers.push(line);
    }
  }

  const output = {};
  if (stop) {
    output.continue = false;
    output.stopReason = budget ?? STOP_REASON;
  } else if (next.pending.length + next.answers.length > 0 && !next.stopped) {
    const text = [...next.pending.map((message) => `${MESSAGE_PREFIX}${message}`), ...next.answers].join('\n');
    if (SHOWS_CONTEXT.has(event)) {
      output.hookSpecificOutput = { hookEventName: event, additionalContext: text };
      next.pending = [];
      next.answers = [];
    } else if (event === 'Stop') {
      output.decision = 'block';
      output.reason = text;
      next.pending = [];
      next.answers = [];
    }
    // SessionEnd: kept in state, which hook.mjs deletes once the ended beat is
    // sent, so the message is dropped and never reaches another session.
  }
  if (notices.length > 0) output.systemMessage = notices.join('\n');

  return { output: Object.keys(output).length > 0 ? output : null, state: next, followUp };
}
