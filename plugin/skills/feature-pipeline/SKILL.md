---
name: feature-pipeline
description: Run a feature from goal to shipped branch through ten phases with a mandatory prototype step, four verification gates, a scripted run ledger, and a fixed progress board with per-phase time, token and cost telemetry. Use when building or planning a feature, running a wave of fixes, or when the user says "plan this feature", "run the pipeline", "let's build X", or "resume <slug>". Owns the whole arc - frame, brainstorm, prototype, plan, verify, dispatch, break the guards, prove the goal, review, finish.
argument-hint: "<what you're building>  |  resume <slug>"
---

# Feature pipeline

Ten phases, four gates that block, one ledger. The gates exist because the same
defect re-enters at the same seam every wave: **a claim accepted without
construction.** Every gate constructs the disproof instead of reading the claim.

This skill is self-contained. Everything load-bearing is in `references/`; no
sibling skill has to exist for a run to be correct. The script in `scripts/`
owns run state, the board, the telemetry and the boundary message, so those
are never improvised.

```
PIPE="${CLAUDE_PLUGIN_ROOT}/skills/feature-pipeline/scripts/pipeline.py"   # python3, stdlib only
```

## The arc

| # | Phase | Read | Produces | Clear after |
|---|---|---|---|---|
| 0 | Frame | `references/phases.md` §0 | `specs/<date>-<slug>-goal.md` | no |
| 1 | Brainstorm | §1 | `specs/<date>-<slug>-design.md` | yes |
| 2 | **Prototype** (never silently skipped) | `references/prototype.md` | a running thing or a committed variants page, shown in the desk + `## Phase 2 verdict` in the design doc | yes |
| 3 | Plan | §3, `references/build-standards.md` | `plans/<date>-<slug>.md` | yes |
| 4 | **Gate: verify the brief** | `references/gates.md` §1 | verdicts appended to the plan | yes |
| 5 | Dispatch, test-first | §5, `references/tdd.md`, `references/dispatch-contracts.md` | `measurements/<date>-<slug>-S<n>-report.md` with red pasted | per wave |
| 6 | **Gate: break the guards** | `references/gates.md` §2 | `measurements/<date>-<slug>-residuals.md` | yes |
| 7 | **Gate: goal achieved** | `references/gates.md` §3; a UI run also measures its screens, `references/ui-gate.md` | `measurements/<date>-<slug>-evidence.md` + screenshots, with the browser suite's totals; on a UI run, `measurements/<date>-<slug>-ui-measure-<screen>-<state>.json` | yes |
| 8 | **Gate: review the diff** | `references/gates.md` §4; a UI run also runs the UI gate, `references/ui-gate.md` | `measurements/<date>-<slug>-review.md` + `measurements/<date>-<slug>-review.json`, recorded with `review record <slug>`, + fixes; on a UI run, `measurements/<date>-<slug>-ui-gate-report-<screen>.json` | yes |
| 9 | Finish | §9 | `handoffs/<date>-<slug>-resume.md`, PR text if the repo uses PRs | yes |

Artifact root defaults to `docs/superpowers/`. If the repo has its own docs
convention, pass `--root` to every script call and say which you picked. Every
artifact is committed to the repo, never `/tmp`.

## The ledger drives everything

At Phase 0, before any design work:

```
python3 "$PIPE" init <slug> --title "<Name>" --goal "<one sentence: what changes for a user>"
python3 "$PIPE" init <slug> --title "<Name>" --goal "..." --hold 2,8   # phases that wait for your review; default 2,8, or 2, or none
python3 "$PIPE" set  <slug> holds 2,8                                  # change them later (or none); a held phase closes only through hold
python3 "$PIPE" init <slug> --title "<Name>" --goal "..." --budget 5   # what the run may spend, in dollars, before the desk asks a person
python3 "$PIPE" set  <slug> budget 10                                  # before the first boundary: the desk takes the budget from the run's first beat only
```

At **every** phase boundary, and nowhere else:

