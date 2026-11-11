// ALYDAS-127, S5 (design D5). `/alydash:runner`: this laptop takes agent work
// a person starts on the desk, for this repository's app, with the
// repository's stored connection. The rules are lib/runner.mjs's; this is the
// I/O, in the pattern of hook.mjs's watcher (lib/watch.mjs): a command that
// returns at once, and a detached worker that outlives it.
//
//   start [--data <dir>]   checks the connection and leaves one worker
//                          running for this repository; says so and returns
//   stop  [--data <dir>]   ends this repository's worker; the sessions it
//                          started carry on until they end
//   work <job file> <nonce>
//                          the worker, started by `start`, never by a person
//   carry <worktree> <session> [--until <instant>] [--outcome <file>]
//                          ALYDAS-127, S7: waits at the hold a session ended
//                          at, then resumes that session with the answer, and
//                          again at its next hold, until it ends at none. The
//                          worker starts it; run.yml runs it after the action.
//                          With --outcome, it files there how it ended (Gate 8,
//                          G8C-4), so a carry that died is told from one that
//                          ended.
//
// The worker holds no token: each claim and acknowledgement reads
// credentials.json again, as the hook's watcher does, and sends the token only
// to the desk the entry names. Nothing it prints or files carries the token.
//
// The job file (`runner/<repo>.json` in the plugin's data directory) is the
// claim on the repository: it names the worker's nonce and pid, and a worker
// whose nonce it no longer names leaves. Beside it, `runner/<repo>.starts.json`
// is what the worker began, by start id, with the session and the process of
// each: the dedupe, and what lets a worker started later still stop a session
// whose run has ended. Each session's output is in `runner/logs/<start>.log`.
//
// ALYDAS-523 (design D3). A session the worker began whose `claude` process
// exited with no SessionEnd (killed, crashed) never sent its ended beat, so
// its run would only go Gone on the desk and no landing Ask would come. When
// the worker sees the process gone and the run waits on no hold, and the
// session's state file (`sessions/<session>.json`, the hook's) is still there
// with no SessionEnd recorded in it and no ended beat waiting to go
// (`<session>.end.json`), it sends that ended beat itself (lib/session.mjs
// diedBeat): the session's last beat, ended, with no reply, and the git facts
// of its worktree read as the hook reads them, once the laptop has been told
// of them (told.json). Then it removes the state file, so a SessionEnd that
// comes late finds nothing to end. Only the sessions this worker began pass
// this way; a session a person started by hand is never its to end.

import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync, closeSync, existsSync, openSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CREDENTIALS_FILE, ensurePrivateDir, isLive, pickCredential, pluginDataDir, sendsHeartbeat, writePrivateJson } from './lib/credentials.mjs';
import { deskUrl, postJson } from './lib/desk.mjs';
import { repoOf } from './lib/repo.mjs';
import {
  CLAIM_BACKOFF_MS,
  CLAIM_MIN_MS,
  CLAIM_HOLD_S,
  CLAIM_TIMEOUT_MS,
  NOT_A_REPO,
  NOT_CONNECTED,
  PIPELINE_SCRIPT,
  RECONNECT,
  RUNNER_NOT_RUNNING,
  RUNNER_STOPPED,
  RUNNER_TICK_MS,
  answerPrompt,
  claimEnds,
  claudeArgs,
  claudeEnv,
  firstDelivery,
  heldPhase,
  holdOutcome,
  holdsRunnerScope,
  lastDecision,
  outOfTime,
  repoKey,
  resumeArgs,
  runEnded,
  runnerAlready,
  runnerStarted,
  startOf,
  worktreePath,
} from './lib/runner.mjs';
import { isAlive, startedOf } from './lib/watch.mjs';
import { gitFacts } from './lib/outcome.mjs';
import { SENDS_VERSION, diedBeat } from './lib/session.mjs';

const CLAIM_PATH = '/api/runner/laptop/claim';
const CLAIMED_PATH = '/api/runner/laptop/claimed';
const HEARTBEAT_PATH = '/api/sessions/heartbeat';
// hook.mjs's SESSION_ID: a session id names a file in sessions/.
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

