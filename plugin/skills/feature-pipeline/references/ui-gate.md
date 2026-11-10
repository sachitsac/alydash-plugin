# The UI gate

A run that changes what a browser draws is measured at Phase 7 and reviewed
at Phase 8, and Phase 8 does not close until every screen's report says
`Approve`. The rules the reviewer applies are in `ui-rules.md`. This file is
how the gate runs.

The gate needs nothing but this skill and the project's own Playwright. It
reads no other skill.

## Which runs need it

The script decides, from the files the run changed since the commit it
started from (uncommitted and untracked files count). A changed file is a rendered surface when its name matches `*.tsx`,
`*.jsx`, `*.css`, `*.scss`, `*.html`, `*.vue` or `*.svelte`. Test files
(`*.test.*`, `*.spec.*`, `**/__tests__/**`, `test_*.py`, `*_test.py`) and the run's own artifact root
are left out.

A renamed file counts on both sides: `page.tsx` renamed to `page.ts` is
still a page that changed.

**A run is judged by its own change.** The changed files are read against
the merge-base of HEAD with origin's default branch (with no origin default,
a local `main`, then `master`) as it is now, so a branch that was rebased
lists only its own files. When that merge-base is HEAD itself, the branch is
already merged or the run is on the default branch, and the script falls
back on the commit the run started from: `init` records on the ledger, as
`base`, the merge-base at that moment. So a branch that was merged before
Phase 8 closes, and a run made on the default branch and pushed, still list
what they changed. `init` never fails or waits long on this: when git or
origin cannot answer, no `base` is recorded.

A project that draws from other files, or has rendered files that are not
screens, says so in `ui-gate/config.json` at the repository root. Each list
it gives replaces the default list:

```json
{ "surfaces": ["apps/desk/**/*.tsx", "*.css"], "ignore": ["*.test.*", "apps/desk/emails/**"] }
```

**A run cannot loosen its own gate.** The file is read as it is at the
merge-base, the default branch's version, and the defaults apply when it is
not there. A `ui-gate/config.json` that the run itself added or changed,
committed or not, takes effect only for the runs after it. When a run did
change it, `ui-gate check` and Phase 8's check say so in one line that
starts `note:`, and refuse nothing on that account.

A run that changed a rendered surface needs a UI gate report, one for each
screen it reviewed, and Phase 8 cannot be `skipped` on it. A run that changed none says
so, once, and Phase 8 refuses the skip, naming the files, if one did change:

```
python3 "$PIPE" ui-gate skip <slug> --reason "<why this run draws nothing>"
```

When git cannot say what changed (no repository, no base to compare with),
Phase 8 is refused rather than passed. So is a run that has nothing ahead of
its base and no uncommitted or untracked file, with no `base` on its ledger
to fall back on: it may already be merged, or be on the default branch, and
that is not the same as having changed nothing.

## What the gate does not establish

Two things the check cannot see, so the run has to hold them itself.

- **That every screen the run touched was reviewed.** Nothing maps a changed
  file to a screen. One approving report for each screen that was reviewed
  closes Phase 8, however many screens the diff touched. So the reviewer
  lists, in its report's `notVerified`, any changed screen it did not review,
  and the dispatcher sends a reviewer for each screen the run touched.
- **That a measurement describes the code as it is now.** A measurement file
  is not tied to a commit. Measure after the last change to the screen, and
  measure again after a fix pass: a report approved on numbers taken before
  the fix says nothing about the page that ships.

## What the project supplies

Two kinds of file under `ui-gate/` at the repository root, committed.

**A screen file for each screen**, `ui-gate/<screen>.json`:

```json
{
  "screen": "run page",
  "route": "/runs/[id]",
  "root": ".run-page",
  "states": ["live", "needs-you", "finished"],
  "jobs": [
    "See at a glance whether the run needs me",
    "Steer it: stop it, or send it a message"
  ],
  "blocks": [
    { "name": "state and controls", "selector": ".run-page-aside", "serves": [1, 2] },
    { "name": "phase list", "selector": ".run-phases", "serves": [1] }
  ]
}
```

- `screen`: the screen's name. Measurement files and the report carry the
  same name.
- `route`: optional, for the reader.
- `root`: the selector every measurement is confined to. The shell around a
  screen is another screen, with a file of its own. `body` when absent.