```
python3 "$PIPE" phase <slug> <n> start                       # entering: purpose stamped, usual steps seeded, the first step marked doing
python3 "$PIPE" note  <slug> <n> --scene "<what we know coming in, what this answers>"
python3 "$PIPE" step  <slug> <n> 1 doing --sub "<what it is doing>"   # as each step starts
python3 "$PIPE" step  <slug> <n> 1 done  --sub "<what came of it>"    # as it finishes, not at the end; the next todo step moves to doing
python3 "$PIPE" step  <slug> <n> 2 dropped --sub "<why it was not done>"   # a step that will not be done; the reason is required
python3 "$PIPE" note  <slug> <n> --plain "<one sentence a newcomer understands>" --lede "..." \
        --number "18 of 18|leaked before the fix" --option "A|chosen|why" --option "B|rejected|why" \
        --assumed "text|what came of it" --left-out "..." --doc "path|title|about|in short"
python3 "$PIPE" note  <slug> <n> --summary "..." --next "..." \
        --decided "..." --measured "..." --wrong "what => did" --overruled "..." --produced "path=note"
python3 "$PIPE" table <slug> <n> --columns "Slice,Red pasted,State" --row "S1|64 tests after|green"
python3 "$PIPE" story <slug> --term "S1=the first slice"      # every code the run uses
python3 "$PIPE" phase <slug> <n> done | failed | blocked | skipped --note "<reason>"   # done (and hold) is refused while a step is still todo: tick it or drop it
python3 "$PIPE" hold  <slug> <n>        # done through a person: checks, blocked, a question on the desk, waits; run in the background
                                        # exits 0 answered (Approve or a variant closes it; sent back reopens it, and the next hold asks afresh), 3 stop or expired, 4 no desk or one without runs (ask in chat), 5 read-back refused
python3 "$PIPE" hold  <slug> <n> --question "<one line>"      # what the person is asked, up to 200 characters; default names the phase
python3 "$PIPE" hold  <slug> <n> --force "<reason>"           # hold even when the desk could not open a recorded file; recorded on the board
PIPELINE_HOLD_INTERVAL=0.2 python3 "$PIPE" hold <slug> <n>    # tests only: beat and read back every 0.2 s, not every 20 s and 30 s
python3 "$PIPE" wait  <slug>            # after exit 6 (over budget): beats until a person extends it (0) or ends the run (3); run in the background
python3 "$PIPE" log   <slug> "<what happened>" --kind decision|escalated|dispatched|landed   # mid-phase
```

Frame also writes `note <slug> 0 --backstory --fixed-means --wont-change`.
Finish writes the run's short version:
`story <slug> --happened --stands --needs-you "what|why"`. How to write all
of it: `references/board.md` § Writing for the reader.

Every write lands in the run log with a timestamp, so the board replays the
run without the transcript. Log the user's rulings and escalations yourself;
agent dispatches and landings are derived from the transcript.

`phase ... done` runs the artifact checks for that phase (`check` shows them)
and refuses to close on a missing plain line, a step still todo, a missing artifact, a task report with no red output,
a gate doc with no pasted failure, a NOT CONFIRMED verdict that was not
escalated. Fix the artifact. `--force "<reason>"` is for a decision the user
made, and it prints on the board in amber. On `phase ... done` it goes past
every one of these, a step still todo included; on `hold` it goes past none
of them, only the check that the desk can open each recorded file.

Every mutating call recomputes telemetry from the session transcripts and
re-renders the board. Fields and vocabulary: `references/board.md`.

