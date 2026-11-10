// ALYDAS-263. The session's pulse: what lets the desk tell a session waiting
// at its prompt from one whose process is gone. Claude Code has no hook that
// fires while a session idles (its `idle_prompt` notification fires once, a
// minute in), so a hook starts one small detached process per session
// (`hook.mjs --watch`), which tells the desk the session is still there once
// a minute for as long as the Claude Code process lives, and exits when it is
// not. The desk reads a watched run that has gone silent as Gone
// (`GONE_MINUTES` in packages/modules/runs/src/run-presence.ts).
//
// This file is the rules, with the two questions it asks the system (is this
// pid alive, and what is it) kept small; `hook.mjs` is the loop.
//
// How the watcher finds out its session is gone, checked every WATCH_TICK_MS:
//
//   1. the watch file no longer names it (a SessionEnd removed the file, or a
//      later hook started another watcher);
//   2. the session's process is not there (`kill(pid, 0)`);
//   3. the session's state file is gone, or says stopped or turned away;
//
// and before each pulse: the pid still started when it did (a reused pid is
// another process), and the credential is still there. The desk answering
// that the run ended, or refusing the token, ends it too. It holds no token:
// each pulse reads credentials.json again, as the ended beat's sender does.

import { execFile } from 'node:child_process';
import { basename } from 'node:path';
import { AGENT, statusLine } from './session.mjs';

export const WATCH_TICK_MS = 5_000;
// One pulse a minute. tests/plugin/watch.test.ts pins this against core's
// GONE_MINUTES: a killed session reads Gone inside five minutes.
export const PULSE_MS = 60_000;
// A watch file with no watcher's pid in it yet is a hook still starting one,
// for this long.
export const STARTING_MS = 10_000;

// What runs a hook's command, between Claude Code and the hook: `sh -c`, or
// whatever shell stands in for it.
const SHELLS = new Set(['sh', 'bash', 'zsh', 'dash', 'ash', 'ksh', 'fish', 'csh', 'tcsh']);
// How far up the hook looks for the session's process.
const ANCESTORS_MAX = 6;

// Whether a process with this pid exists. EPERM is one that does, owned by
// somebody else.
export function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === 'EPERM';
  }
}

function ps(args) {
  return new Promise((resolve) => {
    // A second each: the walk is at most a few of these, inside a hook that
    // has ten.
    execFile('ps', args, { timeout: 1000 }, (error, stdout) => {
      const text = String(stdout ?? '').trim();
      resolve(error || text === '' ? null : text);
    });
  });
}

// When a process started, in `ps`'s own words, or null when it is not there
// (or there is no `ps`, as on Windows). Compared, never parsed.
export function startedOf(pid) {
  return ps(['-o', 'lstart=', '-p', String(pid)]);
}

// One line of `ps -o ppid=,comm=`: the parent's pid and the command's name,
// without its path or a login shell's leading dash.
export function parseProcess(line) {
  const found = /^\s*(\d+)\s+(.+)$/.exec(String(line ?? ''));
  if (!found) return null;
  return { ppid: Number(found[1]), name: basename(found[2].trim()).replace(/^-/, '') };
}

// The process this session lives in: the nearest ancestor of the hook that is
// not a shell, which is Claude Code, since it runs a hook as `sh -c <command>`.
// `{ pid, started }`, or null when it cannot be told, and then nothing is
// watched and the session reads as it did before this file.
export async function sessionProcess(from = process.ppid, lookup = (pid) => ps(['-o', 'ppid=,comm=', '-p', String(pid)])) {
  let pid = from;
  for (let step = 0; step < ANCESTORS_MAX && Number.isInteger(pid) && pid > 1; step += 1) {
    const found = parseProcess(await lookup(pid));
    if (found === null) return null;
    if (!SHELLS.has(found.name)) {
      const started = await startedOf(pid);
      return started === null ? null : { pid, started };
    }
    pid = found.ppid;
  }
  return null;
}

// Whether a hook should start a watcher, given the watch file as it stands.
// Not while one is running for a session process that is still there, and not
// while another hook is in the middle of starting one.
export function needsWatcher(current, now, alive = isAlive) {
  if (current === null || typeof current !== 'object') return true;
  if (!alive(current.pid)) return true;
  if (Number.isInteger(current.watcher)) return !alive(current.watcher);
  return !(typeof current.at === 'number' && now - current.at < STARTING_MS);
}

// One turn of the watcher's loop: 'exit', 'pulse' or 'wait'. `job` is the
// watch file as it now reads, `state` the session's state file.
export function watchStep({ job, nonce, state, now, lastPulseAt, alive = isAlive }) {
  if (job === null || typeof job !== 'object' || job.nonce !== nonce) return 'exit';
  if (!alive(job.pid)) return 'exit';
  if (state === null || typeof state !== 'object' || state.stopped || state.capped) return 'exit';
  // No beat has landed yet, so the desk has no run to stamp.
  if (state.lastBeatAt === null || state.lastBeatAt === undefined) return 'wait';
  return lastPulseAt === null || now - lastPulseAt >= PULSE_MS ? 'pulse' : 'wait';
}

// The pulse's body: the run and the three fields every beat names, and
// `pulse: true`, on which the desk stamps the run alive and changes nothing
// else. It asks for no answers, and the desk takes no message on it.
export function pulseBody(state) {
  return {
    run: state.run,
    agent: AGENT,
    statusLine: state.last?.statusLine ?? statusLine(null, Boolean(state.idle)),
    pulse: true,
  };
}

// Whether the desk's answer to a pulse ends the watch: the run has ended, or
// the token is no longer taken (401, 403). Anything else, a desk that could
// not be reached included, is tried again at the next pulse.
export function pulseEnds(reply) {
  if (reply === null || reply === undefined) return false;
  if (reply.status === 401 || reply.status === 403) return true;
  return reply.status >= 200 && reply.status < 300 && (reply.json?.session?.endedAt ?? null) !== null;
}
