# The phases

What each phase does and what it must leave on disk. `SKILL.md` has the
ledger calls; `gates.md` has the four gates in full; `prototype.md` and
`tdd.md` own Phases 2 and 5.

Each phase's short purpose and its usual steps, in plain words, live in
`scripts/pipeline.py` (`PHASE_PURPOSE`, `PHASE_STEPS`): `phase <slug> <n>
start` stamps the purpose and seeds the steps, and the desk shows both in the
phase's Story view. Reword them there, not here. Every phase closes with a plain
line (`note <slug> <n> --plain "..."`); `board.md` § Writing for the reader
says how to write it.

## §0 Frame

Before `init`, look for work already done on the item: an open or merged pull
request that names its key, a branch, a worktree. Another session may be
running in parallel, and a note that says "X is next" can be stale. If a run
exists, show it to the person and ask whether to resume, review or replace it.
After a context clear, an unexpected change in the checkout may be a
background agent still working: look for one before reverting anything.

Before any design, write `specs/<date>-<slug>-goal.md`: what changes for a
user, and **what observable evidence would prove it**, each item naming how
it would be observed. Not "comparison view implemented" (a state of the
codebase) but "a signed-in user sees two results side by side, picks one,
and the choice is stamped in the DB; observed by loading the page and
querying the row." Web: load the page. Mobile: a device in airplane mode.
Batch: play the output file. CLI: run it and time it. Backend: trigger it
and query the state.

When the run has a work item, read its acceptance criteria first
(`read_item`): they are what done looks like, written by or agreed with the
person, and the run is finished when each is met and shown. Copy them into the
goal doc word for word, and make each one an evidence item or part of one. A
criterion this run will not meet is named there with the reason, never
dropped. An item with no criteria, or criteria too loose to check, gets them
proposed (`propose_criteria`) before the design starts, so the run and the
person agree on done before anything is built.

Then ask of the set, not of each line: **if every one were ticked, would the
person say it is done?** Criteria are often true and still short of done. A
set that covers done has all of these, and one that lacks any gets the
missing criteria proposed the same way:

- **The outcome, as its user meets it.** What they can now do or see, in the
  place they would do it. Not a state of the code ("the table exists").
- **How each would be seen.** A criterion that cannot name its observation
  (load the page, run the command, read the row) is a wish.
- **What must not happen.** The refusal, the empty state, the other user who
  must see nothing, the thing left unchanged. A wrong build usually passes
  the happy line and fails here.
- **Where it counts as done.** Merged, deployed, or seen working where people
  use it: say which, as a criterion, when being built is not enough.
- **What is left out**, named in the item's context and not as a criterion,
  so a builder does not widen the work to reach a done nobody asked for.

This is what Gate 7 checks against. Put the evidence items in the Phase 0
table (`#, Evidence, Observed by, State`) and amend the goal doc when the
brief changes mid-run; the amendment is a `decided` entry with its date.

Frame also tells the story's beginning, for a reader who was not there:

```
python3 "$PIPE" note <slug> 0 --backstory "<why this run exists, in everyday terms>" \
        --fixed-means "<one proof, in plain words>" --fixed-means "..." \
        --wont-change "<one thing this run leaves alone>"
```

## §1 Brainstorm

Explore the design space; land on an approach and its rejected alternatives.
Any optional domain skill goes here, before the design settles. Push reading
into subagents: a design that needs twenty files read is a subagent report,
not twenty Reads. A run that will change a screen writes or updates the jobs
in that screen's file (`ui-gate/<screen>.json`, `ui-gate.md`) here, with the
person: what they come to the screen to do, in rank order. Write
`specs/<date>-<slug>-design.md`. Board: `decided`
for what changed the design, `measured` for anything counted, and the
rejected alternatives as a table or `decided` entries with their why.

## §2 Prototype

`prototype.md`. Always produces an artifact or a recorded, reasoned skip.

