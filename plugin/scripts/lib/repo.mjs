// ALYDAS-18, S3, ruling R1. Which GitHub repository the plugin is running in,
// as `owner/name`. The desk is sent only that, never the origin URL, so a
// personal access token in an https origin never leaves the laptop.
//
// The plugin cannot import the monorepo, so `https` is read by a copy of
// core's `parseGitHubRepo` (packages/core/src/gateway.ts), and
// tests/plugin/repo.test.ts pins the two to agree over a fixture list. The two
// SSH spellings are read by putting their path through the same rule.

import { execFile } from 'node:child_process';

// The owner and name as core reads them from a URL's path: `.git` and one
// trailing slash are dropped.
const PATH = /^\/([A-Za-z0-9_-]+)\/([A-Za-z0-9._-]+?)(?:\.git)?\/?$/;

// Core's rule, copied: `http(s)://(www.)github.com/owner/name`.
function fromHttps(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
  if (parsed.hostname !== 'github.com' && parsed.hostname !== 'www.github.com') return null;
  const match = PATH.exec(parsed.pathname);
  return match ? `${match[1]}/${match[2]}` : null;
}

// A path an SSH remote names, read as the same path on https would be. The
// URL parser resolves `.` and `..`, which git over SSH would not, so a path
// that reads differently once resolved is turned away rather than guessed at.
function fromSshPath(path) {
  const literal = PATH.exec(path);
  if (!literal) return null;
  const resolved = fromHttps(`https://github.com${path}`);
  return resolved === `${literal[1]}/${literal[2]}` ? resolved : null;
}

// `owner/name` for a GitHub origin, or null. The user and password of an
// https origin are never part of the answer.
export function normaliseRepo(url) {
  const given = String(url).trim();
  // scp-like: git@github.com:owner/name(.git)
  const scp = /^git@github\.com:([^/].*)$/.exec(given);
  if (scp) return fromSshPath(`/${scp[1]}`);
  if (given.startsWith('ssh://')) {
    let parsed;
    try {
      parsed = new URL(given);
    } catch {
      return null;
    }
    if (parsed.hostname !== 'github.com' || parsed.username !== 'git' || parsed.password !== '' || parsed.port !== '') {
      return null;
    }
    if (parsed.search !== '' || parsed.hash !== '') return null;
    return fromSshPath(parsed.pathname);
  }
  return fromHttps(given);
}

// The repository `cwd` is in, off `git remote get-url origin`, or null when
// it is not in one, has no origin, or its origin is not on GitHub. git is
// handed `cwd` as an argument, with no shell in between.
export function repoOf(cwd) {
  return new Promise((resolve) => {
    execFile(
      'git',
      ['-C', cwd, 'remote', 'get-url', 'origin'],
      { timeout: 5000, env: { ...process.env, GIT_TERMINAL_PROMPT: '0' } },
      (error, stdout) => resolve(error ? null : normaliseRepo(String(stdout))),
    );
  });
}
