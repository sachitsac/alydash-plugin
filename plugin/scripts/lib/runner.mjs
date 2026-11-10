// ALYDAS-127, S5 (design D5). The laptop runner's rules: what `/alydash:runner`
// checks before it starts a worker, and what the worker (`runner.mjs work`)
// does with each start the desk hands it. The worker is the loop and the
// processes; this file is what it decides, kept pure where it can be, so
// tests/plugin/runner.test.ts can hold it.
//
// For each start the desk hands over (`POST /api/runner/laptop/claim`, the
// laptop runner module's long-poll), the worker:
//
//   1. dedupes it by its id: delivery is at least once (a claim the desk does
//      not hear acknowledged goes back to the queue after two minutes), so a
//      start this laptop already began is acknowledged again, never begun
//      twice (`firstDelivery`);
//   2. makes a worktree beside the repository, `../<repo>-<start>`, from
//      origin's default branch (`worktreePath`);
//   3. runs Claude Code there on the person's own subscription,
//      `claude -p "<prompt>" --permission-mode auto --session-id <uuid>`
//      (Ask ddad76ac, and amendment 45: the session is named up front, so its
//      id is known before it runs) with the four variables the session's
//      hooks and the pipeline read (`claudeEnv`);
//   4. acknowledges it with that session (`POST /api/runner/laptop/claimed`);
//   5. stops the process once the run has ended: the session's own hook
//      records the desk's stop in its state file (lib/session.mjs
//      applyReply, `stopped`), and `runEnded` reads it.

import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// The scope a start is claimed with, the laptop runner module's grant
// (connect.mjs RUNNER_SCOPE, which connect asks for).
export const RUNNER_SCOPE = 'claim:RunStart';

// Seconds the worker asks the desk to hold a claim while nothing is pending
// (`{ "wait": 25 }`; the desk holds none unless asked).
export const CLAIM_HOLD_S = 25;
// Seconds the worker gives the desk to answer a claim: the desk holds it up
// to 25 s, and the route may take 30.
export const CLAIM_TIMEOUT_MS = 35_000;
// A claim answered sooner than this (nothing pending on a desk that does not
// hold, or a refusal) is not asked again before it is up.
export const CLAIM_MIN_MS = 2_000;
// After a desk that could not be reached.
export const CLAIM_BACKOFF_MS = 15_000;
// How often the worker looks at its running sessions.
export const RUNNER_TICK_MS = 2_000;

// The desk's START_ID (runs' run-start-state.ts), copied: an id it would
// never hand out is never made into a path.
const START_ID = /^[A-Za-z0-9_-]{1,100}$/;

export const NOT_A_REPO = 'Run /alydash:runner inside the GitHub repository of an app on your desk.';
export const NOT_CONNECTED = 'This repository is not connected to a desk. Run /alydash:connect first, then /alydash:runner.';
export const RECONNECT =
  'This connection cannot take work from the desk: it was made before the laptop runner. Reconnect with /alydash:connect, then run /alydash:runner again.';
export const runnerStarted = (appName, desk) =>
  `The laptop runner is taking work for ${oneLine(appName)} from ${desk}. Each start runs as a Claude Code session in a worktree beside this repository. Stop it with /alydash:runner stop.`;
export const runnerAlready = (appName, desk) => `The laptop runner is already taking work for ${oneLine(appName)} from ${desk}.`;
export const RUNNER_STOPPED = 'The laptop runner stopped taking work here. Sessions it started carry on until they end.';
export const RUNNER_NOT_RUNNING = 'No laptop runner is taking work here.';

const oneLine = (text) => String(text).replace(/\s+/g, ' ').trim();

// Whether a stored connection may claim a start: its filed scopes name the
// runner's. Strict, unlike the hook's reading of scopes: an entry from before
// connect filed its scopes was made before this scope existed, so it lacks it.
export function holdsRunnerScope(entry) {
  const scopes = entry?.scopes;
  return Array.isArray(scopes) && scopes.includes(RUNNER_SCOPE);
}

// The key a repository's worker files are kept under, in the plugin's data
// directory: `owner/name` in lower case, with no separator a path would read.
export function repoKey(repo) {
  return String(repo).toLowerCase().replace(/[^a-z0-9._-]+/g, '__');
}

// A start the desk handed over, as the worker takes it, or null for one it
// cannot run: an id it would never hand out, or no prompt.
export function startOf(json) {
  const start = json?.start;
  if (start === null || typeof start !== 'object') return null;
  if (typeof start.id !== 'string' || !START_ID.test(start.id)) return null;
  if (typeof start.prompt !== 'string' || start.prompt.trim() === '') return null;
  const key = typeof start.subject?.key === 'string' ? start.subject.key : '';
  const budget = typeof start.budgetUsd === 'number' && Number.isFinite(start.budgetUsd) && start.budgetUsd > 0 ? start.budgetUsd : null;
  return { id: start.id, kind: start.kind === 'resume' ? 'resume' : 'start', prompt: start.prompt, item: key, budgetUsd: budget };
}

// Where a start's worktree goes: beside the repository, never inside it (a
// worktree inside the checkout is read by its guards and its tools), named
// for the repository and the start.
export function worktreePath(root, startId) {
  if (!START_ID.test(String(startId))) throw new Error('A start id names no worktree.');
  return join(dirname(root), `${basename(root)}-${startId}`);
}

// Claude Code's arguments for a start: one prompt, non-interactive, in auto
// permission mode as ruled (Ask ddad76ac), as the session this worker named.
export function claudeArgs(prompt, sessionId) {
  return ['-p', prompt, '--permission-mode', 'auto', '--session-id', sessionId];
}

