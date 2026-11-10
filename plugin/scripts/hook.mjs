// ALYDAS-18, S4, design §3. The one script behind all five hooks in
// hooks/hooks.json. The decisions are lib/session.mjs's; this is the I/O:
//
//   node hook.mjs               a hook: the event's JSON on stdin, the hook's
//                               JSON (or nothing) on stdout
//   node hook.mjs --send FILE   the detached ended beat, which outlives
//                               Claude Code's cancel of a SessionEnd hook
//                               (probe Q4)
//   node hook.mjs --watch FILE NONCE
//                               ALYDAS-263: the session's watcher, one per
//                               session, which pulses while the Claude Code
//                               process lives and exits when it is gone
//                               (lib/watch.mjs)
//
// It always exits 0, so a desk that is down never holds up a session. Every
// error is one line on stderr, and no line it writes carries the token or a
// header. The token is read from credentials.json and sent only to the desk
// that entry names; no other file ever holds it.
//
// Gate 8: a prompt or a session start also tells the person how a
// `/alydash:connect` in this repository ended, once (connect.mjs
// takeDecidedLines), and a session start says when the connection is gone
// (an expired entry here, or a 401 in session.mjs).
//
// ALYDAS-265. It reads two more things and sends neither: the prompt, of
// which lib/session.mjs keeps the first line and nothing else, and the
// session's transcript, of which lib/usage.mjs keeps sums. A laptop is told
// once what a connected session sends (`SENDS_NOTICE`), and no prompt line is
// sent before it has been.
//
// ALYDAS-379, S2. A credential for a desk built without runs (its `scopes`
// lack the heartbeat's, lib/credentials.mjs sendsHeartbeat) gets no beat from
// any hook, the detached sender or the watcher, and so no refusal line at
// every prompt. Everything else a hook says (setup, a decided connect, a gone
// connection) and the session's CLAUDE_ENV_FILE line stay. Nothing is sent,
// so the laptop is not told what is sent, and no session file or watcher is
// kept.

import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync, existsSync, linkSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { takeDecidedLines } from './connect.mjs';
import {
  CREDENTIALS_FILE,
  ensurePrivateDir,
  hasWork,
  isLive,
  isRevoked,
  markRevoked,
  pickCredential,
  sendsHeartbeat,
  writePrivateJson,
} from './lib/credentials.mjs';
import { connectionOf, deskUrl, postJson, probeConnection } from './lib/desk.mjs';
import { repoOf } from './lib/repo.mjs';
import { notSetUp, notSetUpNotice } from './lib/setup-state.mjs';
import {
  DISCONNECTED_NOTICE,
  HOOK_EVENTS,
  accessOffNotice,
  SENDS_NOTICE,
  SENDS_VERSION,
  applyReply,
  decide,
  endedBeat,
  itemRetry,
  switchedOffSentence,
} from './lib/session.mjs';
import { readUsage } from './lib/usage.mjs';
import {
  WATCH_TICK_MS,
  needsWatcher,
  pulseBody,
  pulseEnds,
  sessionProcess,
  startedOf,
  watchStep,
} from './lib/watch.mjs';

const EVENTS = new Set(HOOK_EVENTS);
// The hooks whose systemMessage the person sees before their next turn: where
// a decided connect is announced.
const ANNOUNCES = new Set(['SessionStart', 'UserPromptSubmit']);
// A SessionStart that continues the same session, which has been told already.
const SAME_SESSION = new Set(['compact', 'clear']);
// A session id names a file, so it is held to what Claude Code writes (a UUID).
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
// ALYDAS-265. Where a laptop keeps that it was told what is sent.
const TOLD_FILE = 'told.json';
// How many repositories the laptop remembers having been told are not set up.
const SETUP_TOLD_MAX = 50;

const say = (line) => process.stderr.write(`alydash: ${String(line).split('\n')[0].slice(0, 300)}\n`);

function heartbeatUrl(desk) {
  return `${desk}/api/sessions/heartbeat`;
}

// The live credential for this repository. With ALYDASH_URL set it must be
// for that desk exactly; unset, the entry's own desk is used. Either way the
// token goes only to the desk the entry names.
const pinnedDesk = (env) => ((env.ALYDASH_URL ?? '').trim() !== '' ? deskUrl(env) : null);

