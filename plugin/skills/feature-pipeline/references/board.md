# The ledger, the board, the boundary block

`scripts/pipeline.py` is the only writer. Run it from the repo root.

## Files, per run

| File | Written by | Role |
|---|---|---|
| `<root>/boards/<date>-<slug>.json` | the script | source of truth: phases, notes, tables, telemetry, events |
| `<root>/boards/<date>-<slug>.events.jsonl` | `phase` | append-only boundary stamps with timestamp and session id |
| `<root>/boards/<date>-<slug>.html` | `render` (automatic) | the local board, never hand-edited; with a desk the run's own page, `<desk>/runs/<id>`, draws the same run, and the work item's screen links to it |

Commit all three with the phase's other artifacts.

## Commands

| Command | Does |
|---|---|
| `init <slug> --title --goal [--repo --branch --goal-path --date --hold --budget]` | creates the run with ten pending phases; `--hold` names the phases that wait for a person (default `2,8`; `2` or `none` to wait less); `--budget` what the run may spend, in dollars, before the desk asks a person whether to extend it |
| `phase <slug> <n> start` | stamps entry; the phase shows as running; stamps its purpose and, on a phase with no steps, seeds its usual steps, all todo; then marks the first todo step doing when none is, so a running phase always shows a step in hand (a restart does the same, and leaves a phase with nothing todo alone) |
| `phase <slug> <n> done` | runs `check` (a plain line first, on every phase), stamps exit, marks the step in progress done, prints the boundary block; refused while a step is still todo, naming each one (tick it or drop it); refused on a phase the run holds, which closes through `hold` |
| `phase <slug> <n> failed \| blocked` | same stamp, red on the board |
| `hold <slug> <n> [--question "..." --force "reason"]` | closes a phase the run holds, through a person: runs `check`, pushes the branch and checks that origin has every file the phase recorded (refusing with exit 2, naming each file and why, when one is missing; `--force` asks anyway, in amber), marks it blocked (the desk shows "Waiting on you"), asks on the desk, waits for the answer; Approve or a variant closes it, a send-back reopens it; `--force` does not go past a step still todo, or any other check; `SKILL.md` § How a run waits |
| `wait <slug>` | after a command exits 6 (over budget): beats until a person extends the budget (exit 0) or ends the run (exit 3); `SKILL.md` § When a run is over its budget |
| `phase <slug> <n> skipped --note "why"` | amber on the board; the note is shown |
| `phase ... done --force "reason"` | closes past a failing check; the reason is shown in amber |
| `note <slug> <n> --plain "..." --scene "..." --lede "..."` | the phase's plain line, where the story is as it starts, and the first paragraph of its story; each replaces the one before |
| `note <slug> <n> --number "figure\|what it means" --option "name\|chosen\|why" --assumed "text\|what came of it" --left-out "..."` | the Story view's lists; each flag repeats, a repeat is ignored. `--option` takes `chosen` or `rejected` |
| `note <slug> 0 --backstory "..." --fixed-means "..." --wont-change "..."` | Frame only; refused on any other phase |
| `note <slug> <n> --summary --next --decided --measured --wrong --overruled --produced path=note` | the Technical view; list flags repeat. `--wrong "what => did"` also adds a problem to the Story view |
| `note <slug> <n> --doc "path\|title\|about\|in short"` | names a produced file as a document: a title, a line on what is in it, and a short summary; upserts by path, so a file already `--produced` keeps its note, and a later `--produced` for the path updates the note on the same entry |
| `note <slug> <n> --drop-produced path` | takes one recorded file off the phase, by its path exactly as recorded; a path the phase never recorded is refused. Hold's "record it as" fix names this note, which drops the path it refused and records the one it names, in one call |
| `note` with a byte that is not UTF-8 | a path or text that is not UTF-8 (a file name with a stray byte, say) is refused before anything is written, exit 1, naming the flag or the path as its bytes: the desk cannot be sent it. Rename the file, or write the text again |
| `note ... --clear decided\|measured\|wrong\|overruled\|produced\|summary\|next\|plain\|scene\|backstory\|fixed-means\|wont-change\|lede\|numbers\|options\|assumed\|left-out\|problems\|steps` | empties one field to rewrite it; repeat `--clear` for more. `--clear wrong` leaves the problems; clear both |
| `step <slug> <n> <number or exact text> [todo\|doing\|done\|dropped] [--sub "..."]` | ticks one of the phase's steps, with a line under it; one step is doing at a time, so moving one to doing marks the one before it done; on a running phase, any change that leaves no step doing moves the next todo step to doing, not only done or dropped. `dropped` is a step that was not done and needs `--sub "<why>"`: it is refused without one, exit 1, nothing written |
| `step <slug> <n> "text" --add` | adds a step of the agent's own, todo; on a running phase with no step doing, it is added doing |
| `story <slug> --happened "..." --stands "..." --needs-you "what\|why"` | the run's short version: lines of what happened (up to 8, appended), where it stands (one line, replaced), what waits on Sachit (up to 8) |
| `story <slug> --term "CODE=plain name"` | a code the run uses and its plain name; a later one for the same code replaces it |
| `story ... --clear terms\|happened\|stands\|needs-you` | empties one part of the story to rewrite it |
| `review record <slug>` | Gate 8's `measurements/<date>-<slug>-review.json`, checked, each changed file's lines counted from the merge-base to HEAD and filed into its area, written on Phase 8 as `review` for the desk (a rename once, under its new path); refused whole (exit 2, nothing written) when the file is not well formed, the record is over 48 KB, or Phase 8 is not running or held; `references/gates.md` § Gate 4 |
| `table <slug> <n> --columns "a,b" --row "x\|y" [--replace]` | the phase's one table (evidence items, slices, verdicts, mutations) |
| `defer <slug> --item --needs` | the Deferred tab |
| `log <slug> "message" [--phase n] [--kind dispatched\|landed\|decision\|wrong\|escalated]` | a free-text run-log entry; use it for a user's ruling, an escalation, a wave dispatched or landed |
| `set <slug> <key> <value>` | scalar fields: branch, goal, title, goal_path; `holds`, the phases that wait (`2,8` or `none`); and `set <slug> budget 10`, the run's budget in dollars, which the desk takes from the run's first beat only |
| `usage <slug>` | recompute telemetry and print it |
| `render <slug>` | re-render the HTML |
| `status <slug> [n]` | print the boundary block for phase n (default: current) |
| `check <slug> <n>` | list what is missing before phase n can close |