- `states`: the states worth measuring, such as empty, live and finished.
  A state's name is lower-case letters, digits and hyphens (`needs-you`),
  since it is part of the name of the file its measurement is saved in. The
  screen file's own name is held to the same shape (`run-page.json`).
- `jobs`: what a person comes to this screen to do, in rank order, most
  important first. **A person writes the jobs. The reviewer never does**, and
  never reorders them: a reviewer who chose the jobs would be marking its own
  placement review. A UI run writes or updates them at Phase 1, with the
  person. A screen file with no jobs is refused.
- `blocks`: the parts of the screen placement is judged on. Each has a
  `name`, a `selector` inside the root, and `serves`: the numbers of the jobs
  it serves, counted from 1. A block that serves no job has an empty list.

**The project's rulings**, `ui-gate/rulings.json`: the design choices a
person has already settled, so the gate does not reopen them on every run.

```json
{
  "rulings": [
    {
      "id": "stop-is-plain",
      "choice": "Stop is drawn in ink, with no danger colour: stopping is a person's decision, not a failure",
      "where": "apps/desk/app/work/run-controls.tsx",
      "quote": "Stop is drawn in ink",
      "rules": ["COLOR-TREATMENT"],
      "covers": ["apps/desk/app/work/run-controls.tsx", "apps/desk/app/work/now-strip.css"]
    }
  ]
}
```

`where` is the file the choice is written down in: a path from the
repository root, with no `..` and not absolute. `quote` is a few words of
the comment there, and says the place in that file. `rules` are the rule ids
the choice settles. `covers` are the files it holds for.

The check looks for the quote in the whole of the `where` file, with case
and spacing set aside, and refuses the ruling when it is not there or is
there more than once: once is the one place that is ruled. So lines added
above the place move nothing, and a ruling whose comment is gone is caught.
A `where` still written `path:line` is refused, and the refusal says to
write the file alone.

Only a person adds a ruling. A reviewer that wants one says so in its
finding and leaves `ruling` null. The rulings are read as the run has them,
so a run that adds or changes one is not refused for it, and it is said:
`ui-gate check` prints a `note:` line naming the ruling ids that are new or
changed against the merge-base, and `ui-gate record` puts them on the board.

## Measuring, at Phase 7

Gate 7 starts the real app anyway. While it is up, measure every screen the
run touched, in each of its states. The module is `scripts/ui_measure.mjs`,
beside the script. It opens the page in four new browser contexts (desktop
light, desktop dark, phone at 390, narrow at 320), reads only inside the
screen's `root`, and writes failures, notes, the keyboard walk and the
placement numbers for each context.

Playwright's browsers must be installed for the measuring module; the
report check needs nothing but Python.

A page that needs no sign-in is measured from the command line. Run it from
the project, since Playwright is the project's own:

```
cd <the project, where @playwright/test is installed>
node "$(dirname "$PIPE")/ui_measure.mjs" --url http://localhost:3000/runs/abc \
  --screen ui-gate/run-page.json --state live \
  --out <root>/measurements/<date>-<slug>-ui-measure-run-page-live.json
```

`--url` and `--screen` are required. `--url` is an `http` or `https`
address, and no other kind is measured. `--state` names the state being
measured, and the check counts only a file that has one. `--storage-state`
takes a Playwright storage state file, for a session saved earlier. A
storage state belongs to the application being measured: do not hand the
module a session saved for another one. Without `--out` the result goes to
standard output. The module refuses to write `--out` through a symbolic
link.

A page that is meant to redirect is measured with `--allow-redirect` (the
`allowRedirect` option of `measureScreen`), which measures the page it ended
at. The module waits for the page to settle, 30 seconds unless the
`settleTimeout` option says otherwise (in milliseconds). A page that keeps
the network busy past that is refused with the module's own message, which
names the URL, the state and the context.

Measuring runs the project's own code, with the environment the command was
given. A project that is not your own is measured with the desk's token
unset (`ALYDASH_SESSION_TOKEN`).

A project with sign-in or seeding calls the module from its own harness
instead. Import `measureScreen` and hand it a Playwright `browser`:

```js
const { measureScreen } = await import(pathToTheModule);
const result = await measureScreen(browser, { url, storageState, screen, state: 'live' });
writeFileSync(out, JSON.stringify(result, null, 2) + '\n');
```

`screen` is the parsed screen file. The module imports nothing from
Playwright, so the harness signs in and seeds however the project already
does.

Name each file
`measurements/<date>-<slug>-ui-measure-<screen>-<state>.json`, under the
run's artifact root, and commit them. The check reads the files named
`<date>-<slug>-ui-measure-<anything>.json`, with the slug exact: the run
`scratch` does not read a file of the run `scratch-two`. It takes each
file's screen and state from the `screen` and `state` it holds, not from its
name.

What the check asks of the files:

- **A measurement is a measurement.** A file counts only when it has a
  `screen`, a `state` and a `contexts` object holding all four of
  `desktop-light`, `desktop-dark`, `phone` and `narrow`, each with a
  `failures` list and an `innerWidth`. Anything less is refused by name.
- **Every state is measured.** Each state the screen file lists in `states`
  has a measurement file of that screen. A missing state is refused by name.
- **What is measured is reviewed.** A measurement file whose `screen` no
  report of the run is for is refused: measured but not reviewed. A screen's
  name is matched exactly, case and spacing included, so `Run Page` is not
  `run page`.

The module refuses rather than report a clean result it did not earn. It
stops, and reports nothing for the screen, when:

- the page reads a width other than the one the context asked for, wider or
  narrower, in any of the four contexts. A phone result from a window that
  is not a phone is not a result, and a caller's own context cannot borrow
  one of the four names at another width;
- the `root` selector matches nothing, matches more than one element, or
  matches an element that is hidden, has a box with no size, or holds no
  text that is drawn;
- the page answers with a status of 400 or more;
- the page ends on a different origin or path than the one asked for: a
  redirect, for example to a sign-in page when the stored session has
  expired. A sign-in page measured as the screen is not a result;
- the page does not settle within the settle timeout.

Fix the harness or the screen file; do not work round it.

The keyboard walk reads at most 200 stops. On a page with more, the context
carries a note that the walk was cut short. Where the walk enters a frame or
a shadow root it cannot follow, it notes the place and says the stops inside
were not read: a note, not a failure, and the reviewer says under
`notVerified` what was not walked.

A box cut by a clipping ancestor, sideways or downwards, is a note for the
reviewer and not a failure: the module cannot tell a cut from a strip that
is meant to scroll. A keyboard stop's name is the page's own text, cut at 40
characters.

Contrast is measured against one colour. Text over an image or a gradient
ground has no one colour behind it, so it is noted as not measured, not
failed, and the reviewer judges it from the source.

A failure is something the report must answer. A note is something the
reviewer may weigh, and needs no answer.

## Reviewing, at Phase 8

Dispatch one UI reviewer beside the other reviewers. It reviews one screen
and writes one report. A run that touched two screens gets two reviews. Copy
the brief below into the reviewer's prompt, with the paths filled in. Give it
this skill's `references/ui-rules.md` by its full path.

