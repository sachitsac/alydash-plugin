# The UI gate's rules

The rule book the UI gate reviews a screen against. `ui-gate.md` says how the
gate runs; this file says what counts as a finding, what it is called, and
how bad it is. Everything the reviewer needs is here: the gate reads no other
skill and no installed plugin.

## Attribution

The rules for accessibility, layout, writing, typography, colors and UI are
adapted from the interfaces skills by Jakub Krehel
(github.com/jakubkrehel/skills, version 1.6.3), used under the MIT licence
below. They are cut down to what a gate can check, given ids, and reworded.
The placement rules are this skill's own.

```
MIT License

Copyright (c) 2026 Jakub Krehel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Severity

One scale for every domain. Rank a finding by what it does to the person
using the screen, not by which rule it breaks.

- **HIGH** blocks a task, misleads the person, hides content or a control,
  or is a systemic failure: the same fault repeated across the screen from
  one cause.
- **MEDIUM** harms comprehension, efficiency, adaptability or consistency.
- **LOW** is isolated polish. The task is not affected.

Some rules are **automatic HIGH**: once the rule is confirmed it is HIGH on
sight, and it is never averaged down because the surface is minor. The tables
below mark them. The mark sets what a confirmed finding costs. It does not
decide whether the fault is there: that takes evidence, as below.

A HIGH finding blocks the gate unless a ruling covers it (`ui-gate.md`).

## Evidence, not taste

Press hard on the automatic HIGH rules and leave the project's deliberate
choices alone. A density, radius or voice you merely disagree with is not a
finding.

- Every finding names a file and a line, and says what is there now.
- A finding about how the page renders stands on a measurement or on a state
  that was rendered. A finding about the code stands on the code. Do not
  report one from the other when the running page decides the answer.
- A check you could not make is not a finding. It goes in the report's
  `notVerified` list, in words.
- A short report from a real inspection beats a long one padded to look
  thorough. No findings is a valid result.

## Prefer the cheaper fix

A finding's `after` proposes a fix. When more than one would work, take the
earliest of these that does:

1. **Delete.** The separator that space would carry, the animation on a
   control used all day, the attribute a native element makes redundant.
2. **Use the platform.** The native element, the native control, the
   browser's own focus ring, in place of a custom rebuild.
3. **Reuse what the project has.** A token, spacing step or motion curve it
   already defines, before any new value.
4. **Correct the value.** The wrong contrast pair, radius, gap or duration.
5. **Add.** A new token, a wrapper, a media query, an attribute the platform
   cannot supply.

Write the fix in the project's own styling system and component library.

## One root cause is one finding

One root cause is one finding. When a token, a shared component or one
stylesheet rule causes the same fault in six places, report it once, at the
source, and name the places in `before`. Do not pad a report to its limit,
and do not split one cause into rows to make a report look thorough.

When two domains seem to cover one fault, the finding belongs to the domain
that owns the rule, and its `why` may name the other effect.

## Rulings

A ruling is a design choice a person has settled (`ui-gate.md`). It covers a
finding only when three things hold: the finding's rule is in the ruling's
`rules`, its file is in the ruling's `covers`, and the code does what the
ruling chose. The check sees the first two. The third is the reviewer's:
cite a ruling only when the code under review does what the ruling's
`choice` says and the finding objects to that choice. A ruling the code
contradicts is not a ruling for that finding. A finding that the code
departs from a recorded choice is never ruled, and its `why` names the
ruling it departs from. A project that ruled "nothing is red" has not ruled
that a failed phase may be drawn in the colour of a finished one.

## The cap

A report holds at most 15 findings. Answers to measured failures and
automatic HIGH findings are never cut by the cap. The cap cuts the lowest
severity first, and `notVerified` says how many were left out. A domain all
of whose findings were cut keeps the coverage result `findings`, and its
`inspected` says "left out by the cap". The cap leaves nothing out of a
report that holds fewer than 15 findings, and the check refuses a report
that says it did. When the findings that cannot be cut
alone exceed 15, the report holds the first 15 by reach, the ones that touch
the most of the screen first, the verdict is `Block`, and `notVerified` says
how many were left out.

## Reading the tables

One table a domain. **Automatic HIGH** is `yes`, `no`, or the one case in
which the rule is HIGH. **Who sees it** says where the evidence comes from:

- `script`: the measuring module (`scripts/ui_measure.mjs`) measures it on
  the rendered page and reports each failure with the rule's id.
- `reviewer`: the reviewer reads it from the source, or judges it from what
  the script measured.
- `both`: the script measures what was rendered, and the reviewer reads the
  source for the states and paths that were not.

## Accessibility

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `A11Y-FOCUS` | A keyboard stop has a visible focus indicator: an outline, or a change in paint, when it takes focus. | yes | both |
| `A11Y-NAME` | Every control has an accessible name, from a label, its text or an aria label, and a placeholder is not a name. | yes | both |
| `A11Y-KEYBOARD` | Whatever a pointer can reach and work, the keyboard can reach and work too. | yes | both |
| `A11Y-TARGET` | A target is at least 24px on its short side; on touch, 24 to 44 is a note and not a failure; a link inside a sentence is exempt. | no | script |
| `A11Y-MOTION` | Motion and content that plays by itself respect the reduced-motion setting. | yes | reviewer |
| `A11Y-COLOR-ONLY` | A state or meaning the viewer needs to do one of the screen's declared jobs, or to recover from an error, is never carried by colour or shape alone, to the eye or to a screen reader: a word, an icon with a name, or an attribute carries it too. | yes | reviewer |
| `A11Y-STRUCTURE` | Headings form an outline with no skipped level, regions are landmarks, and the page has one main. | no | reviewer |
| `A11Y-ANNOUNCE` | Content that changes without a page load, such as an error, a result or a status, is announced to a screen reader. | no | reviewer |

`A11Y-MOTION`: a global reduced-motion rule in the project's stylesheet
satisfies it. Many projects have one rule that turns every transition and
animation off under `prefers-reduced-motion: reduce`. Look for it before
reporting. With that rule present, a component that does not repeat the media
query is not a finding.

`A11Y-COLOR-ONLY` is this rule, and automatic HIGH, only when the thing
carried by colour or shape alone is a state or meaning the viewer needs to
do one of the screen's declared jobs or to recover from an error: status,
error, selection, required, progress. A category mark that the same row or
its detail also says in words is not this rule; raise it, if at all, as
MEDIUM under `A11Y-STRUCTURE`. It is: a failed row told from a finished one
only by its dot's colour. It is not: a gate drawn as a diamond beside phases
drawn as squares, when each row also says "Gate" in words.

`A11Y-TARGET`: the script reports under 24px as a failure and 24 to 44 on a
touch context as a note. A note needs no finding. Raise one only when the
control is a main action on a phone.

## Layout

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `LAYOUT-CLIP` | No content or control is clipped, and the page does not scroll sideways, at 390 or 320 wide. | yes | script |
| `LAYOUT-HIDDEN` | Content reachable only past a scroll edge or behind a disclosure shows a visible cue that more is there. | yes | reviewer |
| `LAYOUT-GROUP` | Things that belong together sit together: the gap between groups is clearly larger than the gap inside one, and edges align. | no | reviewer |
| `LAYOUT-CONTROL` | A control looks like a control and static content does not: nothing that acts reads as plain text, and nothing static is drawn like a button or a link. | no | reviewer |

`LAYOUT-CLIP`: content inside its own sideways scroller can be reached, so
the script reports it as a note. The reviewer then judges whether the
scroller shows a cue, which is `LAYOUT-HIDDEN`.

## Writing

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `WRITE-DESTRUCTIVE` | A destructive action does not take effect on one activation: it asks for confirmation first, or it offers an undo after. | yes | reviewer |
| `WRITE-ERROR` | An error says what happened and names a way to recover. | on the main path; MEDIUM for a fallback string shown only when the server gave no reason | reviewer |
| `WRITE-LABEL` | A button's label starts with a verb that names the action, and a link's text describes where it goes. | no | reviewer |
| `WRITE-CONSISTENT` | One thing has one name across the screen, and the screen keeps one capitalisation policy. | no | reviewer |
| `WRITE-EMPTY` | An empty state says what the place is and what to do next. | no | reviewer |

`WRITE-DESTRUCTIVE` belongs to Writing, because what is missing is the
question or the way back, and both are words on the screen. An action is
destructive when it ends, deletes or sends something that cannot be taken
back by pressing it again. Whether the control has a plain or neutral look is
a separate question, `COLOR-TREATMENT`, with its own finding and its own
rulings. A ruling on the look does not answer this rule: a project that has
ruled its Stop button stays plain has said nothing about whether Stop asks
first.

## Typography

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `TYPE-TRUNCATE` | Truncated text leaves a way to the full value: a title, a tooltip, an expanding row or a detail view. | yes | both |
| `TYPE-HEADING-ORDER` | A heading is never drawn larger than the heading it sits under. | no | both |
| `TYPE-SCALE` | Text sizes come from the project's scale, and a hard-coded size with no scale behind it is a finding. | no | reviewer |
| `TYPE-INPUT-ZOOM` | An input's text is at least 16px on a phone, since under that the phone's browser zooms the page on focus. | no | reviewer |
| `TYPE-MEASURE` | Long-form text is capped at a readable line length, about 60 to 75 characters. | no | reviewer |

## Colors

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `COLOR-CONTRAST` | Body and control text meets its required ratio, 4.5 to 1, measured against the ground it renders on, in light and in dark. | yes | script |
| `COLOR-MEANING` | A semantic colour never signals the opposite of the state it is on: a failure is not drawn as success, and a safe action is not drawn as danger. | yes | reviewer |
| `COLOR-TREATMENT` | Each semantic state has a treatment of its own: a state given no distinct treatment (a destructive action or a failure drawn like any neutral one), or one semantic colour stretched over two near meanings, is a finding; a ruling on a plain look answers this rule. | no | reviewer |
| `COLOR-TOKEN` | A colour comes from the project's token for that role: a raw value where a token exists, or a token borrowed outside its role, is a finding. | no | reviewer |

`COLOR-CONTRAST` is measured, never estimated from the source: the ground a
piece of text lands on depends on what is drawn behind it.

`COLOR-MEANING` is this rule, and automatic HIGH, only when a semantic
colour signals the opposite of the state it is on. A semantic state given
no distinct treatment, or one colour stretched over two near meanings, is
not this rule: it is `COLOR-TREATMENT`. It is: a failed phase drawn in the
colour of a finished one, or the danger colour on an action that destroys
nothing. It is not: emerald used both for a finished phase and for a chosen
option. A ruling that a control stays plain does not answer this rule.

`COLOR-TREATMENT` is MEDIUM by default and never automatic: a destructive
action or a failure drawn like any neutral one, or one semantic colour used
for two near meanings. It is the rule a project most often has a ruling
for, since a calm look can be a choice. Check `ui-gate/rulings.json` before
raising it; a ruling on a plain look answers it.

## UI

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `UI-TRANSITION-ALL` | A transition names the properties it animates, and `transition: all` is a finding. | no | both |
| `UI-MOTION` | A high-frequency interaction answers at once or in 150ms or less. | only when a state change is visible only while the animation runs | reviewer |
| `UI-RADIUS` | Nested corners are concentric: the outer radius is the inner radius plus the padding between them. | no | reviewer |
| `UI-STATE` | A component has each state it needs: hover, focus, disabled, loading and empty. | no | reviewer |

`UI-MOTION` is HIGH in one case: the state change can be seen only while the
animation runs, so when the animation does not run, nothing on the screen
says the state changed.

## Placement

These four are new. They are not from the interfaces skills. They ask
whether the screen spends its first screen on the jobs a person comes to it
for.

The jobs are in the screen's file (`ui-gate/<screen>.json`), in rank order,
written by a person. Each block names the jobs it `serves`. The script
measures, at desktop and at phone, where each block sits and how much room it
takes: `onFirstScreen`, `firstScreenShare` (the percent of the first screen
the block covers), `fill` (the percent of the block's height its content
reaches) and `top`. The reviewer judges the rules from those numbers. The
script reports no placement failure by itself.

| Id | The rule | Automatic HIGH | Who sees it |
|---|---|---|---|
| `PLACE-MISSING` | The screen's first-ranked job has a block on the first screen. | yes | reviewer, from the script's placement numbers |
| `PLACE-OFFSCREEN` | No block that serves one of the top two jobs lies wholly below the first screen, at desktop or at phone. | no | reviewer, from the script's placement numbers |
| `PLACE-SHARE` | A block's share of the first screen is in proportion to how full it is and to the rank of the job it serves. | no | reviewer, from the script's placement numbers |
| `PLACE-ORDER` | Blocks come in the order of the jobs they serve: a block for a lower job does not sit above the block for a higher one. | no | reviewer, from the script's placement numbers |

`PLACE-SHARE`, the working test: fill under 40% while taking over 20% of the
first screen, or a block serving no job taking more of the first screen than
a block serving job 1.

A project may have ruled on a placement, such as two cards drawn as a matched
pair. Cite the ruling when it covers the rule and the file; the finding stays
in the report.
