// Installed by /alydash:setup, and run by `.github/workflows/merge-notes.yml`.
// Fails a pull request whose description has no complete "What you'll notice"
// section, which is the text the alydash desk's Timeline shows for the merge.
//
// The section is read by `merge-notes.mjs` beside this file, the desk's own
// reader with its types taken out, so this check and the desk cannot disagree
// about what a section is. The rest is a copy of the desk repository's check
// (`scripts/src/merge-notes-check.ts` there), and that repository's
// tests/plugin/setup.test.ts pins the copy to the original, sentence for
// sentence.
//
// The description arrives in `PR_BODY`, set by the workflow as an `env:` value
// and never written into a script, because it is text anybody opening a pull
// request controls.
//
// Paths are looked up from the working directory, which in the workflow is the
// pull request's own checkout. `lstat` and `isFile`, so a symbolic link is not
// a file here: a link could point anywhere, and the screenshot has to be the
// committed bytes.

import { lstatSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readMergeNotes, screenshotPathProblem } from './merge-notes.mjs';

// The most the desk stores for one screenshot.
export const SCREENSHOT_MAX_BYTES = 2_000_000;

const SCREENSHOT_ABSOLUTE = (path) =>
  `The screenshot ${path} is an absolute path. Name it from the repository root, like docs/screenshots/<name>.png.`;
const SCREENSHOT_CLIMBS_OUT = (path) =>
  `The screenshot ${path} has a ".." segment, so it is not a path inside this repository.`;
const SCREENSHOT_NOT_A_FILE = (path) =>
  `The screenshot ${path} is not a file in this pull request. Commit it, or fix the path.`;
const SCREENSHOT_TOO_LARGE = (path, bytes) =>
  `The screenshot ${path} is ${bytes.toLocaleString('en-AU')} bytes, over the ${SCREENSHOT_MAX_BYTES.toLocaleString('en-AU')} byte limit.`;

const CLOSING_SENTENCE =
  "Fill in the \"What you'll notice\" section the way .github/pull_request_template.md lays it out, then save the description; this check runs again on the edit.";

// `fileSize(path)` is the size of the file at a repository path, or null when
// there is no file there.
export function checkMergeNotes(body, { fileSize }) {
  const reading = readMergeNotes(body);
  const problems = [...reading.problems];
  const paths = reading.ok ? reading.notes.screenshots : [];

  for (const path of paths) {
    const problem = screenshotPathProblem(path);
    if (problem === 'absolute') {
      problems.push(SCREENSHOT_ABSOLUTE(path));
      continue;
    }
    if (problem === 'climbs-out') {
      problems.push(SCREENSHOT_CLIMBS_OUT(path));
      continue;
    }
    const size = fileSize(path);
    if (size === null) problems.push(SCREENSHOT_NOT_A_FILE(path));
    else if (size > SCREENSHOT_MAX_BYTES) problems.push(SCREENSHOT_TOO_LARGE(path, size));
  }

  if (problems.length > 0) return { ok: false, problems, lines: [...problems, CLOSING_SENTENCE] };

  const shots =
    paths.length === 0 ? 'no screenshot needed' : `${paths.length} ${paths.length === 1 ? 'screenshot' : 'screenshots'} checked`;
  return { ok: true, problems, lines: [`The "What you'll notice" section is complete, ${shots}.`] };
}

const invoked = process.argv[1] ? realpathSync(process.argv[1]) : null;
if (invoked === realpathSync(fileURLToPath(import.meta.url))) {
  const result = checkMergeNotes(process.env.PR_BODY ?? null, {
    fileSize: (path) => {
      try {
        const stat = lstatSync(resolve(process.cwd(), path));
        return stat.isFile() ? stat.size : null;
      } catch {
        return null;
      }
    },
  });
  for (const line of result.lines) (result.ok ? console.log : console.error)(line);
  process.exit(result.ok ? 0 : 1);
}
