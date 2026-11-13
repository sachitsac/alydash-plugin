# The four gates

A gate that produced no broken build, no red test, and no pasted output did not
run. Reading is not verification.

---

## Gate 1 — Verify the brief (Phase 4)

**Find the claims.** Read the plan and extract every sentence asserting what
existing code, data, or config *already does*. Tells:

- "X already …", "the table isn't …", "all five … use …", "this is already handled by …"
- Any count, list, or table describing current state (sites, fields, phases, rows)
- Any snippet presented as *existing* code
- Any supplied copy string implying a state it attaches to

**Rule each one.** Open the source. Verdict per `dispatch-contracts.md`. Append
to the plan under `## Brief verification`.

**Prefer running to reading.** The cheapest verification is executing the thing:
ten runs against a suspected flake, a throwaway probe test for any claim about
volume, a real query for any claim about data. Measure before you scope.

**Rewrite the survivors.** Even a CONFIRMED assertion is better carried as an
instruction: *"read X and confirm whether it already does Y."* It costs the
implementer one file read and cannot mislead them if state drifts before dispatch.

**Watch supplied copy specifically.** A string written for a state that doesn't
exist reads as a requirement to make it true — an implementer will build the
state so the string has a home, adding product behaviour inside a task that was
never about product. When supplying copy, name the existing state it attaches
to; if none does, say it may be unattachable and to report back.

### Stop conditions

Any NOT CONFIRMED → **stop, escalate, do not proceed.** A wrong premise can
delete work (a whole slice may dissolve because the bug was already fixed) or
add the wrong work. Both are decisions for the user.

**Record the result** once the plan carries it: the plain line first, since the
phase cannot close without one.

```
python3 "$PIPE" note <slug> 4 --plain "<how many claims held, in plain words, and what changed because of the rest>" \
        --summary "9 confirmed, 1 partial, 0 not confirmed"
```

---

## Gate 2 — Break the guards (Phase 6)

**Mutation, not review.** For each new or modified guard: break the thing it
claims to protect, run it, **observe red**, revert. Paste the failing output
into the residuals doc in a fenced block; the ledger's check for closing Gate 6
looks for it. If it stays green, the guard checks nothing.

This shape is **not greppable.** Agents whose explicit task was hunting it have
written fresh instances in the same hour. Only construction finds it.

### The specific shapes

**Name vs. predicate.** A docstring, `describe`, or `it` states a universal —
"every", "all", "any", "complete" — while the predicate narrows it by one
dimension. Ask each guard what its predicate's **live domain** is, in numbers.
*"1 of 79"* is the tell.

**One-sided thresholds.** For any threshold, ask what the **other direction**
permits. A cap invites gutting; a floor invites padding. A word cap with no
floor passed a 63-word signpost where prose was expected.

**Subsidised sums.** For any floor or cap, ask **which fields it sums.** A
neighbouring field that grows for unrelated reasons can carry a gutted field
past the threshold.

**Vacuous assertions.** A test asserting the absence of something it could never
reach passes for the wrong reason. A `toHaveBeenCalled()` named "once" passes
while the callback fires 13 times.

**Hand-written subject lists.** Derive from the registry. A hand-written list
silently stops covering new members.

**Comfortable round numbers.** Set non-vacuity floors to the **measured live
count**, not a round number safely below it.

### Compounds — the part per-task review cannot do

**Break two guards at once**, especially two covering the same surface from
different angles. Each narrowing is silent alone; applied together they ship the
defect while the suite stays green. This is structurally invisible to per-task
review — only a whole-branch pass that tries combinations finds it.

### Apply to this wave's own code first

The wave writing the fix reproduces the defect: in one wave 10 of 17 instances
were in guard code that wave itself had just written, and in the next **all
seven** were in the plan or the guards written to close this very family. Start
Phase 6 with the guards this wave authored.

**Record the result** once the residuals doc carries it: the plain line first, since the
phase cannot close without one.

```
python3 "$PIPE" note <slug> 6 --plain "<how many guards caught the break, in plain words, and what was added where one did not>" \
        --summary "12 of 12 mutations caught"
```

---

## Gate 3 — Goal achieved (Phase 7)

**Green ≠ done.** Tests passing, tasks closed, and artifacts present are all
compatible with a dead feature.

**Run the thing.** Start it with the project's own command (its README or
package scripts say which), then exercise it by whatever route
the project actually exposes — browser, simulator, rendered file, CLI, or a
triggered job. Use the route the goal doc's `Observed by` column names for
each item (`phases.md` §0 lists the route per project type).

If the project has **no** runnable surface at all, say so plainly in the
evidence doc rather than substituting a test run for observation. A unit test is
not an observation; it is the thing Gate 2 already checked.