function git(cwd, args, timeout = 60_000) {
  return new Promise((resolve) => {
    execFile('git', ['-C', cwd, ...args], { timeout, env: { ...process.env, GIT_TERMINAL_PROMPT: '0' } }, (error, stdout, stderr) => {
      resolve({ ok: !error, out: String(stdout ?? '').trim(), err: String(stderr ?? '').trim() });
    });
  });
}

const pinnedDesk = (env) => ((env.ALYDASH_URL ?? '').trim() !== '' ? deskUrl(env) : null);

function filesOf(dataDir, repo) {
  const dir = join(dataDir, 'runner');
  const key = repoKey(repo);
  return { dir, job: join(dir, `${key}.json`), starts: join(dir, `${key}.starts.json`), log: join(dir, `${key}.log`), logs: join(dir, 'logs') };
}

// Where this command is: the repository's top level and its `owner/name`.
async function whereAmI(cwd) {
  const top = await git(cwd, ['rev-parse', '--show-toplevel'], 5_000);
  if (!top.ok || top.out === '') return null;
  const repo = await repoOf(top.out);
  return repo === null ? null : { root: top.out, repo };
}

export async function start({ env = process.env, dataArg, cwd = process.cwd() } = {}) {
  const dataDir = pluginDataDir(env, dataArg);
  const here = await whereAmI(cwd);
  if (here === null) return { code: 0, lines: [NOT_A_REPO] };
  const entry = pickCredential(dataDir, here.repo, { desk: pinnedDesk(env) });
  if (!isLive(entry)) return { code: 0, lines: [NOT_CONNECTED] };
  // A connection from before the laptop runner: said, and nothing else done.
  if (!holdsRunnerScope(entry)) return { code: 0, lines: [RECONNECT] };

  const files = filesOf(dataDir, here.repo);
  const current = readJson(files.job);
  if (current !== null && Number.isInteger(current.watcher) && isAlive(current.watcher) && current.desk === entry.desk) {
    return { code: 0, lines: [runnerAlready(entry.appName, entry.desk)] };
  }
  ensurePrivateDir(files.dir);
  const nonce = randomUUID();
  const job = { dataDir, desk: entry.desk, repo: here.repo, root: here.root, nonce, at: Date.now(), watcher: null };
  writePrivateJson(files.job, job);
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), 'work', files.job, nonce], {
    cwd: here.root,
    detached: true,
    stdio: 'ignore',
    env,
  });
  child.unref();
  if (Number.isInteger(child.pid) && readJson(files.job)?.nonce === nonce) writePrivateJson(files.job, { ...job, watcher: child.pid });
  return { code: 0, lines: [runnerStarted(entry.appName, entry.desk)] };
}

export async function stop({ env = process.env, dataArg, cwd = process.cwd() } = {}) {
  const dataDir = pluginDataDir(env, dataArg);
  const here = await whereAmI(cwd);
  if (here === null) return { code: 0, lines: [NOT_A_REPO] };
  const files = filesOf(dataDir, here.repo);
  const current = readJson(files.job);
  if (current === null) return { code: 0, lines: [RUNNER_NOT_RUNNING] };
  // The worker leaves at its next tick, on finding the file gone.
  rmSync(files.job, { force: true });
  return { code: 0, lines: [Number.isInteger(current.watcher) && isAlive(current.watcher) ? RUNNER_STOPPED : RUNNER_NOT_RUNNING] };
}

// ---------------------------------------------------------------------------
// ALYDAS-127, S7 (Ask 79e3e335, runner-waits). Waiting at a hold, then
// resuming the session. The rules are lib/runner.mjs's.

