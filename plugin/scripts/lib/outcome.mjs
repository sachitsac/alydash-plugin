// ALYDAS-515, S1 (design D1, D2). What a session that stopped came to, read
// on the laptop by hook.mjs on Stop and SessionEnd only:
//
//   lastReplyText(transcriptPath)  the text of the last assistant message that
//                                  has text, off the transcript's tail; the
//                                  cut to one line is session.mjs replyLine's
//   gitFacts(cwd, run, { startHead, startOrigin, since })
//                                  the commit, how many commits the branch has
//                                  that origin's default branch does not,
//                                  whether a remote-tracking branch holds it,
//                                  and (ALYDAS-524) whether the session
//                                  committed straight onto that default branch
//
// Neither ever throws, and neither touches the network: the git facts are what
// the laptop last saw of origin, never a fetch. A transcript that cannot be
// read gives null and costs nothing else.

import { closeSync, fstatSync, openSync, readSync } from 'node:fs';

// The most of a transcript's tail read for the reply. The last reply is near
// the end, and a hook has ten seconds; lib/usage.mjs bounds its reads the
// same way.
export const REPLY_READ_MAX_BYTES = 1024 * 1024;
// The most `ahead` says (the desk's bound, slice S2): a branch further ahead
// is said to be this far.
export const AHEAD_MAX = 100_000;

// The text blocks of one transcript line that is an assistant message, in
// order, and its message id. Null for any other line.
function assistantText(text) {
  if (!text.includes('"assistant"')) return null;
  let line;
  try {
    line = JSON.parse(text);
  } catch {
    return null;
  }
  if (line?.type !== 'assistant') return null;
  const content = line.message?.content;
  const texts =
    typeof content === 'string'
      ? [content]
      : Array.isArray(content)
        ? content.filter((block) => block?.type === 'text' && typeof block.text === 'string').map((block) => block.text)
        : [];
  const id = line.message?.id ?? null;
  return { id: typeof id === 'string' ? id : null, texts: texts.filter((one) => one.trim() !== '') };
}

// Whether one transcript line is a prompt a person sent: a user line whose
// content is text, not the tool results Claude Code also files as user lines.
function isPrompt(text) {
  if (!text.includes('"user"')) return false;
  let line;
  try {
    line = JSON.parse(text);
  } catch {
    return false;
  }
  if (line?.type !== 'user') return false;
  const content = line.message?.content;
  if (typeof content === 'string') return true;
  return Array.isArray(content) && content.length > 0 && !content.some((block) => block?.type === 'tool_result');
}

// The last REPLY_READ_MAX_BYTES of a file as whole lines: a first line the
// window cut into is dropped. Null when the file cannot be read.
function tailLines(path) {
  let file;
  try {
    file = openSync(path, 'r');
  } catch {
    return null;
  }
  try {
    const size = fstatSync(file).size;
    const from = Math.max(0, size - REPLY_READ_MAX_BYTES);
    const buffer = Buffer.alloc(size - from);
    const read = readSync(file, buffer, 0, buffer.length, from);
    let text = buffer.toString('utf8', 0, read);
    if (from > 0) {
      const first = text.indexOf('\n');
      text = first === -1 ? '' : text.slice(first + 1);
    }
    return text.split('\n');
  } catch {
    return null;
  } finally {
    closeSync(file);
  }
}

// The text of the last assistant message that has text, or null. An
// assistant line with no text block (a tool call) is stepped over; one message
// written over several lines (one per content block, the same id) is read
// whole, its blocks in order. Gate 8 (F5): the search stops at the prompt that
// began the last turn, so a turn that wrote no text gives null, never the
// reply of the turn before it.
export function lastReplyText(transcriptPath) {
  if (typeof transcriptPath !== 'string' || transcriptPath === '') return null;
  const lines = tailLines(transcriptPath);
  if (lines === null) return null;
  let found = null;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const one = assistantText(lines[i]);
    if (found === null) {
      if (one !== null && one.texts.length > 0) found = { id: one.id, texts: [...one.texts] };
      else if (one === null && isPrompt(lines[i])) return null;
      continue;
    }
    // Earlier lines of the same message; anything else ends it.
    if (one === null || one.id === null || one.id !== found.id) break;
    found.texts.unshift(...one.texts);
  }
  return found === null ? null : found.texts.join('\n\n');
}

// One fact, in its own try: null when the runner fails or throws.
async function attempt(read) {
  try {
    return await read();
  } catch {
    return null;
  }
}

