// ALYDAS-18, S3, design §2. The plugin's credential store, the file `gh` and
// `gcloud` keep: `<data dir>/credentials.json`, mode 600, in a directory of
// mode 700.
//
//   {"entries": [{"desk": "https://desk.example",        the desk it is for
//                 "appId": "...", "appName": "...",     the app it was minted for
//                 "repo": "owner/name" | null,          the repository connect ran in
//                 "token": "...",                       the session token, never printed
//                 "expiresAt": "2026-10-29T00:00:00.000Z",
//                 "scopes": ["read:WorkItem", ...],     what connect asked for, when known
//                 "revokedAt": "2026-11-06T..."}]}      when the desk said it no longer takes it
//
// That shape is also read by the plugin's copy of pipeline.py (S5), so it does
// not change without that reader. Nothing here prints; a token is only ever
// written into this one file.
//
// ALYDAS-379, S1, P2. `scopes` is the list connect asked the desk for, which
// the desk grants whole or refuses. An entry filed before has none and is read
// as it was. pipeline.py reads the entry by its named keys, so a key it does
// not know changes nothing there (`stored_credential`).
//
// ALYDAS-379, S2. The hook reads `scopes` to tell whether the desk takes a
// heartbeat at all (`sendsHeartbeat`).
//
// Gate 8 (W3). `revokedAt` is written when the desk answers the token 401
// (status's probe, or a session start's probe or beat: `markRevoked`), so the
// picker ranks it after a live entry on another desk. Connecting again files a
// new entry without it. pipeline.py does not read it.