`note`, `step` and `story` check every value against its bound before writing
anything, so a refused call leaves the ledger as it was.

### Which calls reach the desk

With a desk configured, these beat (report the whole ledger), so the run page
moves as the work does. The ones marked **push** push the branch first, so the
commit the beat names is on origin; the rest do not push, since a push can
start a preview build, and their beat names a commit only once origin has it:

- `phase`, every event, start included: **push**;
- `note` with `--produced` or `--doc`: **push**;
- `note` with neither, every call;
- `step`, every call;
- `story`, every call;
- `log`, every call;
- `review record`, every call that records;
- `hold`, as it blocks the phase and every 20 s while it waits; it pushes (**push**) before its file check, and a hold the check refuses beats nothing;
- `wait`, every 20 s while it waits for a person to extend the budget;

A note reports the phase the run is in, not the phase noted.
- a stop: when the desk answers "stop", the ledger records it and one last
  beat ends the run. A budget stop (`"reason": "budget"`) ends nothing: the
  command exits 6 and `wait` takes over.

A refused call (exit 1 or 2) sends nothing. `table`, `defer` and `set` send
nothing; they arrive with the next beat.

## Panel fields and what goes in them

The desk shows each phase two ways. **Story** is for someone who has not read
the code: the plain line, the scene, the lede, the steps, the numbers, the
options, what was assumed, what was left out, the problems, the documents.
**Technical** is the rest: summary, decided, measured, wrong, overruled, the
table.