Inside a session of the alydash plugin, every phase boundary is also reported
to the alydash desk, which draws the run on its Now strip with the phase, the
branch and the tokens and cost this ledger already computes. Nothing is set by
hand: `/alydash:connect`, run once in the repository, stores a credential for
it (the plugin names its store in `ALYDASH_CREDENTIALS`), and the run reports
with that credential, only to the desk it was minted on. With no credential for
the repository nothing is sent and nothing else changes. A desk without runs
(its credential was not given `heartbeat:AgentSession`) is told nothing either:
no phase reports to it and nothing is printed about that, and `hold` and `wait`
exit 4 as with no desk. Connecting again once that desk has runs picks it up. A machine with no
plugin, such as a scheduled cloud session, can still report with a token minted
on the desk: the script's own help text says how. The run's own page on the desk, `<desk>/runs/<id>`, draws the whole board:
every phase with its detail, live. A run is tied to one work item on the desk,
whose screen then shows one line linking to that page: the item is read off
the run's slug, then its branch, so `init joone-2-confirm` or a branch
`feature/joone-2-confirm` names JOONE-2 with nothing set. Name the slug after
the item. `ALYDASH_WORK_ITEM=ALY-7` names it outright and outranks both; a key
read off a name that the desk cannot link is dropped and the run reports
without it.
A session a runner started from the desk (Start with agent on an item) is
also given `ALYDASH_RUN_START`, which every beat carries so the desk joins the
run to that start, and `ALYDASH_RUN_BUDGET`, sent as the run's budget when the
ledger states none: `init --budget` outranks it, and a value that is not a
budget is ignored.
Every `note`, `step`, `story` and `log` call is reported too, so the story
and the run log move as they are written. That report names the phase the run
is in, not the phase noted, so a note against an earlier phase does not rewind
the card. Which calls push the branch: a `phase` event, and a `note` that
records a file (`--produced` or `--doc`), pushed before its report so the file
(the Phase 2 variants page above all) opens in the desk's run view while its
phase is still open. `step`, `story`, `log`, `ui-gate record` and a note with no file report
without pushing, since a push can start a preview build. `review record`
reports without pushing too. A beat names its
commit only once origin has it. A refused call sends nothing; `table`,
`defer` and `set` send nothing and arrive with the next beat. A branch is pushed only
when origin's default branch is known (`origin/HEAD`, or origin says it) and
is not that branch, main or master; otherwise one stderr line says why not.
A desk from before ALYDAS-225, October 2026, refuses a beat that carries a
dropped step, and every beat after it while the step stays dropped, so drop a
step only on a desk that already knows the state.

At **Gate 7**, a repository carrying `scripts/post-evidence.ts` also posts what
it proved — the screenshots attached to the criteria they show, those criteria
ticked, and one question raised about the ones this gate could not show:

```
python3 "$PIPE" evidence <slug> --plan <file.json>
```

The plan is written by you and never guessed by the script; `references/gates.md`
§ Gate 3 has its shape and the three ways this behaves when it cannot post.

The desk can answer back. A line `pipeline: message from the desk: ...` is a
person talking to this run from its Now card: read it and act on it. A line
`pipeline: the desk says stop` with **exit code 3** means a person pressed Stop
(or turned down more budget): the ledger has already recorded it and ended the
run on the desk, so stop working, start no further phase, and tell the user
where the run stopped. A line `pipeline: over budget: ...` with **exit code 6**
is a budget stop: the run has spent past its budget and the desk has asked a
person whether to extend it. The write landed and nothing was ended: run `wait`
in the background and do not start the next phase (§ When a run is over its
budget).

With either variable unset or empty nothing is sent and nothing else changes. A
desk that is down or says no costs one line on stderr: the board is still
written, the phase still closes, and the exit code is the one the command had
anyway.

## The review record

Gate 8 writes `measurements/<date>-<slug>-review.json` beside the prose review,
and records it for the desk:

- `review record <slug>`: checks the file, runs only while Phase 8 is running or held; counts each changed file's lines with git's numstat over `<base>...HEAD` (the merge-base with origin's default branch, as GitHub's compare does, a rename once under its new path with only its changed lines), files each into the first area whose pattern matches it (or "Other", never dropped), marks the tests by the UI gate's own patterns, and writes the result on Phase 8 as `review`. Exit 2, nothing written, for a file that is not well formed or a record over 48 KB: it is refused, never cut. Phase 8 and `hold <slug> 8` refuse to close without a well-formed record made at HEAD: after a fix commit or an amend, record it again.

The file's shape and bounds: `references/gates.md` § Gate 4.

## The UI gate

A run that changed a file a browser draws is measured at Phase 7 and reviewed
at Phase 8, one report a screen, and Phase 8 does not close (and cannot be
`skipped`) until every report is well formed, answers each failure that was
measured on its screen, and says `"verdict": "Approve"`. Three subcommands:

- `ui-gate skip <slug> --reason "<why this run draws nothing>"`: for a run with no rendered surface. Phase 8 refuses the skip, naming the files, when one changed.
- `ui-gate check <slug>`: every report of the run, checked. Exit 2 with each problem. Phase 8 and `hold <slug> 8` run the same check.
- `ui-gate record <slug> --replace`: the reports' findings into Phase 8's table, for the board, beside the review's own rows. It refuses what `check` refuses. `--replace` is needed only when Phase 8 already has a table with other columns.

The run is judged by what it changed since it left the default branch, and,
once it is merged, since the commit `init` recorded as its `base`, so a
branch merged before Phase 8 closes is still judged. One
approving report closes Phase 8 for the screen it reviewed: nothing checks
that every changed screen has one, nor that a measurement was taken after
the last change, so measure again after a fix pass.

