# Phase 2 - Prototype

A design doc is an assertion; a running thing is a construction. Phase 2 is
where the design meets a real viewport, a real row, a real number, before
seven slices are built on top of it. **It produces an artifact every run.**
Skipping is a recorded decision that the board shows in amber, never a quiet
omission. Phase 2 holds by default, so a skip is the person's ruling:
`phase <slug> 2 skipped --note "<reason>" --force "<their ruling>"` (a note
alone is refused while the run holds at 2; a run that does not hold there
skips with `--note` only).

## Pick the question, then the shape

Write one sentence: *the decision that would be expensive to get wrong and is
cheap to test now.* If Phase 1 produced several, take the one the most slices
depend on. Then pick the shape by what answers it:

| Question | Shape | Produces |
|---|---|---|
| What should this look like, and does it survive real density? | **A. Variants page** | `<root>/prototypes/<date>-<slug>.html`, committed and shown in the desk's run view |
| Same, and the app runs in a browser within a few minutes | **B. In-app variants** | a `?variant=` switch on the real route, deleted in the last slice |
| Does this logic, state model or contract behave? | **C. Runnable probe** | `<root>/prototypes/<date>-<slug>/` script or TUI, one command to run |
| What does the outside world actually do? | **D. Measurement** | numbers read off the real thing (browser, query, device), pasted with method and date |

A is the default for anything with a UI. B is better when it is cheap; a
variant beside the real header, real sidebar and real data exposes what a
mock hides. C and D are for questions a picture cannot answer. A UI feature
with a logic question gets two artifacts, not a compromise.

## Shape A - variants page

- **Two or three variants that disagree about structure**: layout, hierarchy,
  primary affordance. Three tweaked card grids are wallpaper, not variants.
- **Frame it like the host.** Same width, the real neighbouring chrome drawn
  in (nav, sidebar, the row above and below), so density is honest.
- **Draw in the host's own tokens and components**, not a fresh style. Unless
  the host already uses them: no cream background, no italic accent words in
  headings, no numbered 01/02/03 section labels, no monospace labels, no
  pill-shaped buttons.
- **Fixtures carry the production distribution**, fabricated identities: the
  empty category, the out-of-range value, the record missing the field, the
  longest real string. Never real people's data in a committed file.
- A switcher (buttons or `?variant=`) at the bottom; arrow keys cycle.
- A `<title>`, light and dark tokens, and a layout that holds at phone
  width. Spend the design effort on the structural differences between
  variants rather than polish: the page's job is to be judged, not admired.

### Written to fit the desk's frame

The desk runs the page in a sandboxed frame (and in its own tab from Open
full size) under a fixed policy, so write it to fit:

- **One self-contained file.** CSS and JS inline, fixtures inline.
- **Scripts** inline, or from `https://cdnjs.cloudflare.com` or
  `https://cdn.jsdelivr.net` only.
- **Styles** inline, or from `https://fonts.googleapis.com`; **fonts** from
  `https://fonts.gstatic.com` only.
- **Images** as `data:` URIs or inline SVG. Nothing else loads.
- **No network calls**: no `fetch`, XHR or WebSocket, no forms that submit.
- **An opaque origin**: no cookies, and `localStorage` throws, so keep state in
  memory and wrap any storage call in `try`.

Open it from disk in a browser before recording it; a page that needs a local
server or a file beside it shows blank in the desk.

## Shape B - in-app variants

The route stays; only the rendered subtree swaps on `?variant=`. Keep data
fetching, params and auth as they are. Gate the switcher on non-production.
Same variant rules as A.

## Shape C - runnable probe

A pure module (reducer, state machine, functions) behind a throwaway shell.
No tests, no persistence, in-memory state, every action prints the full
state. One command to run, added to the project's task runner. Benchmarks run
on the real runtime the code will ship on, against a real-sized input, median
of three.

## Shape D - measurement

State the method (URL, viewport, query, device), the date, and the raw
numbers. Three readings minimum. Estimates are labelled estimates and carried
to Phase 7 as a device step.

## The user picks

Show the running thing, not a description. For a variants page, commit it and
record it on the ledger. The desk shows only what is committed, and hold pushes
it before it asks (below):

```
python3 "$PIPE" note <slug> 2 --produced "<root>/prototypes/<date>-<slug>.html=variants page" \
        --doc "<root>/prototypes/<date>-<slug>.html|The variants page|<what each variant tries>|<the question it answers>"
```

With a desk configured, that note pushes the branch and reports to the desk,
so the page appears in the item's run view, in Phase 2's pane, while the
phase is still open; the person picks there, through the hold below. With no
desk configured, give the user the file's local path to open in a browser and
ask for the pick in chat with the variants named. The best answer is usually
"B's layout with A's ordering"; record that as the design.

## The verdict

Append `## Phase 2 verdict` to the design doc:

1. The question, one sentence.
2. The candidates and what each tried.
3. What was measured, with numbers.
4. The pick, whose it was, and why. Before the hold this is your
   recommendation; once answered, it is the person's pick and their note. If it
   went against your recommendation, say so; the verdict records what was
   decided, not what you argued.
5. Every defect the prototype exposed, each carried into the plan as a named
   task.

Then on the ledger:

```
python3 "$PIPE" table <slug> 2 --columns "Variant,Tries,Verdict" --row "A|single column, totals first|picked" --row "B|two-pane, filters left|rejected: clips at 390px"
python3 "$PIPE" note  <slug> 2 --summary "<question>: <pick>" --wrong "..." \
        --plain "<the pick and why, for someone who has not read the code>" \
        --option "A, single column|chosen|<why>" --option "B, two panes|rejected|<why>"
```

One `--option` per variant, your recommendation `chosen` and each other
`rejected`: the hold below builds its choices from them.

## Holding for the pick

With a desk set, the run does not decide for the person. Once the verdict and
the options are written, hold the phase (`SKILL.md` § How a run waits):

```
python3 "$PIPE" hold <slug> 2
```

Commit the variants page or probe first and let hold push it. Hold refuses a
page or probe the desk cannot open, such as one not committed or not pushed: it
asks nothing, names each file, why and the fix, and exits 2. Do what it names
and hold again.
`hold <slug> 2 --force "<reason>"` asks anyway, for a ruling the user gave, and
the board shows it in amber.

The desk asks the question with a choice per variant, and the phase shows as
"Waiting on you" until it is answered. The answer and its note land in the run
log as a decision. When `hold` exits with an answer, fill in the verdict's pick
line from it: whose it was, their note, and whether it went against your
recommendation. If they picked another variant, rewrite the options
(`note <slug> 2 --clear options`, then the pick `chosen`), the plain line and
the summary to match. A send-back reopens the phase with the note as its
brief: do the prototype again, then hold again.

Only a run with no desk (`hold` exits 4, so nobody can answer there) may go on
without an answer. Ask in chat; if the user is not around, mark the verdict
**provisional**, close the phase with `--force "provisional pick"`, go on with
your recommendation, and put `provisional pick` in the boundary block's
`Result` line so the next human message can overturn it cheaply.

## Delete it in the last slice, except the variants page

Prototype code is written under prototype rules. The winner is rebuilt
test-first in Phase 5. In-app variant code (Shape B's `?variant=` switch) and
a probe's code (Shape C) are deleted in the final slice, and the deletion is a
line in that slice's report. A variants page under `<root>/prototypes/` is
**kept**: it is the record the desk shows for Phase 2, evidence rather than
code, and nothing imports it.
