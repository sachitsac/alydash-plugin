// ALYDAS-522, slice E (design D1). The dev-pipeline skill's stage report:
//
//   node stage.mjs <n> start|done|failed|skipped [reason...]
//
// run by the skill at each stage boundary, Stage 0 to Stage 6. It beats the
// session's own run (the one the hook keeps in `sessions/<id>.json`, named
// there by the hook) with a seven-stage ledger, so the run page draws the
// stages in the bar a feature-pipeline run's phases use. The stages so far are
// kept beside the hook's state, in `sessions/<id>.stages.json`, since each
// call is its own process and a beat carries the whole ledger.
//
// It finds what the hook finds: the credential store the hook exported into
// the session (`ALYDASH_CREDENTIALS`, a path), the session from
// `CLAUDE_CODE_SESSION_ID`, and (Gate 8, code F3) the entry for the desk and
// repository the hook's state names (`desk`, `repo`, recorded at
// SessionStart), never one picked again for the call's own directory. A state
// that names neither (an older plugin's) sends nothing.
//
// Gate 8 (G8W4, the conservative option while the ruling is open). A failed
// or skipped stage's reason is free text, so it is sent only as the hook
// sends a prompt line: once the laptop was told what is sent (told.json at
// SENDS_VERSION) and with ALYDASH_SEND_PROMPT on. Otherwise the stage goes
// with its state and no reason. No store, no entry, an entry for a
// desk without runs (no heartbeat scope), or no session the hook beats for is
// no desk connection: it sends nothing, writes nothing and says nothing
// (criterion 4). It always exits 0, so a desk that is down or refuses never
// holds up the pipeline, and no line it writes carries the token.
//
// The desk's answer reaches the session as the hook's would: a message a
// person sent is printed on stdout, where the session reads a command's
// output, and a stop is said.

import { existsSync, readFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CREDENTIALS_FILE, isLive, pickCredential, sendsHeartbeat, writePrivateJson } from './lib/credentials.mjs';
import { postJson } from './lib/desk.mjs';
import { AGENT, MESSAGE_PREFIX, SENDS_VERSION, SESSION_KEY, STOP_REASON, cleanMessage, runStartOf, sendsPrompt, statusLine } from './lib/session.mjs';

// The seven stages, in order, numbered from 0 as the skill numbers them.
export const STAGES = ['Sandbox', 'Think', 'Code', 'Lint', 'Commit', 'Test', 'Review'];
export const PHASE_RANGE = [0, STAGES.length - 1];
export const MARKS = ['start', 'done', 'failed', 'skipped'];
// A reason is held to the bound a note has (runs' LEDGER_BOUNDS.note), well
// inside a summary's and a skip reason's, and folded to one line.
export const REASON_MAX = 500;
export const USAGE = 'usage: stage <0-6> start|done|failed|skipped [reason]';

const say = (line) => process.stderr.write(`alydash: ${String(line).split('\n')[0].slice(0, 300)}\n`);
const errorText = (error) => (error instanceof Error ? error.message : 'unknown error');

// Control characters and the invisible ones out, runs of space folded, and
// cut by code point.
const UNSAID_RANGES = [[0x00, 0x1f], [0x7f, 0x9f], [0x200b, 0x200f], [0x2028, 0x202e], [0x2060, 0x2064], [0xfeff, 0xfeff]];
const hex = (point) => `\\u{${point.toString(16)}}`;
const UNSAID = new RegExp(`[${UNSAID_RANGES.map(([from, to]) => `${hex(from)}-${hex(to)}`).join('')}]`, 'gu');
export function reasonOf(words) {
  const text = words.join(' ').replace(UNSAID, ' ').replace(/\s+/g, ' ').trim();
  return text === '' ? null : [...text].slice(0, REASON_MAX).join('');
}