Which files count, what the project supplies under `ui-gate/`, how to
measure, the reviewer's brief, the report's shape, what the gate does not
establish and what Block means: `references/ui-gate.md`. The rules the reviewer applies:
`references/ui-rules.md`.

## How a run waits

Some phases wait for a person before the run goes on: Phase 2 (the prototype)
and Phase 8 (Gate 8, the review) by default, or the ones `init --hold` or
`set <slug> holds` named. At Gate 8 the person reviews the delivery on the
desk's Review the delivery sheet, opened from Waiting on you, and approves or
sends it back with a note; a send-back reopens Phase 8 with the note as its
brief. A ledger from before this default, with no `holds` key, holds at Phase 2
only. Such a phase closes through
`hold`, never a plain `phase <n> done`, which refuses it (when nobody can answer
on the desk, see exits 4 and 5 below), and no later phase starts until it is
answered. A holding phase is not skipped by hand either: `phase <n> skipped
--note` refuses it too, and only `--force "<reason>"`, the person's ruling,
skips it, recorded on the board. At Gate 8 the review record must be made at
HEAD: after a send-back and a fix commit, run `review record <slug>` again
before holding, or the hold and the close refuse.

When the phase's work is written (its plain line and artifacts; for Phase 2 the
verdict and one `--option` per variant), start the hold with Claude Code's
`run_in_background`, so the session wakes when it exits:

```
python3 "$PIPE" hold <slug> <n>
```