| Field | Flag | Holds | Example |
|---|---|---|---|
| `plain` | `--plain` | one sentence a newcomer understands; a phase cannot close without one | "The plan checked out: every claim it makes about the code is true, and one was only half true." |
| `scene` | `--scene` | where the story is as the phase starts: what we know coming in, what this phase answers | "We know the screen is hard to read. This phase decides what to change." |
| `lede` | `--lede` | the first paragraph of the phase's story | "Three ways to lay out the page were tried side by side, and one read best on a phone." |
| `steps` | `step` | the phase's to-do list, each todo, doing, done or dropped, with a line under it (a dropped step's line is its reason) | step 2 doing, "reading the run page's layout" |
| `numbers` | `--number "figure\|means"` | a figure and what it means | "18 of 18\|leaked the old value before the fix" |
| `options` | `--option "name\|chosen\|why"` | an option weighed, chosen or rejected, and why | "Two columns\|rejected\|the right one is cut off on a phone" |
| `assumed` | `--assumed "text\|later"` | what was taken as true, and what came of it later; the desk prints "Later:", so a leading one is taken off | "Nobody runs two at once\|held: the desk refuses a second" |
| `leftOut` | `--left-out` | one thing left out on purpose | "Old runs keep their old layout" |
| `problems` | `--wrong "what => did"` | what went wrong and what was done about it; the whole line also stays in `wrong` | "a test passed whatever the code did => rewrote it to fail first" |
| `backstory` | `--backstory` (Frame) | why this run exists, in everyday terms | "Reading a finished run meant knowing the pipeline's own codes." |
| `fixedMeans` | `--fixed-means` (Frame) | one thing "fixed" means, the proof in plain words | "A newcomer can say what a phase did from its first line." |
| `wontChange` | `--wont-change` (Frame) | one thing this run will not change | "How phases are numbered" |
| `produced` | `--produced path=note`, `--doc` | a file written, with a note; `--doc` names it with a title, a line on what is in it, and a short summary | `docs/plans/x.md\|The build plan\|seven slices, each with its test\|build the plain line first` |
| `summary` | `--summary` | the result in one line, for Technical: shorthand may live here | "9 confirmed, 1 partial, 0 not confirmed" |
| `decided` | `--decided` | the decision and its why | "tiers are cumulative, so the drill-down needs exclusive buckets" |
| `measured` | `--measured` | numbers actually observed, with method | "fold is 3 lines at 475px; a blank line spends one" |
| `wrong` | `--wrong` | what failed or drifted, kept in | "shared local DB moved under us; migration renumbered 127 to 129" |
| `overruled` | `--overruled` | where the decision went against your recommendation | "owner chose C; both its defects were fixed rather than shipped" |
| `next` | `--next` | what the next phase starts with | "wave 1 = S1, S3, S5" |
| `table` | `table` | the phase's one table | Phase 0 evidence items; Phase 3 slices; Gate 4 verdicts; Phase 5 slices with red pasted; Gate 6 mutations; Gate 7 evidence observed; Gate 8 findings |

The run as a whole carries three more, written with `story`:

| Field | Flag | Holds | Example |
|---|---|---|---|
| `story.happened` | `--happened` | a line of what happened, in plain words (up to 8) | "The run page now opens with one plain sentence per phase." |
| `story.stands` | `--stands` | where the run stands, in one line | "Built and checked; waiting for you to merge." |
| `story.needsYou` | `--needs-you "what\|why"` | one thing waiting on Sachit, and why (up to 8) | "Merge PR #55\|merging puts it live" |
| `terms` | `--term "CODE=plain name"` | every code the run's texts use, with its plain name, shown when a reader points at the code | "S3=the third slice of the build" |
| `log` | (every write) | the run log, sent to the desk; see The run log | |

Table cells that are exactly a status word render as a pill: `observed`,
`confirmed`, `green`, `caught`, `fixed`, `landed`, `passed` (good);
`partial`, `pending`, `estimated`, `deferred`, `fix-now`, `forced` (amber);
`not confirmed`, `red`, `not caught`, `failed`, `blocks`, `open` (red).
Backticks in any text render as code.

## Writing for the reader

The plain line, the scene, the lede, the steps and the story are for Sachit
following the run on the desk, often on a phone, not for the agent. Rules:

1. **Write for someone who has not read the code.** Everyday words, whole
   sentences, one idea each.
2. **No bare codes in the story.** A code (a slice, a gate, a finding, a
   criterion) appears only with its plain name beside it: "the third slice
   (S3)", never "S3". Every code used anywhere in the run goes in `terms`
   (`story --term "S3=the third slice of the build"`).
3. **Numbers say what they mean.** "18 of 18 leaked the old value", not
   "18/18 TOLD APART". A `--number` always carries its meaning after the bar.
4. **Every decision names what else was on the table.** One
   `--option "name|chosen|why"` for the pick and one
   `--option "name|rejected|why"` for each alternative.
5. **Tick a step when it finishes**, not at the end of the phase: the desk
   shows the phase moving only if the steps do. The start marks the first
   step doing, and each tick moves the next one to doing, so the page always
   says what is in hand. A step that was not done is dropped with its reason,
   never ticked, and a phase does not close while one is still todo.

Technical (summary, decided, measured, wrong, overruled, the table) is where
shorthand may live: codes, counts, file paths. It is still read, so keep it
exact.

## Status vocabulary