### Web projects

Two options, both MCP tools independent of plugin state:

| Tool | Use when |
|---|---|
| `claude-in-chrome` (`mcp__claude-in-chrome__*`) | **Default.** Your real Chrome session — real auth, real cookies, real extensions. Closest to what a user sees. |
| Playwright MCP (`mcp__playwright__*`) | Clean room, a specific viewport, or a check worth keeping as a repeatable script. |

Load the `mcp__claude-in-chrome__*` tool set in **one** `ToolSearch` call, not
one per tool.

**Capture, don't narrate.** For each surface in `goal.md`:

- **Screenshot** — attach it to the evidence doc
- **Console** (`read_console_messages`) — a dead feature is often silent in the
  UI and loud here; filter with a pattern rather than dumping everything
- **Network** (`read_network_requests`) — confirm the call fired, its status,
  and that the row it should have written exists

Do not trigger `alert` / `confirm` dialogs — they block the extension and it
stops responding to commands.

**Check `goal.md` from the outside.** For each piece of evidence written in
Phase 0, verify it as an observer with no knowledge of the implementation — load
the page, query the row, click the button. Not by re-reading the task list, and
not by reasoning from the diff.

### A run that touches a rendered surface: measure it

While the app is up, measure every screen the run changed, in each of its
states, with `scripts/ui_measure.mjs`. `ui-gate.md` § Measuring has the
command, and the harness call for a project with sign-in or seeding. Write
each result to `measurements/<date>-<slug>-ui-measure-<screen>-<state>.json`
and commit it. Gate 4's UI reviewer answers what these files say failed, and
a screen with no measurement file cannot pass there. Do not edit a
measurement file, and do not skip a state because it is awkward to seed: say
which states were not measured in the evidence doc.

### Run the project's browser suite

Run the project's whole browser suite (its end-to-end or Playwright tests, by
the project's own command) once on the final tree, and paste its totals in the
evidence doc under a line that names it:

```
Browser suite (pnpm e2e, final tree at <sha>): 160 passed, 0 failed
```

The line names the suite and the commit it ran on, and carries a number before
`passed`. A failure is fixed, or shown to fail on `main` too and listed in the
evidence doc as unverified: under an `## Unverified` heading that names the
browser suite, or on a line that says unverified and gives the number failed.
The word elsewhere in the doc does not list them. A project with no browser
suite writes `no browser suite` in the evidence doc instead, and only when it
has no totals line: beside one, the totals line is what counts. `phase <slug>
7 done` refuses an evidence doc with neither, and a totals line that names no
commit, says 0 passed, or carries failures not listed, each with its own
sentence: the instruction alone was skipped.

### The failure this exists to catch

Every task complete, every script and config still looking live and correct —
and the renderer returning `null` for the entire feature. Nothing in the task
list, the diff, or the test suite said so. Only loading the page did.

### Checklist

- [ ] Each `goal.md` evidence item checked by observation, output pasted
- [ ] Checked in a real browser, not a test harness or a mock
- [ ] Screenshot captured for each surface
- [ ] Browser suite run on the final tree, its totals pasted under a `Browser suite (` line, or `no browser suite` written
- [ ] Console read — no errors, and no silence where a call should have logged
- [ ] Network confirms the request fired and the row was written
- [ ] Feature reachable by a user who wasn't told where it is — flags on, routes wired, entry point present
- [ ] Anything unverifiable stated plainly as unverified, not silently omitted
- [ ] Deferred items listed explicitly, with what would be needed to finish

Any unchecked box → say so in the evidence doc and to the user. An unverified
goal reported as achieved is the most expensive failure in this pipeline,
because everything downstream is built on it.

### Put the evidence where the work is tracked

The screenshots and the judgement this gate just produced are the two things a
work tracker wants, and until they are posted they stay in the repository: the
images in a folder, the judgement in a report, and the board still saying
`0 of 5` about work that is finished. A person who was never told they were owed
a tick does not tick.

So when the repository has a poster at `scripts/post-evidence.ts`:

```
python3 "$PIPE" evidence <slug> --plan <file.json>
```

**The plan is yours to write, and the script will not invent one.** Which
criteria a run demonstrated is a judgement; a script that guessed would be a
machine marking its own homework at one remove. Name only what this gate
actually showed:

```json
{
  "item": "ALY-7", "appId": "alydo", "run": "<slug>", "label": "<the item's title>",
  "screenshots": ["docs/screenshots/<date>-<surface>.png"],
  "criteria": [
    { "id": "<row id>", "text": "<verbatim>", "shown": true,
      "evidence": ["docs/screenshots/<date>-<surface>.png"] },
    { "id": "<row id>", "text": "<verbatim>", "shown": false,
      "why": "<why this gate could not show it>" }
  ]
}
```