The files the phase recorded (`note --produced` or `--doc`) must be committed.
Hold pushes the branch, then checks that origin has each file at the commit
the desk will read, before it raises anything. When one is missing, hold
refuses: it raises nothing, names each file and why (such as not committed,
not pushed, a directory, a link GitHub does not follow, a submodule, or the
file's name in other capital letters), with the
fix for each, puts that on the run log, and exits 2. A fix that says to record a path
afresh names the note to run, `note <slug> <n> --drop-produced <path>`, since
`--produced` alone adds a path and keeps the one refused.
`hold <slug> <n> --force "<reason>"` asks anyway, and the board shows the
reason in amber.

The phase shows as "Waiting on you" on the desk, with the question and a choice
for each thing the person could say. While it waits, a message from the desk is
printed (`pipeline: message from the desk: ...`) and the wait goes on: read it
and act on it. Running `hold` again for a phase it already blocked picks up the
same question, and an answer given while nothing was waiting is read, not lost.
What the exit code means, and what to do next:

- **0**, answered. Read the `pipeline: answered:` line and the `pipeline: note:`
  line. Approve or a variant closed the phase: go on with the one picked, and
  take the note into account. Send it back reopened the phase: do it again with
  the note as its brief, then hold again.
- **1**, no run or phase by that name: check the slug and the number.
- **2**, refused: the phase is not the one running, or it cannot close yet
  (stderr says what is missing), or a file it recorded is not on origin at the
  commit the desk will read (such as not committed or not pushed). Fix it
  and hold again: stderr gives the fix for each. On a feature branch hold
  pushes the branch itself; on main, origin's default or a detached HEAD it
  never pushes, and the fix is to commit on a feature branch. Only the file
  check can be passed, with `hold <slug> <n> --force "<reason>"`, for a
  ruling the user gave.
- **3**, stopped: a person pressed Stop or turned down more budget, or the
  question expired on the desk. Stop as above, and start no further phase.
- **4**, no desk, a desk without runs, or the desk would not take the question,
  so nobody can answer there. Ask the user in chat as before, then close the phase with
  `phase <slug> <n> done --force "<their ruling>"`, since a holding phase
  refuses a plain `phase <n> done`.
- **5**, the desk refused to read the answer back. Tell the user the desk
  refused and the phase is still waiting for them, ask them in chat, then close
  it the same way: `phase <slug> <n> done --force "<their ruling>"`.

The answer and its note land in the run log as a decision.

## Working with the desk

When the run reports to a desk (the block's `Board` line is a page on it), the
desk is where the person follows the work, so these go there and not only into
the chat or a document. The tools are the desk's MCP tools, on the server
connected for this repository's app: their names end in `raise_ask`,
`read_ask`, `propose_items`, `propose_criteria`, `read_proposal`, `read_item`,
`write_note`, `tick_criterion` and `attach_evidence`. With several desk
servers connected, use the one whose `list_items` answers with this app's item
keys. With none connected, say so once and carry on in chat.

- **A ruling is an Ask.** Any stop that waits on the person, such as a claim
  Gate 4 could not confirm, a finding that blocks, or a question of scope, is
  put on the desk with its choices, and the run waits there. Use `hold` when
  the phase itself waits, and `raise_ask` (kind `decide`, with `choices`)
  otherwise. Tell the person in chat that it is waiting, with the link; a chat
  question can go with it, never in its place.
- **Read an answer back.** Before saying something still waits on the person,
  and before acting on what they chose, call `read_ask` or `read_proposal`. An
  answer given on the desk does not always reach the session.
- **A step left for the person is an Ask.** Write each step or check the pull
  request leaves to be done by hand under **By hand:** in What you'll notice,
  one line a step starting "Before merging:", "After the deploy:" or "After
  the deploy, check:", with `(KEY criterion N)` at the end of a check that
  proves a criterion. The desk raises each one on Needs you, a before-merging
  step while the pull request is open and the rest once its deploy is green,
  so do not raise them yourself. With nothing to do by hand, write `None.`
- **Work is proposed, never created.** A deferral, a follow-up or a bug the
  run found goes to the person with `propose_items`, and reaches the board
  when they approve it. Each line is a user story: who wants what and why in
  its description, then criteria a wrong implementation would fail, then the
  context. Do not create or edit items through the browser.
- **The item's criteria are what done looks like.** Read them at Phase 0,
  plan each slice against the ones it closes, and put those in every
  builder's brief (`references/phases.md` §0, §3 and §5). Check the set as
  well as each line: if every one were ticked, would the person say it is
  done? Phase 0 lists what a set that covers done has, and the same list
  holds for criteria you propose on a new item.
- **A note says what it ticked.** When a criterion of the run's item is met,
  attach the evidence, tick it, and write a note naming which criteria are now
  met, with the evidence, and which remain.
- **The pull request is the Timeline's entry.** Its description opens with a
  `## What you'll notice` section (Headline, What's new, How to try it, What
  isn't done yet, Button, Screenshot, each as a bold label), written for the
  person and not for a reviewer, and carries one line `Ships:` with the keys
  of the items it ships, outside any code block. The desk publishes that
  section when the merge deploys and moves those items; a description without
  them publishes nothing and closes nothing. Before telling the person a pull
  request is ready, run `python3 "$PIPE" ready <slug> --item KEY --pr N` with
  one `--item` for every key on its Ships line (`--item KEY-1 --item KEY-2`),
  since a step lands on the item it names. With `--pr N` it also reads the
  pull request's description with `gh pr view N`, so a before-merging step
  written there counts until it is answered on the desk, one the desk has not
  read yet included. Exit 0 says no before-merging step is open; exit 3 lists
  the ones that are (on the desk, or written and not yet read by it), which
  the person does first; exit 4 means the desk or the description was not
  read (no desk, or gh missing, not signed in, or the pull request not
  found), so it is not ready either.

If a tool refuses a call that names the run, send it again without `run`.

At Phase 0, look for `.github/workflows/sync.yml` in the repository. Without
it the desk is never told about a merge or a deploy, so the Timeline stays
empty and items stay open: tell the person to run `/alydash:setup`, which adds
it with the pull request template and its check, and go on with the run.

## When a run is over its budget

A run given a budget, by `init --budget` or `set <slug> budget`, states it on
every beat. The desk takes it from the run's first beat only, so set it before
the first boundary; with none stated, the app's own run budget on the desk
applies, if it has one. Once the run's cost passes its budget, the desk answers
the next beat with a budget stop and asks a person, on the run, whether to
extend it.

The command whose beat heard it (`phase`, `note`, `step`, `story`, `log` or `ui-gate record`)
prints `pipeline: over budget: spent $x of $y ...`, logs it, and ends with
**exit code 6** once its own write has landed. Nothing is ended. Start the wait
with Claude Code's `run_in_background`, tell the user the run is over budget
and waiting on the desk, and do not start the next phase until it exits:

```
python3 "$PIPE" wait <slug>
```

It beats every 20 s, so the run stays live on the desk and the question stays
with it. A message from the desk is printed and the wait goes on; a desk that
is busy, down or unreachable costs a line and the wait goes on. `hold` waits
through a budget stop too: it still ends on its review answer, and the next
command hears the stop. What the exit code means:

- **0**, extended: a person raised the budget (`pipeline: budget extended on
  the desk`, with the new amount when the desk says it), logged as a decision.
  Pick the run up where the command that exited 6 left it.
- **1**, no run by that name: check the slug.
- **3**, stopped: a person pressed Stop, or turned the extension down. Stop as
  above, and start no further phase.
- **4**, no desk is set, or the desk has no runs, so nobody can extend it there. Ask the user in chat
  whether to spend more.

## Reporting to the user

The boundary message is the script's output, pasted verbatim in a fenced
block. `phase` prints it; `status <slug> [n]` reprints it.

```
Gate 4 · Verify the brief — PASSED
────────────────────────────────────────────
Took      5m · 12k out / 1.9M in · ~$4 · 1 agent
Wrote     docs/superpowers/plans/2026-09-09-linkedin-enhancements.md  (verdicts appended)
Board     https://desk.example/runs/…
Result    9 confirmed, 1 partial, 0 not confirmed
Wrong     the plan's "nine words" example was ten
Next      Phase 5 · wave 1 = S1, S3, S5
Resume    /feature-pipeline resume linkedin-enhancements
Clear     yes
```

Rules, because the run's transcript is otherwise noise:

- One boundary block per phase event. After it, write only what the user has
  to decide, if anything.
- Between boundaries, tell the user when agents are dispatched, when they
  land, and when something changes the plan or needs their input. Keep these
  short and skip restating the plan, since the block and the board carry it.
- While a phase holds, tell the user the run is waiting on the desk for
  their answer, with the run page link (the block's `Board` line).
- Numbers go in the block or the board, never in prose.
- The plain lines, steps and story are for the reader following on the desk,
  who has not read the code; write them as `references/board.md` says.
- Failures are reported in the block's `Wrong` line and on the board, not
  softened into the summary.

## The board

One HTML file per run, `boards/<date>-<slug>.html`, rendered by the script from
`boards/<date>-<slug>.json` at every write. **Never hand-edit the HTML and do
not design a board** - the template is the design. Nothing is published: with
a desk set, every beat carries the whole ledger, the run's title and goal, and
the run's own page on the desk draws it; the block's `Board` line is that page,
`<desk>/runs/<id>`. Without a desk, the `Board` line is the local HTML file. The
board shows per phase: wall time, tokens, cost, agents, what was decided,
measured, went wrong, overruled, produced, and every skip or forced close.

## Context discipline

- Announce boundaries with the block above; `Clear yes` means the next phase
  can be resumed from disk with `/feature-pipeline resume <slug>`.
- Resume = `python3 "$PIPE" status <slug>`, read the JSON's current phase, then
  only the artifacts that phase names. Do not re-read the run from the top.
  A run started under an older script and resumed under this one is refused
  at its first close until the open phase's steps are ticked or dropped.
- A question needing more than three files is a subagent, not a Read chain.
  Read with `limit`/`offset`; whole-file reads are re-sent every later turn.
- Stop at the next boundary when context is heavy; a planned clear beats
  auto-compaction.

## Optional enrichments

Invoke when present; the run is correct without them.

| Touches | Skill |
|---|---|
| UI | `frontend-design:frontend-design`, `impeccable` |
| Payments | `stripe:stripe-best-practices` |
| Postgres, RLS | `supabase-postgres-best-practices` |
| Charts | `dataviz` |
| Browser verification | `claude-in-chrome` (MCP tools; load in one `ToolSearch`) |

## Failure modes

- **A phase closed by narrative.** If `check` passes on an artifact that was
  padded to pass it, the run has the same defect with a green tick. The checks
  find missing evidence; only you find false evidence.
- **Prototype skipped because the design "was clear".** Skipping is a recorded
  decision with a reason, shown in amber. See `references/prototype.md`.
- **A gate passed by reading.** No red output, no broken build, no screenshot:
  it did not run.
- **A review summarised instead of reproduced.** Each finding gets a probe and
  a control before a ruling.
- **The wave writing the fix reproduces the defect.** Phase 6 starts with the
  guards this wave wrote.
- **A UI gate passed by a report nobody measured.** A report written from the
  diff alone, on a page that was never rendered, can be well formed and say
  Approve. Measure at Phase 7, and let the reviewer answer what was measured.