// ALYDAS-387. Unpinned, a repository connected to two desks (one with runs,
// one without) beats to a desk that takes the beat. ALYDAS-445 (CR-1): the
// choice is lib/credentials.mjs pickCredential, which setup and status make
// too, so the hook never sends a person to a setup that reads another desk.
// Null when nothing live is there: `picked` is then the entry that ran out,
// if any.
function liveCredential(dataDir, repo, desk) {
  const picked = pickCredential(dataDir, repo, { desk });
  return { picked, entry: isLive(picked) ? picked : null };
}

// ALYDAS-450. What the desk says of this entry's token, asked once (lib/
// desk.mjs probeConnection): 'revoked' for a 401, 'switched-off' for agent
// access switched off for the app (Gate 8, W4), and so on; a desk that cannot
// be reached is one line on stderr and reads as 'unknown'. Gate 8 (W3): what
// the desk said is recorded on the entry (heard), so a live entry on another
// desk is picked ahead of a revoked one.
async function connectionOnDesk(entry, heard) {
  try {
    const said = connectionOf(await probeConnection(entry.desk, entry.token));
    heard(said);
    return said;
  } catch (error) {
    say(`could not ask the desk whether this connection is live: ${error instanceof Error ? error.message : 'unknown error'}`);
    return 'unknown';
  }
}

function git(cwd, args) {
  return new Promise((resolve) => {
    execFile('git', ['-C', cwd, ...args], { timeout: 3000 }, (error, stdout) => {
      const text = String(stdout ?? '').trim();
      resolve(error || text === '' ? null : text);
    });
  });
}

// The branch. `symbolic-ref` first, because `rev-parse --abbrev-ref HEAD`
// fails on a branch with no commit yet (measured: a fresh `git init`); then
// `rev-parse`, which says `HEAD` when detached.
async function branchOf(cwd) {
  return (await git(cwd, ['symbolic-ref', '--short', '-q', 'HEAD'])) ?? git(cwd, ['rev-parse', '--abbrev-ref', 'HEAD']);
}

// told.json as an object, whatever is in it: the two things a laptop is told
// once (`sends`, `setup`) are written back together, neither over the other.
function toldOf(toldFile) {
  const read = readJson(toldFile);
  return read !== null && typeof read === 'object' && !Array.isArray(read) ? read : {};
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

async function post(desk, token, body) {
  try {
    return await postJson(heartbeatUrl(desk), body, { token });
  } catch (error) {
    say(`the heartbeat did not reach the desk: ${error instanceof Error ? error.message : 'unknown error'}`);
    return null;
  }
}

function reportRefusal(answer) {
  if (!answer || (answer.status >= 200 && answer.status < 300)) return;
  const reason = typeof answer.json?.error === 'string' ? answer.json.error : 'no reason given';
  say(`the desk refused the heartbeat (${answer.status}): ${reason}`);
}

// The detached ended beat. The file names the desk and repository, never the
// token, which is read again from credentials.json here.
function spawnSender(sendFile) {
  spawn(process.execPath, [fileURLToPath(import.meta.url), '--send', sendFile], {
    detached: true,
    stdio: 'ignore',
  }).unref();
}

async function send(sendFile) {
  const job = readJson(sendFile);
  rmSync(sendFile, { force: true });
  if (!job) return;
  const { entry } = liveCredential(job.dataDir, job.repo, job.desk);
  if (!entry) return;
  // ALYDAS-379, S2. Read again here, so a credential replaced since the hook
  // by one for a desk without runs is sent nothing.
  if (sendsHeartbeat(entry)) reportRefusal(await post(entry.desk, entry.token, job.body));
  if (job.deleteState) rmSync(job.stateFile, { force: true });
}

// ALYDAS-263. Starts this session's watcher unless one is running. The watch
// file is the claim: it names the session's process, the watcher's nonce and,
// once started, its pid. It is made with a link, which fails if the file is
// there, so of two hooks at once (parallel tool calls) one starts a watcher
// and the other leaves. Like the sender's file, it names the desk and the
// repository and never the token.
async function ensureWatcher(watchFile, job) {
  const now = Date.now();
  if (!needsWatcher(readJson(watchFile), now)) return;
  const session = await sessionProcess();
  if (session === null) return;
  const nonce = randomUUID();
  const claim = { ...job, ...session, nonce, at: now, watcher: null };
  const temp = `${watchFile}.${nonce}.tmp`;
  rmSync(watchFile, { force: true });
  try {
    writePrivateJson(temp, claim);
    linkSync(temp, watchFile);
  } catch {
    return;
  } finally {
    rmSync(temp, { force: true });
  }
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--watch', watchFile, nonce], {
    detached: true,
    stdio: 'ignore',
  });
  child.unref();
  // The watcher's pid, so the next hook sees it running without asking `ps`.
  // Only while the claim is still this hook's.
  if (Number.isInteger(child.pid) && readJson(watchFile)?.nonce === nonce) {
    writePrivateJson(watchFile, { ...claim, watcher: child.pid });
  }
}