// The session's environment: the worker's own, with the four the desk's
// start is told by set, each from the start and never inherited, so a value
// left in the person's shell never names another start.
//
//   ALYDASH_CREDENTIALS  where the plugin's credential store is (a path; the
//                        skills read the token from it)
//   ALYDASH_RUN_START    the start, which every beat names (hook.mjs,
//                        pipeline.py) so the desk joins the run to it
//   ALYDASH_WORK_ITEM    the item's key, which every beat names
//   ALYDASH_RUN_BUDGET   the start's budget in dollars, empty for none
export function claudeEnv(base, { credentials, start }) {
  return {
    ...base,
    ALYDASH_CREDENTIALS: credentials,
    ALYDASH_RUN_START: start.id,
    ALYDASH_WORK_ITEM: start.item,
    ALYDASH_RUN_BUDGET: start.budgetUsd === null ? '' : String(start.budgetUsd),
  };
}

// The worker's dedupe, by start id: `seen` is what it filed of the starts it
// began (`{ [id]: { session, ref } }`). A start in it is acknowledged again
// with what was filed and begun no second time.
export function firstDelivery(seen, id) {
  return !Object.prototype.hasOwnProperty.call(seen ?? {}, id);
}

// Whether a session's run has ended, read off the state file its own hook
// keeps (`sessions/<session id>.json`): the desk answered stop.
export function runEnded(state) {
  return state !== null && typeof state === 'object' && state.stopped === true;
}

// Whether the desk's answer to a claim ends the worker: the token is not
// taken (401), or may not claim (403: the scope, or the runner switched off).
export function claimEnds(reply) {
  return reply?.status === 401 || reply?.status === 403;
}

// ---------------------------------------------------------------------------
// ALYDAS-127, S7 (Ask 79e3e335, runner-waits). A desk-started run does not
// end at a hold. A `claude -p` session that reaches one ends its turn, and
// the hold it ran ends with it, leaving the phase blocked with its Ask
// recorded on the run's ledger. The runner then waits at that hold itself
// (`runner.mjs carry`): it runs the pipeline's own `hold` again, which reads
// back the Ask the session recorded and beats as the session's run, with the
// same token. Once a person answers, `hold` closes or reopens the phase, and
// the runner resumes the same session with the answer as its next prompt
// (`resumeArgs`). A stop or an expired Ask ends the hold as it does today,
// and nothing is resumed.

// The plugin's own pipeline, the one the start's prompt runs
// (/alydash:feature-pipeline): its `hold` is what the runner waits with.
export const PIPELINE_SCRIPT = fileURLToPath(new URL('../../skills/feature-pipeline/scripts/pipeline.py', import.meta.url));

// Claude Code's arguments for the session resumed after a hold: the answer
// as its one prompt, in the same permission mode, as the same session.
export function resumeArgs(prompt, sessionId) {
  return ['-p', prompt, '--permission-mode', 'auto', '--resume', sessionId];
}

// The phase a run's ledger says waits on a hold, as the pipeline's own
// `waits_on_a_hold` reads it: blocked, with a `hold` the hold command wrote
// (a phase blocked by hand has none). `ledgers` is `[{ path, run }]`, each
// ledger at `<root>/boards/<date>-<slug>.json`; the first that waits wins.
export function heldPhase(ledgers) {
  for (const { path, run } of ledgers ?? []) {
    if (run === null || typeof run !== 'object' || typeof run.slug !== 'string' || !Array.isArray(run.phases)) continue;
    for (const phase of run.phases) {
      if (phase?.status !== 'blocked' || phase.hold === null || typeof phase.hold !== 'object' || !Number.isInteger(phase.n)) continue;
      const attempt = Number.isInteger(phase.hold.attempt) ? phase.hold.attempt : 1;
      return { root: dirname(dirname(path)), slug: run.slug, n: phase.n, attempt };
    }
  }
  return null;
}

// What an answer did to the held phase, read off the phase once `hold` has
// ended: closed (Approve or a variant: the phase is done), sent back (it runs
// again, the note as its scene), or unanswered (still waiting: a stop, an Ask
// that expired, or a hold that could not reach the desk).
export function holdOutcome(phase) {
  if (phase?.status === 'done') return 'closed';
  if (phase?.status === 'active') return 'sent-back';
  return 'unanswered';
}

// The answer as `hold` wrote it on the run's log (`Answered on the desk:
// <choice>. Note: <note>`): the last decision event of phase `n`, or null.
export function lastDecision(events, n) {
  const found = (events ?? []).filter((event) => event?.phase === n && event.event === 'decision' && typeof event.note === 'string');
  return found.length === 0 ? null : found.at(-1).note;
}

// The resumed session's prompt: what the person answered, with its note, word
// for word, and what that did to the phase.
export function answerPrompt(outcome, n, decision) {
  const said = decision === null ? '' : ` ${decision}`;
  if (outcome === 'sent-back') {
    return `The desk sent phase ${n} back.${said}\n\nPhase ${n} is running again, with the note as its brief. Work on it again, then hold it as before.`;
  }
  return `The desk answered the hold on phase ${n}.${said}\n\nPhase ${n} is closed. Go on with the run.`;
}

// What the runner writes on the run's log when it stops waiting because its
// time ran out (`carry --until`: a GitHub Actions job's token and job last six
// hours). The Ask is not answered for anyone, so it stays open on the desk.
export const outOfTime = (n) =>
  `Phase ${n} was still waiting on the desk when this job ran out of time, so the runner stopped waiting. The question stays open on the desk.`;