`pending`, `active` (running), `done` (a gate shows "passed"), `failed`,
`skipped` (reason required), `blocked`. Forced closes carry the reason. A
phase `hold` blocked is waiting for a person, and the desk shows it as
"Waiting on you".

## The run log

Every ledger write is an event: phase opened and closed, each result, decided,
measured, went-wrong and overruled entry, each file written, each table row,
each deferral, each failed check, each forced close, and `log` entries. Agent
dispatches and landings are derived from the transcripts at render time, with
duration, tokens and cost. The board shows them as one time-ordered stream
under "Run log", so a reader can replay the run without the transcript.

Log the things the ledger cannot see on its own: the user's rulings
(`--kind decision`), escalations, and anything that changed the plan mid-phase.
An answer given on the desk to a held phase is logged for you, as a decision:
"Answered on the desk: <the choice>. Note: <their note>".

With a desk, every beat also carries the log to the run's page, each event in
words ("Phase 3 · Plan started", "Decided: ...", "Step 2 done: ..."): the
newest 200 events, and when a ledger is too big for the desk the oldest log
entries are the first thing trimmed, before any phase's own words.

## Telemetry

Computed from `~/.claude/projects/*/<session>.jsonl` for every session id the
events file names, so it survives `/clear` and spans sessions. Per phase:
wall time between its start and end stamps; tokens of every assistant
message in that window plus every subagent that started in it; agent count
and per-agent rows; cost. Cost is priced per message, main thread and
subagents alike: at the session's own calibrated rate when its transcript has
a cost line (matched by base model id, so `claude-opus-5-5[1m]` prices
`claude-opus-5-5`), otherwise from the list prices in `PRICES` in
`pipeline.py`, with 5-minute and 1-hour cache writes priced apart. A live
session has no cost line yet, so this table is what gives a running phase a
cost; update it when prices move. One message on a model in neither source
makes the cost unknown (a dash, and no cost sent to the desk) rather than a
smaller number. The transcripts do not carry every call Claude Code bills, so
the priced total runs below the session's own figure (16 to 33 percent on
three sessions, ALYDAS-135 S4); the strip shows both. The desk is sent
Claude Code's own total, marked exact, once every session the run spans has
written one (and none says a model's cost was unknown, and no session went on
past its line). A resumed session writes a new line for the resumed part only,
under a new `startTime`, so a session's total is the last line of each
`startTime` added up; until then it is sent the priced total marked not exact, and
shows it as "at least". Before a beat, a ledger over 192 KB serialized is
trimmed to fit the desk's 256 KB body ceiling, the oldest log entries first,
then the longest texts and then the oldest table rows, never a phase's number,
name or status, and the run log records a `trimmed` line saying how much. Telemetry never blocks a run: a
failure is recorded in the JSON and the board still renders.

## Checks that gate `phase done`

| Phase | Refuses to close unless |
|---|---|
| every | the phase has a plain line (`note <slug> <n> --plain "..."`); checked before its own artifacts |
| every | no step is still todo: each one is named with its number and text, to tick with `step <slug> <n> <i> done` or drop with `step <slug> <n> <i> dropped --sub "<why>"`. A step being done is not held to it: the close finishes it |
| 0 | a goal doc exists and names how each evidence item is observed |
| 1 | a design doc exists |
| 2 | the design doc has `## Phase 2 verdict` and a prototype artifact was recorded (or the phase was skipped with a note) |
| 3 | a plan exists and names the test written first |
| 4 | the plan has `## Brief verification` with verdicts; a NOT CONFIRMED needs `--force` carrying the user's decision |
| 5 | every task report has a fenced block with failing output |
| 6 | the residuals doc has a fenced block with failing output |
| 7 | the evidence doc references a screenshot, or states there is no runnable surface |
| 8 | the review doc rules findings blocks / fix-now / defer; Phase 8 carries a well-formed review record (`review record <slug>`) made at HEAD; and, when the run changed a rendered surface, every screen's UI gate report passes its check and says Approve (a run that changed none records `ui-gate skip <slug> --reason "..."`) |
| 9 | the resume doc exists |

`phase ... done --force "reason"` closes past any of these, the plain line
included; the reason is recorded and shown in amber, and the desk's log says
the phase was closed by hand. `hold` runs the same checks before it asks, and
`hold --force` goes past none of them, a step still todo included: a held
phase gets past one only by ticking or dropping it. `failed`, `blocked` and
`skipped` run no check, so a phase that did not finish its steps shows them.

The checks find missing evidence. They cannot find false evidence; that is
still the gate's job.