`shown` is required and never defaulted. A criterion this gate could not
demonstrate is `false` with a reason, and that reason is what the person reads —
it is not a failure, it is the half of the work that needs a human.

Behaviour, so none of it is a surprise:

- **No poster in the repository → nothing happens.** Most repositories have
  never heard of a desk.
- **Poster present but the credentials unset → one line on stderr, exit 0.**
  Said out loud rather than skipped quietly, because this is a run that could
  have put its evidence on the board and did not.
- **The post failed → loud, and a non-zero exit.** Unlike the ledger's
  heartbeat, evidence that goes nowhere is this feature failing silently, which
  is the thing it exists to stop. It still cannot hold the gate open: closing a
  phase is a different command. **Close the phase and record the failure rather
  than claiming the board shows what it does not.**
- The credential is never printed, in any spelling, by this or by the poster.

**Record the result** once the evidence doc carries it: the plain line first, since the
phase cannot close without one.

```
python3 "$PIPE" note <slug> 7 --plain "<what a person using the app now sees, and anything that could not be shown>" \
        --summary "6 of 7 observed, 1 unverified"
```

---

## Gate 4 — Review the diff (Phase 8)

**What the other three cannot see.** Gates 1–3 check the work against its own
intent — the plan's claims, the guards' promises, the goal's evidence. A feature
can pass all three, be internally coherent, and still be wrong, because the
intent itself was incomplete. Review is the only gate pointed outward.

**Review the whole diff against the merge-base.** Not the last commit, not the
files you happen to remember touching. Dispatch the reviewers this project
mandates (its CLAUDE.md names them) alongside a correctness pass, in parallel,
each given the diff and `goal.md`.

**On a run that touched a rendered surface, dispatch the UI reviewer beside
them**, one for each screen, with the brief in `ui-gate.md` § Reviewing copied
into its prompt and `ui-rules.md` given by path. It reads the diff, the
screen file, the rulings and Gate 3's measurement files, and writes
`measurements/<date>-<slug>-ui-gate-report-<screen>.json`. A run that draws
nothing records that instead (`ui-gate.md` § Which runs need it).

**A reported finding is an assertion, and this file's whole premise is that
assertions get constructed, not read.** That cuts both ways: do not accept a
finding because it is written confidently, and do not dismiss one because it
sounds theoretical. Reproduce it. Run the query. Issue the request. Probe the
policy with the real credential.

**Every probe needs a control that must still pass.** A refusal proves the door
is shut only if you also showed a legitimate caller still gets through —
otherwise a policy that rejects everything is indistinguishable from one that
works, and a broken feature reads as a secure one.

**Rule each finding by its consequence, not its label.** Write the sentence: what
happens to a real user if this ships? A sentence that decides itself is a
blocker. A sentence you cannot finish is not a finding yet.

**Then treat the fixes as what they are: new, unverified code.**

- Failing test first, red observed, then the fix. A fix that goes straight to
  green was never demonstrated to fix anything.
- Re-run Gate 2 over the guards this phase wrote. The wave writing the fix
  reproduces the defect — that pattern does not pause because the code came from
  a review.
- If a finding shows an evidence item passed for the wrong reason, that item did
  not pass. Go back and amend it. A green tick resting on a premise you now know
  is false is worse than a missing one, because nobody will look again.

**The most valuable finding is the one that makes an earlier gate look wrong.**
That is the gate learning where its blind spot is; record it in the review doc so
the next run inherits it rather than rediscovering it.

**The close rule for a UI run.** Phase 8 closes only when every screen's
report passes the check and says Approve: each measured failure answered,
every HIGH finding fixed or covered by a ruling a person recorded, all seven
domains reviewed. A Block is fixed, measured again and reviewed again, or
ruled by a person; `ui-gate.md` § What Block means for the run. Check the
reports, then put their findings on the board:

```
python3 "$PIPE" ui-gate check <slug>
python3 "$PIPE" ui-gate record <slug>
```

**One table, shared, on a UI run.** Phase 8's table then has the columns
`Severity, Domain, Where, Finding, Standing`, and the code review's own
findings go in it too, written with `table`, with the Domain `Code` and the
ruling under Standing:

```
python3 "$PIPE" table <slug> 8 --columns "Severity,Domain,Where,Finding,Standing" \
        --row "HIGH|Code|lib/token.ts:3|the token is logged on every request|fix-now"
```

`ui-gate record` owns only the rows whose Domain starts with `UI gate: `,
which is how it writes its own (`UI gate: Layout`): it takes those out,
writes the current ones, and leaves every other row where it found it,
whatever its Domain says. When the other rows already fill the table it
refuses and says so. A run with no rendered surface keeps whatever table the
review writes today.