// `run(cwd, args)` answers git's trimmed output, blank included, or null when
// git failed (hook.mjs's `git` with `{ empty: true }`).
//
//   commit     `git rev-parse HEAD`: 40 (or 64) hex, else null
//   ahead      `git rev-list --count origin/HEAD..HEAD`: null when origin/HEAD
//              is not known, as on a clone that never set it
//   pushed     `git branch -r --contains HEAD`: true when it names a branch,
//              false when it names none, null when git failed
//   onDefault  ALYDAS-524 (design D4). True when the branch is origin's
//              default (`git symbolic-ref refs/remotes/origin/HEAD`, read
//              beside `git rev-parse --symbolic-full-name HEAD`) and the
//              session committed straight onto it. False on any other branch
//              (a detached HEAD among them), and on the default branch when
//              it did not. Null when it cannot be told: origin's default is
//              not known, git failed, or on the default branch with no commit
//              or no recorded start to compare.
//
//              Gate 8 (G8W2). HEAD moving on the default branch is not the
//              session's commit: a pull, or a checkout of main after it moved,
//              moves it too. So the start the hook recorded at SessionStart is
//              HEAD (`startHead`), origin's default tip as the laptop knew it
//              (`startOrigin`) and when (`since`, ms), and true needs HEAD to
//              have `startHead` as an ancestor and a commit that neither start
//              holds which this laptop's git identity committed since the
//              session began, a merge aside. A pull brings commits newer than
//              `startOrigin` too, so the identity and the time are what tell
//              the session's own from someone else's. A start with no
//              `startOrigin` recorded (an older plugin's state) is null.
//
// The first five read at once, so the hook's budget pays for one git round;
// on the default branch, a second round asks which commits are the session's.
export async function gitFacts(cwd, run, { startHead = null, startOrigin = null, since = null } = {}) {
  const [commit, ahead, pushed, originDefault, fullName] = await Promise.all([
    attempt(async () => {
      const text = await run(cwd, ['rev-parse', 'HEAD']);
      return typeof text === 'string' && COMMIT.test(text) ? text : null;
    }),
    attempt(async () => {
      const text = await run(cwd, ['rev-list', '--count', 'origin/HEAD..HEAD']);
      return typeof text === 'string' && /^\d+$/.test(text) ? Math.min(Number(text), AHEAD_MAX) : null;
    }),
    attempt(async () => {
      const text = await run(cwd, ['branch', '-r', '--contains', 'HEAD']);
      return typeof text === 'string' ? text.trim() !== '' : null;
    }),
    attempt(async () => {
      const text = await run(cwd, ['symbolic-ref', 'refs/remotes/origin/HEAD']);
      return typeof text === 'string' && text.startsWith(ORIGIN_PREFIX) && text.length > ORIGIN_PREFIX.length ? text.slice(ORIGIN_PREFIX.length) : null;
    }),
    attempt(async () => {
      const text = await run(cwd, ['rev-parse', '--symbolic-full-name', 'HEAD']);
      return typeof text === 'string' && text !== '' ? text : null;
    }),
  ]);
  const onDefault = await onDefaultOf(cwd, run, { originDefault, fullName, commit, startHead, startOrigin, since });
  return { commit, ahead, pushed, onDefault };
}

const COMMIT = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
const ORIGIN_PREFIX = 'refs/remotes/origin/';

const isCommit = (text) => typeof text === 'string' && COMMIT.test(text);
// `git var GIT_COMMITTER_IDENT`: `Name <email> seconds zone`, as git would
// commit now, environment included.
const IDENT_EMAIL = /<([^<>]*)>\s+\d+\s+[+-]\d{4}$/;

async function onDefaultOf(cwd, run, { originDefault, fullName, commit, startHead, startOrigin, since }) {
  if (originDefault === null || fullName === null) return null;
  if (fullName !== `refs/heads/${originDefault}`) return false;
  if (commit === null || !isCommit(startHead) || !isCommit(startOrigin)) return null;
  if (commit === startHead) return false;
  const [behind, ident, made] = await Promise.all([
    // Commits of the start's HEAD that HEAD lacks: none when it is an ancestor.
    attempt(() => run(cwd, ['rev-list', '--count', startHead, '--not', 'HEAD'])),
    attempt(() => run(cwd, ['var', 'GIT_COMMITTER_IDENT'])),
    // What HEAD has that neither start held, merges aside, with who and when.
    attempt(() => run(cwd, ['log', '--no-merges', '--format=%ce%x09%ct', 'HEAD', '--not', startHead, startOrigin])),
  ]);
  if (typeof behind !== 'string' || !/^\d+$/.test(behind)) return null;
  if (behind !== '0') return false;
  const email = typeof ident === 'string' ? IDENT_EMAIL.exec(ident.trim())?.[1] ?? null : null;
  if (email === null || email === '' || typeof made !== 'string') return null;
  const from = Number.isFinite(since) ? Math.floor(since / 1000) : null;
  return made
    .split('\n')
    .filter((line) => line.trim() !== '')
    .some((line) => {
      const [who, at] = line.split('\t');
      return who === email && (from === null || Number(at) >= from);
    });
}