import { randomBytes } from 'node:crypto';
import { chmodSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

export const CREDENTIALS_FILE = 'credentials.json';

export const NO_DATA_DIR =
  'The alydash plugin has no data directory (CLAUDE_PLUGIN_DATA is not set), so there is nowhere safe to keep a credential. Run this from Claude Code with the alydash plugin installed.';

// Where the plugin keeps its files. Claude Code sets CLAUDE_PLUGIN_DATA for a
// plugin's hooks, but not for a command's `!` or for the Bash tool (measured
// on 2.1.284, S3 report), so a command hands the directory over as an
// argument it had Claude Code substitute: `given`. There is no fallback to a
// home path: a guess would put a token somewhere nobody looks after.
export function pluginDataDir(env = process.env, given) {
  for (const candidate of [env.CLAUDE_PLUGIN_DATA, given]) {
    const dir = (candidate ?? '').trim();
    // Absolute only, which also turns away a `${CLAUDE_PLUGIN_DATA}` that was
    // never substituted.
    if (dir !== '' && isAbsolute(dir)) return dir;
  }
  throw new Error(NO_DATA_DIR);
}

// A directory only its owner can enter, made if missing and closed if it was
// open.
export function ensurePrivateDir(dir) {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
}

// A JSON file only its owner can read, written whole or not at all: a temp
// file of mode 600 beside it, then a rename over it.
export function writePrivateJson(path, value) {
  const temp = `${path}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
    renameSync(temp, path);
  } catch (error) {
    rmSync(temp, { force: true });
    throw error;
  }
}

const isEntry = (entry) =>
  entry !== null &&
  typeof entry === 'object' &&
  typeof entry.desk === 'string' &&
  typeof entry.appId === 'string' &&
  typeof entry.appName === 'string' &&
  (typeof entry.repo === 'string' || entry.repo === null) &&
  typeof entry.token === 'string' &&
  typeof entry.expiresAt === 'string';

// Every well-formed entry. A store that is missing or unreadable reads as
// empty, the way an unset variable is no token.
export function readCredentials(dataDir) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(join(dataDir, CREDENTIALS_FILE), 'utf8'));
  } catch {
    return { entries: [] };
  }
  const entries = Array.isArray(parsed?.entries) ? parsed.entries.filter(isEntry) : [];
  return { entries };
}

// The repository in any case, as GitHub compares it. Exported for the hook's
// choice among desks (ALYDAS-387).
export const sameRepo = (a, b) => (a === null || b === null ? a === b : a.toLowerCase() === b.toLowerCase());

// Files `entry`, replacing any entry for the same desk and repository (the
// repository in any case, as GitHub compares it).
export function saveCredential(dataDir, entry) {
  ensurePrivateDir(dataDir);
  const kept = readCredentials(dataDir).entries.filter(
    (other) => !(other.desk === entry.desk && sameRepo(other.repo, entry.repo)),
  );
  const { desk, appId, appName, repo, token, expiresAt, scopes } = entry;
  const filed = { desk, appId, appName, repo, token, expiresAt };
  if (Array.isArray(scopes)) filed.scopes = [...scopes];
  writePrivateJson(join(dataDir, CREDENTIALS_FILE), { entries: [...kept, filed] });
}

// Gate 8 (W3). Records on the stored entry for `entry`'s desk, repository and
// token what the desk last said of it: revoked (`at`, an instant) or, with
// `at` null, taken. Anything else in the store is left as it was, and a store
// with no such entry is not written.
export function markRevoked(dataDir, entry, at = new Date()) {
  const { entries } = readCredentials(dataDir);
  let changed = false;
  const next = entries.map((other) => {
    if (bareDesk(other.desk) !== bareDesk(entry.desk) || !sameRepo(other.repo, entry.repo) || other.token !== entry.token) return other;
    if (at === null) {
      if (other.revokedAt === undefined) return other;
      changed = true;
      const { revokedAt: _cleared, ...kept } = other;
      return kept;
    }
    changed = true;
    return { ...other, revokedAt: at.toISOString() };
  });
  if (changed) writePrivateJson(join(dataDir, CREDENTIALS_FILE), { entries: next });
}

// Whether the desk said it no longer takes this entry's token.
export const isRevoked = (entry) => typeof entry?.revokedAt === 'string';

// The scope a heartbeat needs, one of connect.mjs's SCOPES.
export const HEARTBEAT_SCOPE = 'heartbeat:AgentSession';

// ALYDAS-379, S2. Whether the hook sends this entry's desk heartbeats. Not
// when connect filed the scopes it asked for and the heartbeat's is not among
// them: the desk was built without runs, lists no such scope (S1), and would
// answer every beat 404. An entry with no `scopes` (filed before S1), or with
// a `scopes` that is not a list of strings, is read as one that may beat, as
// every entry was. Connecting again after the desk adds runs files an entry
// that asked for the scope, and the hook beats again.
export function sendsHeartbeat(entry) {
  return mayHold(entry, HEARTBEAT_SCOPE);
}

// The scope Work's items are read with, one of connect.mjs's SCOPES.
export const WORK_SCOPE = 'read:WorkItem';

// ALYDAS-385, S2 (design D3). Whether the desk this entry is for has Work, and
// so a board for /alydash:setup to point a repository at. Not when connect
// filed the scopes it asked for and Work's is not among them: the desk was
// built without Work and lists no such scope. Read as "may" by the same rule
// as sendsHeartbeat, so an entry from before ALYDAS-379 keeps the setup notice
// and setup as they were.
export function hasWork(entry) {
  return mayHold(entry, WORK_SCOPE);
}

// Whether `entry`'s filed scopes leave `scope` possible: false only for a list
// of strings without it.
function mayHold(entry, scope) {
  const scopes = entry?.scopes;
  if (!Array.isArray(scopes) || !scopes.every((one) => typeof one === 'string')) return true;
  return scopes.includes(scope);
}

// The live credential for `repo` on `desk`: the desk exactly, the repository
// in any case, not expired, and the latest expiry when there are several. An
// entry whose expiry cannot be read is taken as expired.
export function credentialFor(dataDir, desk, repo, now = new Date()) {
  let best = null;
  let bestAt = -Infinity;
  for (const entry of readCredentials(dataDir).entries) {
    const at = Date.parse(entry.expiresAt);
    if (entry.desk !== desk || !sameRepo(entry.repo, repo) || !(at > now.getTime())) continue;
    if (at > bestAt) {
      best = entry;
      bestAt = at;
    }
  }
  return best;
}

// A desk as the picker compares it: its trailing slashes cut.
const bareDesk = (desk) => String(desk).replace(/\/+$/, '');

// Whether `entry` has not run out at `now`. An expiry that cannot be read is
// taken as run out.
export function isLive(entry, now = new Date()) {
  return entry !== null && entry !== undefined && Date.parse(entry.expiresAt) > now.getTime();
}

// ALYDAS-445 (CR-1), design §1. The one choice of a repository's entry, which
// the hook, the detached sender, the watcher, setup and status all make, so
// none of them names a desk another does not. Among the entries for `repo`
// (in any case), on `desk` when one is named (ALYDASH_URL pinned, or the desk
// a session's job recorded) and on any desk otherwise, desks compared with
// their trailing slashes cut:
//
//   live ones first; among those, one the desk has not said is revoked
//   (Gate 8, W3: isRevoked) before one it has, then one that sends heartbeats
//   (sendsHeartbeat) before one that does not (ALYDAS-387), then the latest
//   expiry;
//   with none live, the latest that ran out, so status can still say which
//   desk it was.
//
// The entry comes back with its desk's trailing slashes cut, or null when the
// repository has none there. The caller reads isLive itself: a hook beats
// only with a live one.
export function pickCredential(dataDir, repo, { desk = null, now = new Date() } = {}) {
  const wanted = desk === null ? null : bareDesk(desk);
  const ends = (entry) => Date.parse(entry.expiresAt);
  let live = null;
  let ranOut = null;
  for (const entry of readCredentials(dataDir).entries) {
    if (!sameRepo(entry.repo, repo) || (wanted !== null && bareDesk(entry.desk) !== wanted)) continue;
    if (isLive(entry, now)) {
      const ahead =
        live === null ||
        (!isRevoked(entry) && isRevoked(live)) ||
        (isRevoked(entry) === isRevoked(live) &&
          ((sendsHeartbeat(entry) && !sendsHeartbeat(live)) ||
            (sendsHeartbeat(entry) === sendsHeartbeat(live) && ends(entry) > ends(live))));
      if (ahead) live = entry;
    } else if (ranOut === null || ends(entry) > ends(ranOut)) {
      ranOut = entry;
    }
  }
  const picked = live ?? ranOut;
  return picked === null ? null : { ...picked, desk: bareDesk(picked.desk) };
}