// The run ledgers in a worktree: `<root>/boards/<date>-<slug>.json`, the root
// one or two folders down (`docs`, `docs/superpowers`), as the pipeline is
// told it with `--root`. Each with its events, `<ledger>.events.jsonl`.
function ledgersIn(worktree) {
  const dirs = (at) => {
    try {
      return readdirSync(at).filter((name) => !name.startsWith('.') && name !== 'node_modules' && statSync(join(at, name)).isDirectory());
    } catch {
      return [];
    }
  };
  const roots = dirs(worktree).flatMap((one) => [join(worktree, one), ...dirs(join(worktree, one)).map((two) => join(worktree, one, two))]);
  const found = [];
  for (const root of roots) {
    let names = [];
    try {
      names = readdirSync(join(root, 'boards')).filter((name) => name.endsWith('.json')).sort();
    } catch {
      continue;
    }
    for (const name of names) found.push({ path: join(root, 'boards', name), run: readJson(join(root, 'boards', name)) });
  }
  return found;
}

function eventsOf(ledgerPath) {
  try {
    return readFileSync(`${ledgerPath.slice(0, -'.json'.length)}.events.jsonl`, 'utf8')
      .split('\n')
      .filter((line) => line.trim() !== '')
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      });
  } catch {
    return [];
  }
}

const heldKey = (held) => (held === null ? null : `${held.slug}:${held.n}:${held.attempt}`);

// A child in the foreground, its output on ours. With `until`, ended at that
// instant: answers whether it was.
function foreground(command, args, { cwd, env, until = null }) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env, stdio: ['ignore', 'inherit', 'inherit'] });
    let cut = false;
    const left = until === null ? null : until.getTime() - Date.now();
    const timer =
      left === null
        ? null
        : setTimeout(() => {
            cut = true;
            child.kill('SIGTERM');
          }, Math.max(0, left));
    child.once('error', () => {
      if (timer) clearTimeout(timer);
      resolve({ code: null, cut });
    });
    child.once('exit', (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, cut });
    });
  });
}

// The session ended. While its run's ledger says a phase waits on a hold:
// wait at it with the pipeline's own `hold` (the same token, read from
// ALYDASH_CREDENTIALS, and the same run, the ledger's slug, in the same
// session, CLAUDE_CODE_SESSION_ID), then resume the session with what was
// answered. Ends when the session ends at no hold, when a hold ends with no
// answer (a stop, or the Ask expired), or at `until`, with a line on the run.
//
// Gate 8 fix (G8C-4). With `outcome`, how it ended is filed there, with this
// process's pid, before it returns: the worker reads it to tell a carry that
// ended (nothing follows) from one that died at the hold (it waits again).
export async function carry({ worktree, session, until = null, env = process.env, outcome = null }) {
  const lines = [];
  const holdEnv = { ...env, CLAUDE_CODE_SESSION_ID: session };
  let last = null;
  const ends = (ended, line) => {
    if (outcome !== null) {
      try {
        writePrivateJson(outcome, { pid: process.pid, ended, at: Date.now() });
      } catch {}
    }
    return { code: 0, lines: [...lines, line] };
  };
  for (;;) {
    const held = heldPhase(ledgersIn(worktree));
    if (held === null) return ends('no-hold', 'The session ended at no hold, so there is nothing to wait for.');
    if (heldKey(held) === last) {
      return ends('held-again', `Phase ${held.n} is held again on the same question after the session resumed, so the runner stops here.`);
    }
    last = heldKey(held);
    const pipeline = [PIPELINE_SCRIPT, '--root', held.root];
    const waited = await foreground('python3', [...pipeline, 'hold', held.slug, String(held.n)], { cwd: worktree, env: holdEnv, until });
    if (waited.cut) {
      await foreground('python3', [...pipeline, 'log', held.slug, outOfTime(held.n), '--phase', String(held.n)], { cwd: worktree, env: holdEnv });
      return ends('out-of-time', outOfTime(held.n));
    }
    const ledger = ledgersIn(worktree).find((one) => one.run?.slug === held.slug) ?? null;
    const phase = ledger?.run?.phases?.find((one) => one?.n === held.n) ?? null;
    const outcome = holdOutcome(phase);
    if (outcome === 'unanswered') {
      return ends('unanswered', `The hold on phase ${held.n} ended with no answer to carry on with, so the session is not resumed.`);
    }
    const prompt = answerPrompt(outcome, held.n, lastDecision(eventsOf(ledger.path), held.n));
    lines.push(`Phase ${held.n} was answered on the desk; resuming session ${session}.`);
    process.stdout.write(`${lines.at(-1)}\n`);
    await foreground('claude', resumeArgs(prompt, session), { cwd: worktree, env });
  }
}

