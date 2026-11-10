// ALYDAS-18, S3. `/alydash:connect`: connects this laptop to the desk with the
// device grant (RFC 8628), because a command's `` !`…` `` runs to completion
// before the model sees anything (design, "Phase 2 verdict"):
//
//   start [--data <dir>]  asks the desk for a code, prints the address and the
//                         code, and leaves a detached `poll` running. The
//                         command's turn ends there, with the code on screen.
//   poll <state file>     polls the token endpoint, files the credential, and
//                         leaves the outcome in the state file. Started by
//                         `start`, never by a person.
//
// Gate 8, bug 1: a model that waited on the poll in its turn hid the code
// until the turn ended, so nothing waits. The plugin's next hook reads the
// decided state file and tells the person how it ended, once
// (takeDecidedLines, called from hook.mjs).
//
// The token and the device code are never printed, by any path. The token is
// written only to credentials.json; the device code only to the state file,
// until the poll is decided and the file is rewritten without it.
//
// `start` returns `{ code, lines }` and `main` prints the lines, so what a
// person sees is in one place, and the desk, the clock and the poller are
// handed in by the suite (tests/plugin/connect.test.ts).

import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync } from 'node:fs';
import { hostname } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  credentialFor,
  ensurePrivateDir,
  pluginDataDir,
  readCredentials,
  saveCredential,
  sendsHeartbeat,
  writePrivateJson,
} from './lib/credentials.mjs';
import { deskUrl, getJson, postForm } from './lib/desk.mjs';
import { repoOf } from './lib/repo.mjs';
import { SENDS_VERSION, WHAT_IS_SENT } from './lib/session.mjs';

// The desk's `DEVICE_CLIENT_ID` and `DEVICE_GRANT_TYPE` (packages/core/src/device.ts, oauth.ts).
const CLIENT_ID = 'alydash-plugin';
const DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code';

// The evidence preset (packages/modules/work/src/presets.ts,
// `WORK_PRESETS.evidence`; tests/plugin/connect.test.ts holds the two equal): Show
// on Now, Ask you, Read items, Attach evidence and Tick criteria, which is
// what the heartbeat and the three session skills use.
//
// ALYDAS-127, S5 (the plan's P4). And Take work from the desk, first, as a
// desk with the laptop runner composes the preset: what `/alydash:runner`
// claims a start with.
export const RUNNER_SCOPE = 'claim:RunStart';
export const SCOPES = `${RUNNER_SCOPE} heartbeat:AgentSession raise:Ask read:WorkItem attach:Attachment tick:Criterion`;

// ALYDAS-379, S1. A desk built without runs or without Work grants fewer than
// the five, and refuses a request that names a scope it does not grant
// (agent access's `unknownScopeRefusal`). So start reads the desk's
// authorization server metadata (RFC 8414, at the address its §3.1 gives an
// issuer of `<desk>/api/auth`) and asks for the five it lists, in SCOPES'
// order, and never for a scope the plugin does not use.
const METADATA_PATH = '/.well-known/oauth-authorization-server/api/auth';
const noneOffered = (desk) =>
  `The desk at ${desk} offers none of the access the alydash plugin uses, so no connection was started.`;

// The scopes to ask `desk` for: those of SCOPES its metadata lists, or the
// five from before the laptop runner when the metadata cannot be read (a desk
// from before, or one that cannot be reached), so the desk answers as it did
// before this read. ALYDAS-127, S5 (amendment 10): never claim:RunStart
// unlisted, which a desk from before the laptop runner would refuse.
async function scopesFor(desk, get) {
  const wanted = SCOPES.split(' ');
  const before = wanted.filter((scope) => scope !== RUNNER_SCOPE);
  let answer;
  try {
    answer = await get(`${desk}${METADATA_PATH}`);
  } catch {
    return before;
  }
  const listed = answer?.status === 200 ? answer.json?.scopes_supported : undefined;
  if (!Array.isArray(listed)) return before;
  return wanted.filter((scope) => listed.includes(scope));
}

// Gate 8, COR-4. The poller is detached and holds up nobody, so it gives the
// desk 30 s to answer. The desk marks a code used before it mints, so a poll
// given up on at the hooks' 3 s would leave a live token nobody holds.
export const POLL_TIMEOUT_MS = 30_000;
// RFC 8628 §3.5: every slow_down adds 5 seconds to the interval, for good.
const SLOW_DOWN_MS = 5000;
// The desk's `HOST_MAX`.
const HOST_MAX = 64;
const ID = /^[0-9a-f]{16}$/;
const STATE_NAME = /^[0-9a-f]{16}\.json$/;