// `[n, mark, reason]` from the command line, or null for a call that is not one.
export function parseArgs(argv) {
  const [first, mark, ...rest] = argv;
  if (!/^\d$/.test(first ?? '') || Number(first) > PHASE_RANGE[1] || !MARKS.includes(mark)) return null;
  return { n: Number(first), mark, reason: reasonOf(rest) };
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

const freshStage = (n) => ({ n, status: 'pending', reason: null, startedAt: null, endedAt: null });

// The stages after this call: the stage's own state, and on a start, the
// stages after it put back to do (the pipeline went back to fix and run them
// again, as Stage 5 sends it back to Stage 3). A skipped stage stays skipped.
export function nextStages(before, { n, mark, reason }, now) {
  const stages = STAGES.map((_, at) => {
    const kept = Array.isArray(before) ? before.find((one) => one && one.n === at) : undefined;
    return kept ? { ...freshStage(at), ...kept } : freshStage(at);
  });
  const at = now.toISOString();
  const stage = stages[n];
  if (mark === 'start') {
    Object.assign(stage, { status: 'running', reason: null, startedAt: at, endedAt: null });
    for (const later of stages.slice(n + 1)) if (later.status !== 'skipped') Object.assign(later, freshStage(later.n));
  } else {
    const status = mark === 'done' ? 'done' : mark;
    Object.assign(stage, {
      status,
      reason: mark === 'done' ? null : reason,
      startedAt: stage.startedAt ?? at,
      endedAt: at,
    });
  }
  return stages;
}

// The ledger as the desk reads it (runs' LedgerPhaseInput): a failed stage's
// reason is its summary, a skipped one's its skip reason.
// `reasons` false sends every stage with no reason (G8W4).
export function ledgerOf(stages, { reasons = true } = {}) {
  const said = (stage, status) => (reasons && stage.status === status ? stage.reason : null);
  return {
    phases: stages.map((stage) => ({
      n: stage.n,
      name: STAGES[stage.n],
      kind: 'phase',
      status: stage.status,
      summary: said(stage, 'failed'),
      skipReason: said(stage, 'skipped'),
      startedAt: stage.startedAt,
      endedAt: stage.endedAt,
    })),
  };
}

// The beat: the hook's run, agent and session key, so it is the same run, and
// the last beat's status line, branch and numbers, so the card keeps them (a
// beat with no count is a run with none). It names no work item, as the
// desk keeps the run's link when a beat names none, and asks for no answers,
// which the hook carries.
export function stageBeat(state, stages, env = {}, { reasons = true } = {}) {
  const last = state.last && typeof state.last === 'object' ? state.last : {};
  const branch = typeof last.branch === 'string' && last.branch !== '' ? last.branch : null;
  const body = {
    run: state.run,
    agent: AGENT,
    statusLine: typeof last.statusLine === 'string' && last.statusLine.trim() !== '' ? last.statusLine : statusLine(branch, false),
    branch,
    idle: false,
    ledger: ledgerOf(stages, { reasons }),
    phaseRange: [...PHASE_RANGE],
  };
  if (SESSION_KEY.test(String(state.sessionId ?? ''))) body.session = state.sessionId;
  if (Number.isFinite(last.tokens)) body.tokens = last.tokens;
  if (Number.isFinite(last.costUsd)) {
    body.costUsd = last.costUsd;
    body.costExact = false;
  }
  const start = runStartOf(env);
  if (start !== null) body.start = start;
  return body;
}

// The store the hook named, as an absolute path to its credentials file, or
// null.
function storeDir(env) {
  const named = String(env.ALYDASH_CREDENTIALS ?? '').trim();
  if (named === '' || !isAbsolute(named) || basename(named) !== CREDENTIALS_FILE || !existsSync(named)) return null;
  return dirname(named);
}

// hook.mjs's TOLD_FILE: where the laptop keeps that it was told what is sent.
const TOLD_FILE = 'told.json';

// `_cwd` is kept for callers: the repository is the state's, not the call's.
export async function report(argv, env = process.env, _cwd = process.cwd(), now = new Date()) {
  const call = parseArgs(argv);
  if (call === null) {
    say(USAGE);
    return;
  }
  const dataDir = storeDir(env);
  const sessionId = String(env.CLAUDE_CODE_SESSION_ID ?? '');
  if (dataDir === null || !SESSION_KEY.test(sessionId)) return;
  const sessions = join(dataDir, 'sessions');
  const state = readJson(join(sessions, `${sessionId}.json`));
  // No session the hook beats for: the desk has no run for this session.
  if (!state || state.sessionId !== sessionId || typeof state.run !== 'string' || state.stopped === true) return;
  // Gate 8 (code F3). The desk and repository the hook beat this run to.
  const repo = typeof state.repo === 'string' && state.repo !== '' ? state.repo : null;
  const desk = typeof state.desk === 'string' && state.desk !== '' ? state.desk : null;
  if (repo === null || desk === null) return;
  let entry = null;
  try {
    entry = pickCredential(dataDir, repo, { desk, now });
  } catch {
    return;
  }
  if (!entry || entry.desk !== desk || !isLive(entry, now) || !sendsHeartbeat(entry)) return;
  // Gate 8 (G8W4). As the hook judges it (hook.mjs `told`).
  const reasons = sendsPrompt(env) && Number(readJson(join(dataDir, TOLD_FILE))?.sends) >= SENDS_VERSION;

  const stagesFile = join(sessions, `${sessionId}.stages.json`);
  const kept = readJson(stagesFile);
  const stages = nextStages(kept && kept.run === state.run ? kept.stages : null, call, now);
  try {
    writePrivateJson(stagesFile, { sessionId, run: state.run, stages });
  } catch (error) {
    say(`could not keep the stages so far: ${errorText(error)}`);
  }

  let reply;
  try {
    reply = await postJson(`${entry.desk}/api/sessions/heartbeat`, stageBeat(state, stages, env, { reasons }), { token: entry.token });
  } catch (error) {
    say(`could not tell the desk about Stage ${call.n}: ${errorText(error)}`);
    return;
  }
  if (reply.status < 200 || reply.status >= 300) {
    const said = typeof reply.json?.error === 'string' ? reply.json.error : `it answered ${reply.status}`;
    say(`the desk did not take Stage ${call.n}: ${said}`);
    return;
  }
  const directive = reply.json?.directive;
  if (directive?.kind === 'stop') process.stdout.write(`${STOP_REASON}\n`);
  else if (directive?.kind === 'message' && typeof directive.text === 'string') {
    const text = cleanMessage(directive.text);
    if (text !== null) process.stdout.write(`${MESSAGE_PREFIX}${text}\n`);
  }
}

// Run as a script, not when a suite imports it.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    await report(process.argv.slice(2));
  } catch (error) {
    say(errorText(error));
  }
  await new Promise((resolve) => process.stdout.write('', () => process.stderr.write('', resolve)));
  // fetch's keep-alive would otherwise hold the process open.
  process.exit(0);
}