// ---------------------------------------------------------------------------
// The worker.

function logLine(files, line) {
  try {
    appendFileSync(files.log, `${new Date().toISOString()} ${String(line).split('\n')[0].slice(0, 300)}\n`, { mode: 0o600 });
  } catch {}
}

const errorText = (error) => (error instanceof Error ? error.message : 'unknown error');

// A worktree beside the repository at origin's default branch, detached: the
// session makes its own branch.
async function makeWorktree(root, path) {
  await git(root, ['fetch', '--quiet', 'origin']);
  const head = await git(root, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], 5_000);
  let base = head.ok && head.out !== '' ? head.out : null;
  for (const candidate of base === null ? ['origin/main', 'origin/master'] : []) {
    if ((await git(root, ['rev-parse', '--verify', '--quiet', candidate], 5_000)).ok) {
      base = candidate;
      break;
    }
  }
  if (base === null) throw new Error("origin's default branch could not be found");
  const made = await git(root, ['worktree', 'add', '--detach', '--quiet', path, base]);
  if (!made.ok) throw new Error(`git worktree add failed: ${made.err.split('\n')[0]}`);
}

// Gate 8 fix (G8C-5). A start's worktree a worker began and never finished
// setting up: taken off the repository's list, and the folder removed. Only
// ever a path `worktreePath` named for that start.
async function clearWorktree(root, path) {
  await git(root, ['worktree', 'remove', '--force', '--force', path]);
  rmSync(path, { recursive: true, force: true });
  await git(root, ['worktree', 'prune']);
}

async function acknowledge(entry, startId, began) {
  try {
    const reply = await postJson(`${entry.desk}${CLAIMED_PATH}`, { start: startId, ref: began.ref, session: began.session }, { token: entry.token });
    return reply.status;
  } catch {
    return null;
  }
}