Phase 2 holds by default, so it closes through `hold <slug> 2`, not
`phase <slug> 2 done`, which refuses it with or without a desk. While it waits
it shows as "Waiting on you" on the desk, and the person's answer and note land
in the run log as a decision (`SKILL.md` § How a run waits). Commit what the
phase recorded first: hold pushes the branch, and refuses (exit 2, asking
nothing) a file the desk could not open, such as one not committed or not
pushed, and names the fix for each. When nobody can
answer on the desk (`hold` exits 4 or 5), ask in chat, then close it with
`phase <slug> 2 done --force "<their ruling>"`. Any other phase a run holds
(`init --hold`) closes the same way. Gate 8 holds by default too (§8).

## §3 Plan

Break into **vertical slices**: each task is one thin capability through
every layer with its test named first, ordered thinnest-first so a walking
skeleton lands before anything widens. Every task states which side of the
hexagonal boundary its logic sits on (`build-standards.md`); a task that
cannot say is not ready. Write claims about existing code as instructions
("read X and confirm whether it fetches the runs"), never assertions.

Every slice says which of the item's criteria it closes, word for word, and
every criterion the run means to meet is closed by some slice. A criterion no
slice closes is a hole in the plan; a slice that closes none is asked why it
is in the run. `Closes` in the table is those criteria.

Write `plans/<date>-<slug>.md`. Board table: `Slice, Builds, Test written
first, Closes`.

## §4 Gate: verify the brief

`gates.md` §1. Extract every sentence asserting what existing code does; open
the source; rule CONFIRMED / PARTIALLY CONFIRMED / NOT CONFIRMED /
UNVERIFIABLE; append under `## Brief verification`. Any NOT CONFIRMED stops
the run and goes to the user; close with `--force "<their decision>"` once
they have ruled. Board table: `Claim, Verdict, Carried into the slices`.

## §5 Dispatch

One worktree per wave. Copy the implementer contract from
`dispatch-contracts.md` verbatim into every prompt, and `tdd.md`'s task
report shape. Every prompt also carries, under a heading `What done looks
like`, the criteria its slice closes, word for word from the item, so the
builder has them while building and not only the reviewer afterwards. One slice per implementer, disjoint files, red pasted before
green, a control broken and seen red. Reports go to
`measurements/<date>-<slug>-S<n>-report.md`; the check for closing Phase 5
looks for a fenced failing block in each. Run the **full** suite before
calling a wave green; scoped suites have hidden repo-wide guards. The
phase's usual steps are the wave's, not one slice's: tick each as the wave
goes, and add a step for each slice with `step --add` when the page should
show the slices.

Board table: `Slice, Red pasted, Control broken, Went wrong, State`. One
line to the user when a wave is dispatched, one when it lands.

## §6 Gate: break the guards

`gates.md` §2. Every new or modified guard: break it, observe red, revert,
paste the output. Then two at once on the same surface. Start with the guards
this wave wrote. Residuals become guards, each shown red first. Write
`measurements/<date>-<slug>-residuals.md`. Board table: `Mutation, Result,
Residual`.

Two things reading does not find. Run the compound: change the thing and
delete its assertion, which is what shows a guard that was never there. And
measure a floor, never guess it: a count the guard requires is read off the
live number. Before a mutation, copy each file to scratch under its full
path, and restore from that copy, never with `git checkout`, which eats
uncommitted work.

## §7 Gate: goal achieved

`gates.md` §3. Start the real artifact and check every Phase 0 evidence item
from the outside, as a user not told where the feature is. Screenshot each
surface, read the console, confirm the request and the row. Write
`measurements/<date>-<slug>-evidence.md` with the images beside it; an item
that could not be observed is listed as unverified, never omitted. A run that
changed a rendered surface also measures each screen it touched while the app
is up, and commits the measurement files (`ui-gate.md` § Measuring). Board
table: `#, Observed, State` with `observed`, `pending`, or `unverified`.

Observe the shipped thing, not a stand-in built for the check: the
migration's own SQL and not a paraphrase of it, the real viewport size, the
accessibility tree beside the screenshot. A green suite over a fake says
nothing about whether an integration ever ran, so look for its trace (a log
row, a request). Say which environment each observation came from: an empty
table is evidence about that database only.

## §8 Gate: review the diff