```
You are the UI gate's reviewer for one screen: <screen>.

Read, in this order:
1. The run's diff against the merge-base.
2. The screen file, ui-gate/<screen>.json: its root, its states, its jobs in
   rank order, and its blocks with the jobs each serves.
3. The project's rulings, ui-gate/rulings.json, if it has one.
4. Every measurement file for this screen:
   <root>/measurements/<date>-<slug>-ui-measure-<screen>-<state>.json
5. The rule book, references/ui-rules.md, in full. It is the only rule book.
   Do not load another skill for rules, and do not apply rules it does not
   have.

Then do four things.

A. Answer every measured failure. For each distinct pair of rule and where
   in the failures of any context of any of the screen's measurement files,
   find the cause in the source and write a finding with the source
   "measured" whose "answers" lists that where, exactly as the file spells
   it. One finding may answer several places when one cause explains them.
   Notes need no answer. You may not drop a measured failure because you
   disagree with it: say so in "why" and give it the severity you can
   defend. An automatic HIGH stays HIGH. The keyboard walk is done at
   desktop only, so the phone and narrow contexts carry none. A touch-size
   note raised as a finding is "source", not "measured": a note is not a
   failure, and there is nothing for it to answer.

B. Review the six domains from the source, for what the script cannot see:
   Accessibility, Layout, Writing, Typography, Colors, UI. Read the changed
   files and what they render. On a review with a diff, the files in scope
   are the changed rendered surfaces and what they import that draws on the
   screen. These findings have the source "source" and carry no "answers".
   Before reporting A11Y-MOTION, look for a global reduced-motion rule in
   the project's stylesheet. WRITE-DESTRUCTIVE is about ending, deleting or
   overwriting something, and an ordinary Send is not it. WRITE-ERROR on a
   network failure counts as the main path. When one cause breaks two
   rules, the finding carries the rule with the higher severity and names
   the other in "why".

C. Do the placement review. Take the placement table from the desktop-light
   and phone contexts and hold it against the jobs in the screen file, by
   the four PLACE rules. Do not write, reorder or reinterpret the jobs. If
   they look wrong, say so under notVerified and review against them as
   written. State every block, not only the ones that fail: "placement"
   has one entry for each block of the screen file, with the jobs it serves
   as the file gives them, whether its position and room fit that rank
   ("fits"), and one sentence saying where it sits and why. A block that
   does not fit points, in "finding", at the Placement finding that says
   why, counted from 1; a block that fits has "finding" null. A block that
   was drawn in no state is still listed, and its note says so. When the
   cap left out the finding a block would point at, the block still does
   not fit: "fits" false, "finding" null, "leftOut" true, and the note
   names the PLACE rule. That happens only in a report that holds 15
   findings, and never for PLACE-MISSING, which is automatic HIGH and so
   is never cut. Several blocks may point at one finding. A block
   that breaks two placement rules points at the one with the higher
   severity and names the other in its note. "fits" is judged across every
   measured state and size: a block that does not fit in any one of them
   does not fit, and the note says where. A block whose only objection is
   ruled fits, and the note names the ruling. A block serving no job fits
   unless it takes more of the first screen than a block serving job 1.

D. Cite a ruling only when it covers that rule and that file. A ruling has
   "rules" and "covers": the finding's rule must be in the one and its file
   in the other. A ruling on one rule does not answer another: a ruling that
   a button stays plain (COLOR-TREATMENT) says nothing about whether it asks
   before it destroys (WRITE-DESTRUCTIVE). A ruled finding stays in the
   report, with "ruling" set to the ruling's id. Never invent a ruling id.
   And a ruling the code contradicts is not a ruling for that finding: cite
   a ruling only when the code under review does what the ruling's "choice"
   says and the finding objects to that choice. A finding that the code
   departs from a recorded choice is never ruled, and its "why" names the
   ruling it departs from.

Evidence, not taste. Every finding names path:line and says what is there
now. One root cause is one finding. At most 15 findings. Answers to
measured failures and automatic HIGH findings are never cut by the cap. The
cap cuts the lowest severity first, and notVerified says how many were left
out. A domain all of whose findings were cut keeps the coverage result
"findings", and its "inspected" says "left out by the cap". The cap leaves
nothing out of a report that holds fewer than 15 findings: a finding you
made goes in the report. When the
findings that cannot be cut alone exceed 15, the report holds the first 15
by reach (the ones that touch the most of the screen first), the verdict is
Block, and notVerified says how many were left out.

Anything you could not render or check goes under "notVerified", in words:
a state that was not measured, a screen reader pass, 200% zoom, forced
colours, and any screen the diff changed that this review did not cover. A
check that was not made is never a finding and never "clear".

Write one JSON file, and nothing else:
<root>/measurements/<date>-<slug>-ui-gate-report-<screen>.json
If it exists, this is a re-review: overwrite it. Do not edit source files.
The shape is given below.
```

Paste the report's shape, from the next section, under the brief.

## The report

One file a screen, `measurements/<date>-<slug>-ui-gate-report-<screen>.json`,
overwritten on a re-review so a screen never has two. The check reads the
files named `<date>-<slug>-ui-gate-report.json` or
`<date>-<slug>-ui-gate-report-<anything>.json`, with the slug exact.