// Gate 8, the S3 ruling: a credential filed outside a repository is read by no
// hook, so connect refuses there rather than mint a token nobody uses.
// ALYDAS-386: said before the desk is known, so in words true of a desk
// without runs.
export const NOT_A_REPO = 'Run /alydash:connect inside the GitHub repository of an app on your desk.';

// ALYDAS-283. What connect says when no desk is named and this laptop's store
// does not settle it: the question, the one command that answers it, and where
// the person's own desk shows that command with its address filled in (the
// connect commands of ALYDAS-272, `connectCommands` in the agent access module).
export function whichDesk(known) {
  return [
    known.length === 0
      ? 'Which desk? This laptop has not connected to one, and the plugin has no desk of its own.'
      : `Which desk? This laptop has connected to more than one: ${known.map(oneLine).join(', ')}.`,
    "Start Claude Code once with your desk's address, then run /alydash:connect again: ALYDASH_URL=https://your-desk.example claude",
    // ALYDAS-386: the steps go once a run reports on a desk with runs, and
    // once Claude Code has connected on one without (ALYDAS-384), so neither.
    "Your desk shows that command with its own address in it, on Apps and on a new app's board.",
  ];
}

// The desks this laptop has a credential for, live or run out, each once, in
// the order they were filed.
function knownDesks(dataDir) {
  return [...new Set(readCredentials(dataDir).entries.map((entry) => entry.desk))];
}

const APPROVE = 'Approve it on the desk. This session will say when it is connected.';
const DENIED = 'Denied on the desk. Nothing was connected.';
const RAN_OUT = 'The code ran out. Run /alydash:connect again.';
// ALYDAS-379. Connected to a desk without runs: true of every session there.
const NO_RUNS_HERE = (repo) => `This desk has no runs, so sessions in ${repo} do not show on Now.`;
// ALYDAS-386, criterion 2. Connected to an app the desk has runs switched off
// for: the token answer's `app.sessionsOnNow` is false (agent access's
// `sessions.on-now`, which runs fills from its switch).
const RUNS_OFF_HERE = (appName, repo) => `Runs are off for ${oneLine(appName)}, so sessions in ${repo} do not show on Now.`;
const FAILED = 'The desk would not hand over a token for that code. Run /alydash:connect again.';
// ALYDAS-388, criterion 3 (ruled: name it). Connecting again files over the
// live credential for the same desk and repository, but the plugin cannot
// revoke its token: the desk's revoke needs a person signed in. So the
// connected line names it and says where to end it, the section and button
// agent access labels "Access" and "Disconnect".
const REPLACED = (appName) =>
  `Your earlier connection to ${oneLine(appName)} still works until you press Disconnect on it in Settings > Access on the desk.`;

const oneLine = (text) => String(text).replace(/\s+/g, ' ').trim();
const realSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The machine's short name, as the desk shows it to the person approving:
// before the first dot, and at most 64 code points.
export function hostName(raw) {
  return [...String(raw).split('.')[0]].slice(0, HOST_MAX).join('');
}

// The state file a `start` leaves for its poller, and the poller for the hook.
const stateFileOf = (dataDir, id) => join(dataDir, 'connect', `${id}.json`);
const connectDirOf = (dataDir) => join(dataDir, 'connect');
const SELF = fileURLToPath(import.meta.url);

// The repository in any case, as GitHub compares it (credentials.mjs's rule).
const sameRepo = (a, b) =>
  typeof a === 'string' && typeof b === 'string' ? a.toLowerCase() === b.toLowerCase() : a === b;

// Every state file `start` left, by name, so nothing else in the folder (a
// temp file, a claimed notice) is read as one.
function stateFilesIn(dataDir) {
  try {
    return readdirSync(connectDirOf(dataDir))
      .filter((name) => STATE_NAME.test(name))
      .sort()
      .map((name) => join(connectDirOf(dataDir), name));
  } catch {
    return [];
  }
}