// The watcher. Every WATCH_TICK_MS it asks whether its session is still there
// (lib/watch.mjs watchStep) and once a minute it pulses; it exits within a
// tick of the session's process going, of a SessionEnd, or of another watcher
// taking its place, and takes its watch file with it if the file is still
// its own.
async function watch(watchFile, nonce) {
  let lastPulseAt = null;
  for (;;) {
    const job = readJson(watchFile);
    const state = job ? readJson(job.stateFile) : null;
    const step = watchStep({ job, nonce, state, now: Date.now(), lastPulseAt });
    if (step === 'exit') break;
    if (step === 'pulse') {
      // A pid that started at another time is another process.
      if ((await startedOf(job.pid)) !== job.started) break;
      const { entry } = liveCredential(job.dataDir, job.repo, job.desk);
      // ALYDAS-379, S2. A desk without runs has no run to keep alive.
      if (!entry || !sendsHeartbeat(entry)) break;
      if (pulseEnds(await post(entry.desk, entry.token, pulseBody(state)))) break;
      lastPulseAt = Date.now();
    }
    await new Promise((resolve) => setTimeout(resolve, WATCH_TICK_MS));
  }
  if (readJson(watchFile)?.nonce === nonce) rmSync(watchFile, { force: true });
}

// Gate 8, D5. The session's environment names where the credential is (a
// path only; the skills read the credential from it, S5). In its own try, so
// an env file that cannot be written never costs the session its card.
function exportCredentials(env, dataDir) {
  if ((env.CLAUDE_ENV_FILE ?? '') === '') return;
  try {
    const path = join(dataDir, CREDENTIALS_FILE).replaceAll("'", `'\\''`);
    appendFileSync(env.CLAUDE_ENV_FILE, `export ALYDASH_CREDENTIALS='${path}'\n`);
  } catch (error) {
    say(`could not write CLAUDE_ENV_FILE: ${error instanceof Error ? error.message : 'unknown error'}`);
  }
}

// The hook's JSON, with the notices put first in its systemMessage.
function print(output, notices) {
  const said = [...notices, ...(output?.systemMessage ? [output.systemMessage] : [])];
  const whole = said.length > 0 ? { ...(output ?? {}), systemMessage: said.join('\n') } : output;
  if (whole) process.stdout.write(`${JSON.stringify(whole)}\n`);
}

// A session's three files in sessions/: its state, an ended beat waiting to
// go, and its watcher's claim. Removed the one way wherever this session can
// no longer beat: nothing reads them again, and the watcher leaves at its
// next tick on finding its file gone. `sessionId` is held to SESSION_ID by the
// caller, so each path stays in sessions/. ALYDAS-388 c1 (scope lost), and
// ALYDAS-388 Gate 8 W2 (credential run out, removed, or its store gone).
function forgetSession(dataDir, sessionId) {
  const sessions = join(dataDir, 'sessions');
  for (const name of [`${sessionId}.json`, `${sessionId}.end.json`, `${sessionId}.watch.json`]) {
    rmSync(join(sessions, name), { force: true });
  }
}