**Record the review for the desk.** Beside the prose review, write
`measurements/<date>-<slug>-review.json`, then record it:

```
python3 "$PIPE" review record <slug>
```

The file holds what only the reviewer knows; the script adds the rest:

| Part | What it holds |
|---|---|
| `areas[]` | `name` (80), `about` (300, optional), `patterns[]` (up to 20 path globs, 200 each; a pattern with no `/` matches a file's name at any depth). Up to 20 areas; past 40 changed files in one, the rest fold into one line (below) |
| `findings[]` | `id` (40, each its own), `title` (200), `plain` (what it means), `why` (why it matters), `how` (how it was reproduced), each 1,000; `by` (who found it, 120); `file` (500) and `line` (from 1, needs `file`), both optional; `group`: `needs-you`, `fixed` or `later`; `blocks`: true or false. Up to 50 |
| `proofs[]` | `criterion` (its number, 1 to 999), `text` (the criterion as read at Gate 8) and `how` (how it was proven), 1,000 each. Up to 30 |
| `tests` | `command` (500), `passed`, `failed`, `skipped` (whole numbers) and `excerpt` (2,000, optional) |
| `verdict` | One plain line (300), saying whether anything blocks |

`review record` adds `base` and `head` (the merge-base with origin's default
branch, or the base `init` recorded once that is HEAD, and HEAD), and each
area's `files[{path, added, removed, test}]` from `git diff --numstat -M
<base>...HEAD`, committed changes only, the same range GitHub's compare shows,
with `added`, `removed`, `testAdded` and `testRemoved` totals. An area
lists at most 40 files: the 40 with the most lines changed (added and
removed, ties by path), in path order. The rest fold into the area's
`more{files, added, removed, testAdded, testRemoved}`, which the desk draws as
one line under the list ("and 258 more files, +4,100 -0"), and `more` is left
out when nothing folds. The area's totals, and the record's, still count every
file, and the desk's Changes still lists every file GitHub returns. A renamed file
is counted as GitHub's compare counts it: once, under its new path, with only
the lines that changed, so a pure rename is 0 added and 0 removed. A file goes to
the first area whose pattern matches it, in the order written; a file no
pattern matches goes to an area named `Other`, never dropped. A file is a test
when it matches the UI gate's test patterns (`ui-gate/config.json`'s `ignore`
at the merge-base, else `*.test.*`, `*.spec.*`, `**/__tests__/**`, `test_*.py`
and `*_test.py`), so a
helper under `tests/` with no `.test.` in its name counts as code. A binary
file counts no lines and says `binary: true`. The record is refused whole
(exit 2, the ledger untouched) for any part past its bound, a group outside
the three, or a record over 48 KB, and never cut: the ledger's trim leaves it
alone. `review record` runs only while Phase 8 is running or held: before
`phase <slug> 8 start` it refuses, since nothing has been reviewed yet.
Phase 8 and `hold <slug> 8` refuse to close until a well-formed record
is on the ledger, made at HEAD; the review doc's ruling word is still checked
beside it. A record of an older commit, after a fix pass or an amend, is
refused with a sentence naming both commits ("the review was recorded at
<sha>, and HEAD is <sha>: run review record <slug> again"). A send-back keeps
the record on the ledger; this check is what makes it stale, and it covers a
phase reopened by hand too. So record it again after a fix pass, and the
counts are the ones merged. `phase <slug> 8 done --force "<reason>"` closes
over a stale record anyway, recorded on the board.

**Gate 8 holds by default.** `init` holds at Phases 2 and 8 unless `--hold`
says otherwise, so once the record and the result are written, close the
gate with `hold <slug> 8`. The desk's Review the delivery sheet is where the
person reviews it: Overview, Changes, Findings, Tests and Verification, read
from the record. They approve on the hold's Ask, or press Send back with a
note on the sheet, which answers the Ask `send-back`: `hold` prints
`pipeline: answered: send-back` and `pipeline: note: <their note>`, and Phase 8
runs again with the note as its brief. A run that does not hold at 8 hears a
note sent back from the sheet as `pipeline: message from the desk:`. A held
Gate 8 is not skipped by hand: `phase <slug> 8 skipped --note` is refused as
`phase <slug> 8 done` is, and only `--force "<reason>"` skips it, recorded on
the board.

**Record the result** once the review doc carries it: the plain line first, since the
phase cannot close without one.

```
python3 "$PIPE" note <slug> 8 --plain "<what the reviewers found and what was done about each, in plain words>" \
        --summary "3 findings: 1 fix-now, 2 deferred"
```