function readState(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

// The detached poller: its own process group, no terminal, not waited for, so
// it outlives the command's `!` and the turn it ends.
function spawnDetachedPoller(stateFile) {
  spawn(process.execPath, [SELF, 'poll', stateFile], {
    detached: true,
    stdio: 'ignore',
  }).unref();
}

// Whether `pid` is this script's poller for `stateFile`: a node process whose
// command line is `<node> <this script> poll <that file>`. A pid is reused once
// its process is gone, so nothing else is ever signalled.
// One `ps` call with a generous timeout: on a loaded machine two calls at 2 s
// each timed out, the poller was taken for a stranger, and it was left running
// (CI, PR #70).
function isOurPoller(pid, stateFile) {
  try {
    const command = execFileSync('ps', ['-ww', '-o', 'command=', '-p', String(pid)], {
      encoding: 'utf8',
      timeout: 10_000,
    }).trim();
    const tail = ` ${SELF} poll ${stateFile}`;
    return command.endsWith(tail) && basename(command.slice(0, -tail.length)).startsWith('node');
  } catch {
    return false;
  }
}

// Gate 8, COR-5. A retried connect ends the one before it for the same desk
// and repository: its poller is stopped and its state removed, so retries do
// not poll side by side and use up the desk's poll allowance.
function supersede(dataDir, desk, repo) {
  for (const file of stateFilesIn(dataDir)) {
    const state = readState(file);
    if (state?.state !== 'waiting' || state.desk !== desk || !sameRepo(state.repo ?? null, repo)) continue;
    if (Number.isInteger(state.pid) && state.pid > 0 && isOurPoller(state.pid, file)) {
      try {
        process.kill(state.pid, 'SIGTERM');
      } catch {
        // already gone
      }
    }
    rmSync(file, { force: true });
  }
}

const isStart = (json) =>
  json !== null &&
  typeof json === 'object' &&
  typeof json.device_code === 'string' &&
  typeof json.user_code === 'string' &&
  typeof json.verification_uri_complete === 'string' &&
  typeof json.expires_in === 'number' &&
  typeof json.interval === 'number';

export async function start({
  env = process.env,
  dataArg,
  cwd = process.cwd(),
  hostname: host = hostname(),
  now = Date.now,
  post = postForm,
  get = getJson,
  spawnPoller = spawnDetachedPoller,
} = {}) {
  let dataDir;
  try {
    dataDir = pluginDataDir(env, dataArg);
  } catch (error) {
    return { code: 1, lines: [error.message] };
  }
  const repo = await repoOf(cwd);
  if (repo === null) return { code: 1, lines: [NOT_A_REPO] };
  // The desk named for this start, or else the one desk this laptop already
  // uses, so another repository or a token that ran out needs nothing set.
  // Otherwise the person is asked: no desk is assumed.
  const known = knownDesks(dataDir);
  const desk = deskUrl(env) ?? (known.length === 1 ? known[0] : null);
  if (desk === null) return { code: 1, lines: whichDesk(known) };
  supersede(dataDir, desk, repo);
  const scopes = await scopesFor(desk, get);
  if (scopes.length === 0) return { code: 1, lines: [noneOffered(desk)] };
  const fields = { client_id: CLIENT_ID, scope: scopes.join(' '), repo };
  const shortHost = hostName(host);
  if (shortHost !== '') fields.host = shortHost;

  let answer;
  try {
    answer = await post(`${desk}/api/oauth/device`, fields);
  } catch (error) {
    // desk.mjs's own sentences, which name nothing that was sent.
    return { code: 1, lines: [`Could not start a connection: ${oneLine(error.message).replace(/\.$/, '')}.`] };
  }
  if (answer.status !== 200 || !isStart(answer.json)) {
    const said = typeof answer.json?.error_description === 'string' ? answer.json.error_description : `it answered ${answer.status}`;
    return { code: 1, lines: [`The desk at ${desk} would not start a connection: ${oneLine(said)}`] };
  }
  const { device_code, user_code, verification_uri_complete, expires_in, interval } = answer.json;

  const id = randomBytes(8).toString('hex');
  const stateFile = stateFileOf(dataDir, id);
  ensurePrivateDir(dataDir);
  ensurePrivateDir(dirname(stateFile));
  writePrivateJson(stateFile, {
    state: 'waiting',
    device_code,
    interval,
    expiresAt: new Date(now() + expires_in * 1000).toISOString(),
    desk,
    repo,
    // ALYDAS-379, S1, P2. What was asked, which the desk grants whole or
    // refuses, so the poller files it with the credential.
    scopes,
  });
  spawnPoller(stateFile, dataDir);

  return {
    code: 0,
    // ALYDAS-265. What approving starts, said before the person approves.
    // ALYDAS-379. A desk without runs is sent no heartbeat, so it is not
    // told what one carries.
    lines: [
      `Open ${oneLine(verification_uri_complete)}`,
      `Code: ${oneLine(user_code)}`,
      APPROVE,
      ...(sendsHeartbeat({ scopes }) ? [WHAT_IS_SENT] : []),
    ],
  };
}

// What one answer from the token endpoint means for the poll (RFC 8628 §3.5).
// A desk that is down, or answers with no error it names, is asked again.
export function pollStep(answer) {
  if (answer === null) return { kind: 'pending' };
  const { status, json } = answer;
  if (status === 200) {
    const ok =
      typeof json?.access_token === 'string' &&
      json.access_token !== '' &&
      typeof json.expires_in === 'number' &&
      typeof json.app?.id === 'string' &&
      typeof json.app?.name === 'string';
    if (!ok) return { kind: 'stop', state: 'failed' };
    // ALYDAS-386. Whether sessions in the app show on Now, only when the desk
    // said so as a yes or no; a desk from before, or with nothing that
    // answers it, says nothing.
    const onNow = typeof json.app.sessionsOnNow === 'boolean' ? { sessionsOnNow: json.app.sessionsOnNow } : {};
    return { kind: 'token', token: json.access_token, expiresIn: json.expires_in, app: { id: json.app.id, name: json.app.name }, ...onNow };
  }
  const error = typeof json?.error === 'string' ? json.error : null;
  if (error === 'authorization_pending') return { kind: 'pending' };
  if (error === 'slow_down') return { kind: 'slow_down' };
  if (error === 'access_denied') return { kind: 'stop', state: 'denied' };
  if (error === 'expired_token') return { kind: 'stop', state: 'expired' };
  if (error !== null && status >= 400 && status < 500) return { kind: 'stop', state: 'failed' };
  return { kind: 'pending' };
}

// The detached half. Reads the state `start` wrote, polls at the interval
// until the code is decided or runs out, and rewrites the state without the
// device code, keeping the desk and repository the hook announces it by.
// Returns the final state's name, or `superseded` once a later start has
// removed its state file.
export async function poll(
  stateFile,
  { post = postForm, sleep = realSleep, now = Date.now, pid = process.pid } = {},
) {
  const state = readState(stateFile);
  if (state?.state !== 'waiting' || typeof state.device_code !== 'string') return state?.state ?? 'missing';
  // The state file sits at <data dir>/connect/<id>.json.
  const dataDir = dirname(dirname(stateFile));
  const expiresAt = Date.parse(state.expiresAt);
  const where = { desk: state.desk, repo: state.repo ?? null };
  const finish = (next) => {
    writePrivateJson(stateFile, { ...next, ...where });
    return next.state;
  };
  const superseded = () => readState(stateFile)?.device_code !== state.device_code;
  // The pid, so a later start can find this poller and stop it.
  if (!existsSync(stateFile)) return 'superseded';
  writePrivateJson(stateFile, { ...state, pid });

  let interval = Math.max(1, Number(state.interval) || 5) * 1000;
  for (;;) {
    await sleep(interval);
    if (superseded()) return 'superseded';
    if (!(now() < expiresAt)) return finish({ state: 'expired' });
    let answer = null;
    try {
      answer = await post(
        `${state.desk}/api/oauth/token`,
        { grant_type: DEVICE_GRANT, device_code: state.device_code, client_id: CLIENT_ID },
        { timeoutMs: POLL_TIMEOUT_MS },
      );
    } catch {
      answer = null;
    }
    const step = pollStep(answer);
    if (step.kind === 'pending') continue;
    if (step.kind === 'slow_down') {
      interval += SLOW_DOWN_MS;
      continue;
    }
    if (step.kind === 'stop') return finish({ state: step.state });
    let replaced = null;
    try {
      // ALYDAS-265. This laptop was told what a connected session sends: by
      // `start`, on screen, and by the page the person approved on. So the
      // hook does not say it again, and sends a prompt's first line from the
      // first prompt (hook.mjs reads `told.json`).
      // ALYDAS-379: only when start said it, which it does not on a desk
      // without runs, so a later connect to a desk with runs still tells it.
      if (sendsHeartbeat({ scopes: state.scopes })) writePrivateJson(join(dataDir, 'told.json'), { sends: SENDS_VERSION });
      // ALYDAS-388. The live entry this one files over, read before it goes:
      // saveCredential replaces every entry for the same desk and repository,
      // and credentialFor is the live one among them. One that has run out
      // is no connection, so it is not named.
      replaced = credentialFor(dataDir, state.desk, state.repo ?? null, new Date(now()));
      saveCredential(dataDir, {
        desk: state.desk,
        appId: step.app.id,
        appName: step.app.name,
        // The repository connect ran in, which is where sessions look it up,
        // and not the app's.
        repo: state.repo ?? null,
        token: step.token,
        expiresAt: new Date(now() + step.expiresIn * 1000).toISOString(),
        // ALYDAS-379, S1. A state a start from before this change left has
        // none, and its credential is filed without, as before.
        ...(Array.isArray(state.scopes) ? { scopes: state.scopes } : {}),
      });
    } catch {
      return finish({ state: 'failed' });
    }
    return finish({
      state: 'connected',
      appName: step.app.name,
      ...(sendsHeartbeat({ scopes: state.scopes }) ? (step.sessionsOnNow === false ? { onNow: false } : {}) : { runs: false }),
      ...(replaced === null ? {} : { replaced: replaced.appName }),
    });
  }
}

// The line a decided state reads as, or null while it is still waiting.
function decidedLine(state) {
  if (state.state === 'connected') {
    const connected =
      state.runs === false
        ? `Connected to ${oneLine(state.appName)}. ${NO_RUNS_HERE(state.repo)}`
        : state.onNow === false
          ? `Connected to ${oneLine(state.appName)}. ${RUNS_OFF_HERE(state.appName, state.repo)}`
          : `Connected to ${oneLine(state.appName)}. Sessions in ${state.repo} now show on Now.`;
    return typeof state.replaced === 'string' ? `${connected} ${REPLACED(state.replaced)}` : connected;
  }
  if (state.state === 'denied') return DENIED;
  if (state.state === 'expired') return RAN_OUT;
  if (state.state === 'failed') return FAILED;
  return null;
}

// Gate 8, bug 1. The lines for every connect in `repo` that has been decided,
// each claimed (renamed away, then removed) so it is said once, even by two
// hooks at the same moment. hook.mjs puts them in a systemMessage.
export function takeDecidedLines(dataDir, repo) {
  const lines = [];
  for (const file of stateFilesIn(dataDir)) {
    const state = readState(file);
    if (state === null || !sameRepo(state.repo ?? null, repo)) continue;
    const line = decidedLine(state);
    if (line === null) continue;
    const claimed = `${file}.${process.pid}.said`;
    try {
      renameSync(file, claimed);
    } catch {
      continue;
    }
    rmSync(claimed, { force: true });
    lines.push(line);
  }
  return lines;
}

// `--data <dir>` from an argument list, or undefined.
function dataArgOf(args) {
  const at = args.indexOf('--data');
  return at === -1 ? undefined : args[at + 1];
}

const USAGE = 'Usage: connect.mjs start [--data <dir>]';

export async function main(argv) {
  const [command, ...args] = argv;
  let outcome;
  try {
    if (command === 'start') outcome = await start({ dataArg: dataArgOf(args) });
    else if (command === 'poll' && typeof args[0] === 'string' && ID.test(basename(args[0], '.json'))) {
      await poll(args[0]);
      return 0;
    } else outcome = { code: 2, lines: [USAGE] };
  } catch {
    // Never the error's own message: a parse error can quote what it read.
    outcome = { code: 1, lines: [`/alydash:connect stopped on an unexpected error in ${command}. Run it again.`] };
  }
  await new Promise((resolve) => process.stdout.write(outcome.lines.map((line) => `${line}\n`).join(''), resolve));
  return outcome.code;
}

// Run as a script, it exits as soon as it has said its lines: fetch keeps a
// connection alive for a few seconds, which would otherwise hold up the
// command's `!` and keep a finished poller running.
const invoked = process.argv[1] ? realpathSync(process.argv[1]) : null;
if (invoked === realpathSync(fileURLToPath(import.meta.url))) {
  process.exit(await main(process.argv.slice(2)));
}