async function hook(env) {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const event = input?.hook_event_name;
  if (!EVENTS.has(event)) return;
  const dataDir = (env.CLAUDE_PLUGIN_DATA ?? '').trim();
  if (dataDir === '') return;
  const hasCredentials = existsSync(join(dataDir, CREDENTIALS_FILE));
  const announces = ANNOUNCES.has(event) && existsSync(join(dataDir, 'connect'));
  const sessionId = String(input.session_id ?? '');
  if (!SESSION_ID.test(sessionId)) return;
  // ALYDAS-388 Gate 8 W2: a store removed mid-session leaves nothing to beat
  // with, so this session's files go too.
  if (!hasCredentials && !announces) {
    forgetSession(dataDir, sessionId);
    return;
  }
  const cwd = typeof input.cwd === 'string' && input.cwd !== '' ? input.cwd : process.cwd();

  const repo = await repoOf(cwd);
  if (repo === null) return;
  const notices = announces ? takeDecidedLines(dataDir, repo) : [];
  const { picked, entry } = hasCredentials ? liveCredential(dataDir, repo, pinnedDesk(env)) : { picked: null, entry: null };
  if (!entry) {
    // ALYDAS-388 Gate 8 W2: no live credential for this repository (run out,
    // removed, or none on a pinned desk), so this session cannot beat, on any
    // event and not only SessionEnd, as on the scope-loss path below.
    forgetSession(dataDir, sessionId);
    // Gate 8, COR-7: the entry for this repository has run out, so this
    // session will not show. Said when the session starts.
    if (
      event === 'SessionStart' &&
      !SAME_SESSION.has(input.source) &&
      picked !== null
    ) {
      notices.push(DISCONNECTED_NOTICE);
    }
    print(null, notices);
    return;
  }

  // ALYDAS-265. Whether this laptop has been told what a connected session
  // sends. The hook that tells it sends no prompt line itself: the line
  // starts with the next prompt, after the notice has been on screen.
  const toldFile = join(dataDir, TOLD_FILE);
  const told = Number(readJson(toldFile)?.sends) >= SENDS_VERSION;
  const beats = sendsHeartbeat(entry);
  if (beats && !told && ANNOUNCES.has(event)) {
    try {
      writePrivateJson(toldFile, { ...toldOf(toldFile), sends: SENDS_VERSION });
      notices.push(SENDS_NOTICE);
    } catch (error) {
      say(`could not record what this laptop was told: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  }

  // ALYDAS-313's follow-up. A connected repository that lacks what
  // /alydash:setup installs is told so when a session starts in it, once for
  // the repository: connecting puts sessions on Now and nothing more, and an
  // empty Timeline is otherwise the first anyone hears of it.
  // ALYDAS-385: not on a desk without Work, which has no board for setup to
  // point the repository at (`hasWork`, read off the scopes connect filed).
  if (event === 'SessionStart' && !SAME_SESSION.has(input.source) && hasWork(entry)) {
    try {
      const before = toldOf(toldFile);
      const named = Array.isArray(before.setup) ? before.setup.filter((one) => typeof one === 'string') : [];
      const key = repo.toLowerCase();
      const root = named.includes(key) ? null : await git(cwd, ['rev-parse', '--show-toplevel']);
      const missing = root === null ? [] : notSetUp(root, entry.desk);
      if (missing.length > 0) {
        writePrivateJson(toldFile, { ...toldOf(toldFile), setup: [...named, key].slice(-SETUP_TOLD_MAX) });
        notices.push(notSetUpNotice(missing));
      }
    } catch (error) {
      say(`could not check this repository's setup: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  }

  // Gate 8 (W3). What the desk said of this entry's token, kept on the entry:
  // revoked, or taken again. In its own try, as a store that cannot be
  // written costs only the ranking.
  const recordRevoke = (said) => {
    if (said !== 'revoked' && !(said === 'live' && isRevoked(entry))) return;
    try {
      markRevoked(dataDir, entry, said === 'revoked' ? new Date() : null);
    } catch (error) {
      say(`could not record what the desk said of this connection: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  };

  // ALYDAS-379, S2. A desk without runs: no beat, no session file, no watcher.
  // ALYDAS-388: and none left behind by a session that beat before its
  // credential was replaced by one without the heartbeat's scope.
  if (!beats) {
    forgetSession(dataDir, sessionId);
    // ALYDAS-450. No beat goes to this desk, so no 401 ever said a revoked
    // token: a session start asks, once, and says so as the 401 to a beat
    // does.
    if (event === 'SessionStart' && !SAME_SESSION.has(input.source)) {
      const said = await connectionOnDesk(entry, recordRevoke);
      if (said === 'revoked') notices.push(DISCONNECTED_NOTICE);
      else if (said === 'switched-off') notices.push(accessOffNotice(entry.appName));
    }
    if (event === 'SessionStart') exportCredentials(env, dataDir);
    print(null, notices);
    return;
  }

  const sessions = join(dataDir, 'sessions');
  ensurePrivateDir(sessions);
  const stateFile = join(sessions, `${sessionId}.json`);
  const sendFile = join(sessions, `${sessionId}.end.json`);
  const watchFile = join(sessions, `${sessionId}.watch.json`);

  const before = readJson(stateFile);
  // The session's tokens and cost, from its transcript, in its own try: a
  // transcript that cannot be read costs the session its numbers and no more.
  let usage = null;
  try {
    usage = readUsage(input.transcript_path, before?.sessionId === sessionId ? before.usage : null);
  } catch (error) {
    say(`could not read the session's usage: ${error instanceof Error ? error.message : 'unknown error'}`);
  }

  const now = Date.now();
  // ALYDAS-451, F11: `work`, so a desk without Work is sent no item.
  const decided = decide(event, { ...input, branch: await branchOf(cwd), usage, told, work: hasWork(entry) }, before, now, {
    ALYDASH_WORK_ITEM: env.ALYDASH_WORK_ITEM,
    ALYDASH_SEND_PROMPT: env.ALYDASH_SEND_PROMPT,
    // ALYDAS-127, S5: the desk's start, which the laptop runner sets.
    ALYDASH_RUN_START: env.ALYDASH_RUN_START,
  });
  let state = decided.state;
  if (state === null) {
    print(null, notices);
    return;
  }

  const detach = (body, deleteState) => {
    writePrivateJson(sendFile, { dataDir, desk: entry.desk, repo, stateFile, deleteState, body });
    spawnSender(sendFile);
  };

  if (event === 'SessionEnd') {
    // Written first, then handed to a detached child: Claude Code cancels a
    // SessionEnd hook that takes more than a moment, and this exits at once.
    writePrivateJson(stateFile, state);
    // ALYDAS-263. The watcher leaves at its next tick, on finding no file.
    rmSync(watchFile, { force: true });
    if (decided.beat) detach(decided.beat, true);
    else rmSync(stateFile, { force: true });
    return;
  }

  let reply = null;
  if (decided.beat) {
    reply = await post(entry.desk, entry.token, decided.beat);
    const retry = itemRetry(decided.beat, reply);
    if (retry) {
      const again = await post(entry.desk, entry.token, retry);
      reply = again && { ...again, droppedItem: decided.beat.workItem };
    }
    reportRefusal(reply);
    // Gate 8 (W3). A 401 that is not the switch is the desk no longer taking
    // the token; a beat it takes is the token taken.
    if (reply?.status === 401 && switchedOffSentence(reply) === null) recordRevoke('revoked');
    else if (reply !== null && reply.status >= 200 && reply.status < 300) recordRevoke('live');
  }
  const applied = applyReply(event, reply, state, { appName: entry.appName });
  state = applied.state;
  writePrivateJson(stateFile, state);
  if (applied.followUp === 'ended-beat') detach(endedBeat(state), false);

  // Gate 8, D5: after the beat.
  if (event === 'SessionStart') exportCredentials(env, dataDir);
  print(applied.output, notices);

  // ALYDAS-263. From any hook, so a session that began before the plugin was
  // updated, or whose watcher died, is watched from its next one. Last, after
  // the hook has printed, and in its own try: a watcher that cannot start
  // costs the session nothing but its Idle.
  if (!state.stopped && !state.capped && state.lastBeatAt !== null) {
    try {
      await ensureWatcher(watchFile, { dataDir, desk: entry.desk, repo, stateFile });
    } catch (error) {
      say(`could not start the session's watcher: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  }
}

async function main(argv) {
  try {
    if (argv[0] === '--send' && argv[1]) await send(argv[1]);
    else if (argv[0] === '--watch' && argv[1] && argv[2]) await watch(argv[1], argv[2]);
    else await hook(process.env);
  } catch (error) {
    say(error instanceof Error ? error.message : 'the hook failed');
  }
  // A pipe on macOS is written asynchronously, so the lines are flushed before
  // the exit below.
  await new Promise((resolve) => process.stdout.write('', () => process.stderr.write('', resolve)));
  return 0;
}

// Exits as soon as it is done: fetch's keep-alive would otherwise hold the
// hook open.
process.exit(await main(process.argv.slice(2)));
