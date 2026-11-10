// What a repository has of the things /alydash:setup installs, read off the
// disk with no desk and no network. Its own small file because hook.mjs asks
// at a session's start, and a hook loads as little as it can: setup.mjs
// brings the whole merge notes reader with it.

import { lstatSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { isLocalDesk } from './desk.mjs';

export const SYNC_FILE = '.github/workflows/sync.yml';
export const TEMPLATE_FILE = '.github/pull_request_template.md';
// The section's heading, as the desk's reader spells it (merge-notes.ts).
export const HEADING = /^ {0,3}## +What you['’]ll notice *$/im;

// What is at `path`: 'none', the text of a regular file, or 'other' for a
// folder, a link or anything else, which is never written over.
export function existing(path) {
  let stat;
  try {
    stat = lstatSync(path);
  } catch {
    return { kind: 'none' };
  }
  if (!stat.isFile()) return { kind: 'other' };
  return { kind: 'file', text: readFileSync(path, 'utf8') };
}

// ALYDAS-391. A desk on this laptop is one GitHub's runners cannot reach, so a
// sync.yml pointing at it would fail on every deploy, and setup writes none
// for one. The rule is desk.mjs's isLocalDesk, read with https taken as http,
// because an https desk on this laptop is as far out of a runner's reach.
// Here, not in setup.mjs, so the hook's notice and setup share the one rule
// (ALYDAS-391 Gate 8 C1).
export const onThisLaptop = (desk) => isLocalDesk(String(desk).replace(/^https:/i, 'http:'));

// Which of the two things the Timeline cannot do without `root` lacks, as
// short names: the sync workflow, and a pull request template that carries
// the section. ALYDAS-391 Gate 8 C1: for a repository whose desk is on this
// laptop the sync workflow is never named, since setup writes none there and
// the notice sends the person to setup; /alydash:status says why.
export function notSetUp(root, desk) {
  const missing = [];
  if (!onThisLaptop(desk) && existing(join(root, SYNC_FILE)).kind !== 'file') missing.push('sync workflow');
  const found = existing(join(root, TEMPLATE_FILE));
  if (found.kind !== 'file' || !HEADING.test(found.text)) missing.push('pull request template with the "What you\'ll notice" section');
  return missing;
}

// Said once for a repository, when a session starts in it.
export const notSetUpNotice = (missing) =>
  `alydash: this repository has no ${missing.join(' and no ')}, so what merges here may never show on your desk. /alydash:status says what is missing and /alydash:setup adds it.`;