```json
{
  "screen": "run page",
  "screenFile": "ui-gate/run-page.json",
  "coverage": [
    { "domain": "Accessibility", "inspected": "run-controls.tsx, run-page.css, the keyboard walk in three states", "result": "findings" },
    { "domain": "Layout", "inspected": "the phone and narrow contexts, run-page.css", "result": "clear" },
    { "domain": "Writing", "inspected": "every string in run-controls.tsx and run-view.tsx", "result": "findings" },
    { "domain": "Typography", "inspected": "run-page.css against the type tokens", "result": "clear" },
    { "domain": "Colors", "inspected": "contrast in light and dark, token use in run-page.css", "result": "findings" },
    { "domain": "UI", "inspected": "transitions and states in run-phase.css", "result": "clear" },
    { "domain": "Placement", "inspected": "the placement table at desktop and phone against the two jobs", "result": "clear" }
  ],
  "findings": [
    {
      "severity": "HIGH",
      "domain": "Accessibility",
      "rule": "A11Y-FOCUS",
      "location": "apps/desk/app/runs/[id]/run-page.css:88",
      "before": "outline: none on .run-page-item, with nothing in its place",
      "after": "delete the rule, so the browser's own ring shows",
      "why": "a keyboard user cannot see which phase is focused",
      "source": "measured",
      "answers": ["a.run-page-item"],
      "ruling": null
    },
    {
      "severity": "MEDIUM",
      "domain": "Colors",
      "rule": "COLOR-TREATMENT",
      "location": "apps/desk/app/work/run-controls.tsx:101",
      "before": "Stop is drawn in ink, like any other button",
      "after": "a danger treatment on Stop",
      "why": "Stop ends the run",
      "source": "source",
      "ruling": "stop-is-plain"
    },
    {
      "severity": "MEDIUM",
      "domain": "Writing",
      "rule": "WRITE-ERROR",
      "location": "apps/desk/app/work/run-controls.tsx:64",
      "before": "the fallback error says only that something went wrong",
      "after": "say the message was not sent and offer to try again",
      "why": "shown only when the server gave no reason, so MEDIUM",
      "source": "source",
      "ruling": null
    }
  ],
  "placement": [
    { "block": "state and controls", "serves": [1, 2], "fits": true, "finding": null, "note": "first in the page at desktop and at phone, a fifth of the first screen and full, which fits jobs 1 and 2" },
    { "block": "phase list", "serves": [1], "fits": true, "finding": null, "note": "second, on the first screen at both sizes, which fits job 1" }
  ],
  "notVerified": ["a screen reader pass", "200% zoom", "the empty state, which was not seeded"],
  "verdict": "Block"
}
```

Field by field:

- `screen`: the screen's name, as its screen file and its measurement files
  spell it.
- `screenFile`: the screen file's path from the repository root, with no
  `..` and not absolute. It must exist, be for this screen, and list its
  `jobs`.
- `coverage`: seven rows, one for each domain, each named once:
  `Accessibility`, `Layout`, `Writing`, `Typography`, `Colors`, `UI`,
  `Placement`. Each row has a `domain`, a `result` and, unless nothing was
  reviewed, `inspected`: what was looked at, in words.
  - `clear`: reviewed, and no finding names the domain.
  - `findings`: reviewed, and at least one finding names it.
  - `not-reviewed`: not looked at. No finding may name it.
- `findings`: a list, at most 15 findings, and it may be empty. Each has:
  - `severity`: `HIGH`, `MEDIUM` or `LOW`, by the scale in `ui-rules.md`. A
    rule that `ui-rules.md` marks automatic HIGH is `HIGH`.
  - `domain`: one of the seven, the rule's owner.
  - `rule`: the rule's id from `ui-rules.md`, and no other.
  - `location`: `path:line`, the path from the repository root, to a file
    that is there.
  - `before`: what is wrong, as it is now. This is the line the run's board
    shows.
  - `after`: the fix, the cheapest that works.
  - `why`: what it costs the person using the screen.
  - `source`: `measured` when the finding answers measured failures,
    `source` when it was read from the code.
  - `answers`: for a `measured` finding only, and required there: the list
    of the `where` strings of the measured failures it accounts for, each
    spelled as the measurement file spells it. Every place listed must be
    one where that rule failed on this screen. A `source` finding carries no
    `answers`.
  - `ruling`: null, or the id of a ruling in `ui-gate/rulings.json` that
    covers this rule and this file.