async function work(jobFile, nonce) {
  const job = readJson(jobFile);
  if (job === null || job.nonce !== nonce) return;
  const files = filesOf(job.dataDir, job.repo);
  ensurePrivateDir(files.logs);
  const credentials = join(job.dataDir, CREDENTIALS_FILE);
  const ours = () => readJson(jobFile)?.nonce === nonce;
  const seenOf = () => readJson(files.starts) ?? {};
  const file = (id, began) => writePrivateJson(files.starts, { ...seenOf(), [id]: began });

  // Gate 6 fix. What the desk answered an acknowledgement with, filed. A 409
  // is the desk saying the start is no longer this token's (its claim went
  // back to the queue, or ran past its hour, before the ack reached it): the
  // session begun for it is ended, and the start is done here, so a second
  // delivery begins nothing and a person's new press is the only run.
  const acked = (id, began, status) => {
    if (status !== 409) return file(id, { ...began, acked: status !== null, ackStatus: status });
    if (Number.isInteger(began.pid) && began.pid > 0) {
      try {
        process.kill(-began.pid, 'SIGTERM');
      } catch {
        try {
          process.kill(began.pid, 'SIGTERM');
        } catch {}
      }
    }
    file(id, { ...began, acked: true, ackStatus: status, done: true });
    logLine(files, `ended the session for ${id}: the desk refused its acknowledgement, so the start is no longer this runner's`);
  };

  // The live entry for this repository on the job's desk, read fresh each time.
  const entryNow = () => {
    const entry = pickCredential(job.dataDir, job.repo, { desk: job.desk });
    return isLive(entry) && holdsRunnerScope(entry) ? entry : null;
  };

  // Every tick: leave when the job is no longer ours, stop each session this
  // repository's workers began whose run has ended, and (S7) wait at the hold
  // of each that ended at one. One tick at a time, so nothing is begun twice.
  let leaving = false;
  let ticking = false;
  const tick = setInterval(async () => {
    if (!ours()) {
      leaving = true;
      clearInterval(tick);
      process.exit(0);
    }
    if (ticking) return;
    ticking = true;
    try {
      for (const [id, began] of Object.entries(seenOf())) {
        if (!began || !Number.isInteger(began.pid) || began.done) continue;
        const running = isAlive(began.pid) && (await startedOf(began.pid)) === began.started;
        if (!running) {
          await afterSession(id, began);
          continue;
        }
        if (!runEnded(readJson(join(job.dataDir, 'sessions', `${began.session}.json`)))) continue;
        try {
          process.kill(-began.pid, 'SIGTERM');
        } catch {
          try {
            process.kill(began.pid, 'SIGTERM');
          } catch {}
        }
        file(id, { ...began, done: true });
        logLine(files, `stopped the session for ${id}: its run ended on the desk`);
      }
    } finally {
      ticking = false;
    }
  }, RUNNER_TICK_MS);

  // S7. A session's process ended (or the `carry` that waited for it). What
  // it left running in its process group, a hold it ran in the background
  // among them, ends with it: the hold is resumable, on the Ask it recorded.
  // If its run waits on a hold, a `carry` waits there and resumes the session;
  // else the start is done.
  //
  // Gate 8 fix (G8C-4, W7). One `carry` a held session, for as long as it
  // runs: it waits at every hold the session reaches, a send-back's next one
  // among them. A carry that ended filed how (`--outcome`, with its pid): a
  // stop, an Ask that expired, its time up, or no hold left, and nothing
  // follows it. One that filed nothing died at the hold (a reboot, a kill),
  // and while the hold is still open it is started again, once in this
  // worker's life, so an answer given later still resumes the session.
  const revived = new Set();
  const outcomeFile = (id) => join(files.logs, `${id}.carry.json`);
  async function afterSession(id, began) {
    try {
      process.kill(-began.pid, 'SIGTERM');
    } catch {}
    const held = typeof began.worktree === 'string' && typeof began.session === 'string' ? heldPhase(ledgersIn(began.worktree)) : null;
    if (held === null) {
      await endDiedSession(id, began);
      file(id, { ...began, done: true });
      logLine(files, `the session for ${id} ended`);
      return;
    }
    if (began.carried) {
      const said = readJson(outcomeFile(id));
      if (said !== null && said.pid === began.pid) {
        file(id, { ...began, done: true });
        logLine(files, `the session for ${id} ended, and the wait at the hold on phase ${held.n} ended (${String(said.ended)})`);
        return;
      }
      if (revived.has(id)) {
        file(id, { ...began, done: true });
        logLine(files, `the wait at the hold on phase ${held.n} for ${id} died again, so it is not started again`);
        return;
      }
      revived.add(id);
      logLine(files, `the wait at the hold on phase ${held.n} for ${id} ended with no outcome filed, so it is started again`);
    }
    rmSync(outcomeFile(id), { force: true });
    const out = openSync(join(files.logs, `${id}.log`), 'a', 0o600);
    let child;
    try {
      child = spawn(process.execPath, [fileURLToPath(import.meta.url), 'carry', began.worktree, began.session, '--outcome', outcomeFile(id)], {
        cwd: began.worktree,
        env: claudeEnv(process.env, { credentials, start: { id, item: began.item ?? '', budgetUsd: began.budgetUsd ?? null } }),
        detached: true,
        stdio: ['ignore', out, out],
      });
    } finally {
      closeSync(out);
    }
    const spawned = await new Promise((resolve) => {
      child.once('spawn', () => resolve(true));
      child.once('error', () => resolve(false));
    });
    if (!spawned) {
      logLine(files, `could not wait at the hold on phase ${held.n} for ${id}`);
      return;
    }
    child.unref();
    file(id, { ...began, pid: child.pid, started: await startedOf(child.pid), carried: heldKey(held) });
    logLine(files, `waiting at the hold on phase ${held.n} for ${id}, to resume session ${began.session}`);
  }

  // ALYDAS-523 (design D3). The ended beat a session that exited with no
  // SessionEnd never sent. Each step in its own try: whatever fails, the
  // start is still done.
  async function endDiedSession(id, began) {
    if (typeof began.session !== 'string' || !SESSION_ID.test(began.session)) return;
    const sessions = join(job.dataDir, 'sessions');
    const stateFile = join(sessions, `${began.session}.json`);
    try {
      if (existsSync(join(sessions, `${began.session}.end.json`))) return;
      const state = readJson(stateFile);
      if (state === null || state.sessionId !== began.session) return;
      const told = Number(readJson(join(job.dataDir, 'told.json'))?.sends) >= SENDS_VERSION;
      const facts =
        told && typeof began.worktree === 'string'
          ? await gitFacts(began.worktree, (dir, args) => git(dir, args, 3_000).then((said) => (said.ok ? said.out : null)), {
              startHead: typeof state.startHead === 'string' ? state.startHead : null,
              // Gate 8 (G8W2): as the hook reads it.
              startOrigin: typeof state.startOrigin === 'string' ? state.startOrigin : null,
              since: Number.isFinite(state.startedAt) ? state.startedAt : null,
            })
          : null;
      const body = diedBeat(state, facts, id);
      if (body === null) return;
      const entry = pickCredential(job.dataDir, job.repo, { desk: job.desk });
      if (!isLive(entry) || !sendsHeartbeat(entry)) return;
      const reply = await postJson(`${entry.desk}${HEARTBEAT_PATH}`, body, { token: entry.token }).catch(() => null);
      logLine(files, `the session for ${id} exited with no SessionEnd, so the runner sent its ended beat (${reply === null ? 'it did not reach the desk' : reply.status})`);
      rmSync(stateFile, { force: true });
      rmSync(join(sessions, `${began.session}.watch.json`), { force: true });
    } catch (error) {
      logLine(files, `could not end the session for ${id} on the desk: ${errorText(error)}`);
    }
  }

  while (!leaving && ours()) {
    const entry = entryNow();
    if (entry === null) {
      logLine(files, 'no live connection with claim:RunStart for this repository; the runner stopped');
      break;
    }
    // Acknowledgements the desk did not take yet are sent again. Gate 8 fix
    // (G8C-5): only of a start that was begun, with its session; one filed
    // with none was never begun, and is begun when the desk hands it back.
    for (const [id, began] of Object.entries(seenOf())) {
      if (began && began.acked === false && typeof began.session === 'string') {
        const status = await acknowledge(entry, id, began);
        if (status !== null) acked(id, began, status);
      }
    }
    const asked = Date.now();
    let reply = null;
    try {
      reply = await postJson(`${entry.desk}${CLAIM_PATH}`, { wait: CLAIM_HOLD_S }, { token: entry.token, timeoutMs: CLAIM_TIMEOUT_MS });
    } catch (error) {
      logLine(files, `the claim did not reach the desk: ${errorText(error)}`);
      await sleep(CLAIM_BACKOFF_MS);
      continue;
    }
    if (claimEnds(reply)) {
      logLine(files, `the desk refused the claim (${reply.status}): ${typeof reply.json?.error === 'string' ? reply.json.error : 'no reason given'}`);
      break;
    }
    const taken = reply.status === 200 ? startOf(reply.json) : null;
    if (taken !== null) await take(entry, taken);
    else if (reply.status !== 204) logLine(files, `the desk answered the claim ${reply.status}`);
    const spent = Date.now() - asked;
    if (taken === null && spent < CLAIM_MIN_MS) await sleep(CLAIM_MIN_MS - spent);
  }
  clearInterval(tick);
  if (ours()) rmSync(jobFile, { force: true });

  async function take(entry, taken) {
    const seen = seenOf();
    // Gate 8 fix (G8C-5, W3). Filed with no session: a worker stopped or
    // killed while it set this start up, before claude ran. It was never
    // begun, so it is begun now, in place of any worktree half made for it.
    const unbegun = !firstDelivery(seen, taken.id) && typeof seen[taken.id]?.session !== 'string';
    if (!firstDelivery(seen, taken.id) && !unbegun) {
      // Delivered again: the desk did not hear the acknowledgement in time.
      const began = seen[taken.id];
      // One the desk refused the acknowledgement of was ended: nothing to say.
      if (began?.session && began.ackStatus !== 409) acked(taken.id, began, await acknowledge(entry, taken.id, began));
      return;
    }
    // ALYDAS-127, S7 (Ask 79e3e335). A held session is resumed in place, by
    // `carry`, so the desk queues no resume; a row of another kind is left.
    if (taken.kind !== 'start') {
      logLine(files, `left ${taken.id}: this runner does not resume a session yet`);
      return;
    }
    // Filed before anything runs, so a second delivery while this one is
    // being set up begins nothing.
    file(taken.id, { session: null, ref: null, pid: null, acked: false, at: Date.now() });
    if (unbegun) logLine(files, `${taken.id} was filed but never begun (the worker stopped while setting it up), so it is begun now`);
    let path;
    try {
      path = worktreePath(job.root, taken.id);
      if (unbegun && existsSync(path)) await clearWorktree(job.root, path);
      if (existsSync(path)) throw new Error(`${path} is there already`);
      await makeWorktree(job.root, path).catch(async (error) => {
        await clearWorktree(job.root, path);
        throw error;
      });
    } catch (error) {
      logLine(files, `could not make the worktree for ${taken.id}: ${errorText(error)}`);
      // Forgotten, so the start is tried again when the desk hands it back.
      const { [taken.id]: _dropped, ...rest } = seenOf();
      writePrivateJson(files.starts, rest);
      return;
    }
    const session = randomUUID();
    const out = openSync(join(files.logs, `${taken.id}.log`), 'a', 0o600);
    let child;
    try {
      child = spawn('claude', claudeArgs(taken.prompt, session), {
        cwd: path,
        env: claudeEnv(process.env, { credentials, start: taken }),
        detached: true,
        stdio: ['ignore', out, out],
      });
    } finally {
      closeSync(out);
    }
    const spawned = await new Promise((resolve) => {
      child.once('spawn', () => resolve(true));
      child.once('error', (error) => {
        logLine(files, `could not start claude for ${taken.id}: ${errorText(error)}`);
        resolve(false);
      });
    });
    if (!spawned) {
      const { [taken.id]: _dropped, ...rest } = seenOf();
      writePrivateJson(files.starts, rest);
      return;
    }
    child.unref();
    const began = {
      session,
      ref: `pid-${child.pid}`,
      pid: child.pid,
      started: await startedOf(child.pid),
      worktree: path,
      item: taken.item,
      budgetUsd: taken.budgetUsd,
      acked: false,
      at: Date.now(),
    };
    file(taken.id, began);
    logLine(files, `began ${taken.id} as session ${session} in ${path}`);
    acked(taken.id, began, await acknowledge(entry, taken.id, began));
  }
}