`gates.md` §4. Whole diff against the merge-base; the reviewers the project
mandates plus a correctness pass, in parallel, each given the diff and the
goal. Reproduce every finding with a control before ruling it blocks /
fix-now / defer by consequence. Fixes are new code: red first, then re-run
Gate 6 over them. Where a finding invalidates earlier evidence, amend the
evidence doc. Write `measurements/<date>-<slug>-review.md`, and beside it
`measurements/<date>-<slug>-review.json` (its shape: `gates.md` § Gate 4),
then run `review record <slug>`: Phase 8 does not close or hold without the
record, made at HEAD, which is what the desk draws Gate 8 from (after a
send-back and a fix commit, record it again before the next hold). Gate 8 holds by default
(`init`'s `--hold` is `2,8` unless it says otherwise), so close it with
`hold <slug> 8`: the person reviews the delivery on the desk's Review the
delivery sheet, opened from Waiting on you, and approves or sends it back
with a note, which reopens Phase 8 with the note as its scene. A run begun
with `--hold 2` or `--hold none` closes Phase 8 with `phase <slug> 8 done`,
and a note sent back from the sheet then reaches it as a message from the
desk. Board table:
`Finding, Reproduced, Ruling`. A run that changed a rendered surface also
dispatches the UI reviewer, one for each screen, and closes only when every
report says Approve (`ui-gate.md` § Reviewing). On such a run the phase's one
table is shared and has the columns `Severity, Domain, Where, Finding,
Standing`: the review writes its own findings into it with `table`, each row's
Domain `Code`, and `ui-gate record` writes the reports' findings beside them
under a Domain that starts with `UI gate: `, leaving every other row alone. A
run with no rendered surface keeps the review's table as above.

Hold every fix until every reviewer has reported, then rule on each finding
once and fix in one pass: a reviewer reading a tree that changes under it
reports defects already fixed and misses new ones. When the change adds a
rule or a refusal, one reviewer walks the real flow end to end by hand,
follows every printed fix literally, and answers "what other command reaches
the same state": mutation misses the side doors through other commands.

## §9 Finish

Finish the branch; do not commit, push, open or merge anything the user has
not asked for. A merge to the default branch is often a deploy, and here a
migration too: never merge unattended, leave the pull request open, and when
several are open write the order to land them in. Pushes cost the person CI
minutes: prove the branch locally, then push once, and land a verified stack
as its top pull request. Write `handoffs/<date>-<slug>-resume.md`: what shipped, what
was deferred, what must happen outside the merge, what bit this run,
artifacts by path. If the repo ships through pull requests, draft the PR
description for a reviewer who was not here. When the repo has
`.github/pull_request_template.md`, follow its sections in order and fill
every one, screenshot included. Otherwise cover what changes for a user and the
evidence, the decisions with their why, what Gate 8 found and what stays
open, what must happen by hand. On a run that reports to a desk the
description opens with the `## What you'll notice` section and carries a
`Ships:` line whether or not the repo has a template, deferrals are proposed
as items, and each by-hand step is written under **By hand:** in What you'll
notice (Before merging, After the deploy, or After the deploy, check, with
`(KEY criterion N)` for a check), which the desk raises on Needs you
(`SKILL.md` § Working with the desk). Before telling the person a pull request
is ready, run `python3 "$PIPE" ready <slug> --item KEY --pr N`, one `--item`
for every key on the Ships line. It also reads the pull request's description
(`gh pr view N`), so a before-merging step written there and not yet answered
on the desk counts, read by the desk or not. It is ready only on exit 0; exit 3
lists the before-merging steps still open, and exit 4 means the desk or the
description could not be read. Board: `produced` for the resume doc and the
branch, `next` for the first thing the next session does, and the Deferred
tab filled.

Then write the run's short version, the band at the top of its desk page, in
plain words, and name every code the run used:

```
python3 "$PIPE" story <slug> --happened "<what happened, one line>" --happened "..." \
        --stands "<where it stands, one line>" --needs-you "<what waits on Sachit>|<why>"
python3 "$PIPE" story <slug> --term "S3=the third slice of the build"
```