- `placement`: one entry for each block of the screen file, the ones that
  fit as well as the ones that do not. Each has:
  - `block`: the block's `name`, as the screen file spells it.
  - `serves`: the job numbers, copied from the screen file.
  - `fits`: true when the block's position and room match the rank of the
    jobs it serves, false when they do not.
  - `finding`: null when it fits. When it does not, the place in `findings`,
    counted from 1, of the Placement finding that says why. Every Placement
    finding is pointed at by an entry, so it always names its block.
  - `leftOut`: optional, true or false. True only on a block that does not
    fit and whose Placement finding the cap left out: `finding` is then
    null, the `note` names the rule's id (one of the `PLACE-` ids), and
    `notVerified` is not empty. It stands only in a report that holds 15
    findings, and its note may not name an automatic HIGH rule
    (`PLACE-MISSING` among them): the cap never cuts one. A report is never made to say a block fits
    when the reviewer found it does not.
  - `note`: one sentence on where the block sits and why that fits or does
    not. A block the measurements show absent in every state is still
    listed, and its note says it was not drawn.
- `notVerified`: a list of the checks that could not be made, each in words.
  It must be there, and may be empty. An entry may not repeat a finding.
- `verdict`: `Approve` or `Block`.

## The verdict

The verdict follows from the findings. The reviewer does not choose it.

- **`Block`** when at least one HIGH finding has no ruling.
- **`Approve`** when no HIGH finding is without a ruling and every domain was
  reviewed.

A ruled finding does not block. A domain marked `not-reviewed` cannot be
approved, so a report is not finished until all seven are reviewed. MEDIUM
and LOW findings never block: they are recorded, and the review rules them
fix-now or defer like any other finding.

## Checking it

```
python3 "$PIPE" ui-gate check <slug>
```

Reads every report of the run and prints a line for each: a tick with the
verdict and the findings counted by severity, or a cross for each problem.
It exits 2 when anything is wrong and writes nothing. Phase 8, and
`hold <slug> 8`, run the same check. A recorded `ui-gate skip` never
switches it off: a run that has a skip and a report has the report checked
like any other. What it refuses:

- A report that is not well formed: a finding missing a field, a severity,
  source or result outside its list, a location not shaped `path:line`, more
  than 15 findings, a domain missing or named twice, `notVerified` missing.
- Coverage that contradicts the findings: `clear` with a finding in the
  domain, `findings` with none (unless the cap cut them all and the row's
  `inspected` contains the words "left out by the cap"), a finding in a
  domain marked `not-reviewed`.
- **Placement `clear` beside a block that does not fit.** When any
  `placement` entry has `fits` false, pointing at a finding or `leftOut`,
  the Placement coverage row's result is `findings`.
- **A cap that cut nothing.** The cap leaves nothing out of a report that is
  not full. A `leftOut` entry is refused unless the report holds 15
  findings, and so is one whose note names an automatic HIGH rule. A
  coverage row that says "left out by the cap" with no finding in its domain
  is refused unless the report holds 15 findings or the verdict is `Block`.
  The refusal names the count.
- **A finding at a file that is not there.** A `location` names a regular
  file inside the repository.