function dataArgOf(argv) {
  const at = argv.indexOf('--data');
  return at === -1 ? undefined : argv[at + 1];
}

async function main(argv) {
  try {
    if (argv[0] === 'work' && argv[1] && argv[2]) {
      await work(argv[1], argv[2]);
      return 0;
    }
    if (argv[0] === 'carry' && argv[1] && argv[2]) {
      const at = argv.indexOf('--until');
      const until = at === -1 ? null : new Date(argv[at + 1]);
      if (until !== null && Number.isNaN(until.getTime())) throw new Error('--until names no instant.');
      const filed = argv.indexOf('--outcome');
      const outcome = filed === -1 || !argv[filed + 1] ? null : argv[filed + 1];
      const { code, lines } = await carry({ worktree: argv[1], session: argv[2], until, outcome });
      for (const line of lines) process.stdout.write(`${line}\n`);
      return code;
    }
    const run = argv[0] === 'stop' ? stop : start;
    const { code, lines } = await run({ dataArg: dataArgOf(argv) });
    for (const line of lines) process.stdout.write(`${line}\n`);
    return code;
  } catch (error) {
    process.stdout.write(`${errorText(error)}\n`);
    return 1;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const code = await main(process.argv.slice(2));
  await new Promise((resolve) => process.stdout.write('', resolve));
  process.exit(code);
}