- **A file the gate may not read.** The reports, the measurement files, the
  rulings, a screen file and the file a ruling's `where` names are read only
  when the real path, with every symbolic link resolved, is inside the
  repository (or, for a run's own files, its artifact root), the file is a
  regular file, and it is not over 5 MB. A link that leads out, a device, a
  pipe and a file over the ceiling are each named and not read.
- A verdict that does not follow: `Approve` with an unruled HIGH or a domain
  not reviewed, `Block` with no unruled HIGH.
- **A rule the rule book does not have, or one it marks automatic HIGH at a
  lower severity.** The refusal names the rule. `WRITE-ERROR` and
  `UI-MOTION` are HIGH only in the case their rows name, so they may be
  lower.
- A ruling that does not hold: an id that is not recorded, a ruling that
  does not cover the finding's rule or its file, a `where` that names no
  file in the repository, names a line, or is outside the repository, a
  `quote` that is missing, not in the `where` file, or in it more than once.
- A screen that was not measured, a screen file that is missing, outside the
  repository or has no jobs.
- **A block of the screen file not stated.** `placement` missing, a block
  with no entry or two, an entry for a block the file does not have, a
  `serves` that differs from the file's, a `fits` that is not true or false,
  a blank `note`, a block that does not fit with no Placement finding to
  point at (unless `leftOut` says the cap cut it, with the rule named in
  the note), a block that fits pointing at one or carrying `leftOut`, an
  unruled Placement finding no entry points at.
- A measurement file that is less than a measurement, a state the screen
  file lists that was not measured, a measurement no report reviews. See
  Measuring, above.
- **A measured failure nobody answered.** Every distinct rule and where
  among the screen's failures, across all its states and contexts, must be
  in the `answers` of a `measured` finding with that rule. The refusal names
  the rule, the where and the file.
- **A measured finding that measures nothing.** An `answers` entry that
  matches no failure of that rule on this screen is refused, and so is a
  `measured` finding whose rule did not fail on this screen at all. Another
  screen's failure does not count.

Fix the report, not the check. A refusal about a measured failure means the
reviewer has more to do. It never means the measurement file should be
edited.

## Putting the findings on the board

```
python3 "$PIPE" ui-gate record <slug>
```

Writes the findings of every report into Phase 8's table, in place of the
findings recorded before, so run it again after a re-review. The columns are
`Severity, Domain, Where, Finding, Standing`. `Domain` is the finding's
domain after the record's mark, as in `UI gate: Layout`. `Where` is the
finding's location. `Finding` is its `before` followed by its rule id in
brackets. `Standing` is `ruled: <ruling id>` or `open`. Rows are sorted by
severity, then domain. A cell is cut to 500 characters and keeps its rule
id.

The table is shared with the code review. On a UI run the review writes its
own findings into the same table with `table`, under the same five columns,
with the Domain `Code` and its ruling under Standing. Record owns only the
rows whose Domain starts with `UI gate: `, exactly as written. It takes
those out, puts the current ones where they were (after the other rows when
there were none), and leaves every other row as it found it, in place,
whatever its Domain says: a hand-written row under `UI` or `layout` is the
review's and is never touched. So a finding that was fixed leaves the table
on the next record, and the review's rows never do.

When the run added or changed rulings in `ui-gate/rulings.json`, the first
of the record's rows has the Domain `UI gate: rulings` and names them, so
the run page shows it.

A table holds 50 rows, and every row counts. The other rows are all kept.
When the findings do not fit in the room left, as many as fit are kept and
the last of them is a row with the Domain `UI gate: left out` that says how
many findings were left out. The closing line then counts what was written:
`recorded 1 of 3 findings`. When the other rows already fill the table, so
that not even that row fits, record refuses, says so, and writes nothing:
move some of the other rows into the review doc and record again.

It refuses, with exit 2, whatever `ui-gate check` refuses, and records
nothing. A blocking report that is well formed is recorded: that is how the
board shows what blocks.

If Phase 8 already has a table with other columns,
such as the review's own `Finding, Reproduced, Ruling`, record refuses,
names the table, and records nothing. To put the gate's findings in its
place:

```
python3 "$PIPE" ui-gate record <slug> --replace
```

It says what it replaced, and the run log keeps the line. A record reports
to the desk at once, as a note does, so the run page shows the table without
waiting for another call. It does not push the branch.

## What Block means for the run

Phase 8 stays open. `phase <slug> 8 done` and `hold <slug> 8` both refuse,
and name the report. There are three ways on, and the reviewer's opinion is
not one of them:

1. **Fix it.** Each fix is new code: a failing test first where one can be
   written, then the fix. Measure the screen again, since the old
   measurement describes the old page. Review again: the reviewer overwrites
   the report. Record again.
2. **A person rules.** When a HIGH finding is a choice the person made, they
   add a ruling to `ui-gate/rulings.json`, naming the file where the choice
   is written down and quoting the comment there. The reviewer then cites it. The agent does not
   write rulings for itself.
3. **A person overrules.** `phase <slug> 8 done --force "<their decision>"`
   closes past the gate, and the board shows the reason in amber. This is
   for a decision the user made, never for a gate that is inconvenient.

A report that says `Approve` and was not earned is the failure this gate
exists to stop. The check finds a missing answer. It cannot find a false
one: only the reviewer reading the source can.
