# Dispatch contracts

Copy these into subagent prompts verbatim. The phrasing is load-bearing — each
clause exists because its absence shipped a defect.

---

## Implementer contract

> **Verify the brief, do not trust it.** Trust the source and report the
> difference. For every factual claim the brief makes about existing code, open
> the file and check before acting on it. Mark each claimed defect **CONFIRMED /
> PARTIALLY CONFIRMED / NOT CONFIRMED** in your report.
>
> If a premise turns out to be wrong, **stop and escalate rather than routing
> around it.** A wrong premise is a design question and may change what should
> ship. Do not quietly build the nearest thing that works.
>
> **When your report says you swept something, enumerate it** — the exact
> command, its raw output, and your ruling on each hit. A count without the list
> is the failure mode.
>
> **Run the thing rather than reasoning about it.** If a claim can be settled by
> executing something — a test, a probe, a query, ten runs of a suspected flake —
> execute it and paste the output.
>
> A justified deviation from the brief is welcome; a silent one is not. Declare
> it and say why.
>
> **The criteria under "What done looks like" are the definition of done for
> your slice.** Read them before you write a test, and build to them, not to
> the nearest thing the brief's steps produce. Your report answers each one by
> its words: **MET**, with what you ran and saw, or **NOT MET**, with why. A
> criterion you think is wrong or cannot be met is an escalation, not a thing
> to reword.

## Reviewer contract

> **Do not trust the implementer's report.** Re-derive the specific counts named
> below, **by a different route than the implementer used.** A sweep confirmed a
> second way and reconciled exactly is evidence; a sweep confirmed the same way
> is the same claim twice.
>
> Where a claim cannot be verified from the diff, **say so plainly** rather than
> passing it through.
>
> You are given the same criteria the implementer was. For each one it marked
> **MET**, check the thing itself, not the report's sentence about it.
>
> For every new or modified guard: **break it, observe red, revert.** Paste the
> failing output. A green guard is not evidence that it checks anything.
>
> Name each count to re-derive explicitly when you dispatch this. A reviewer told
> to "check the work" checks nothing in particular.

## Verdict rubric

| Verdict | Means |
|---|---|
| **CONFIRMED** | Opened the source; the claim holds as stated. |
| **PARTIALLY CONFIRMED** | Holds in part. State exactly which part fails and what the source actually does. |
| **NOT CONFIRMED** | The claim is false. **Stop. Escalate.** Do not proceed on the corrected premise without a decision. |
| **UNVERIFIABLE** | Cannot be settled from available source or by running something. Say so; do not guess. |

---

## Why each clause is here

| Clause | The failure it prevents |
|---|---|
| Verify the brief | A wave ran 13 tasks; the plan was materially wrong in **7** — a false central premise that would have re-broken a previously fixed figure, three tasks resting on "not in the generated types" for a fully-typed table, a table short by a whole site. |
| Escalate, don't route around | Two escalations changed what shipped. Silent workarounds ship the wrong feature quietly. |
| Enumerate the set | **Eight** self-review completeness claims failed on one plan, all the same shape: asserting completeness over a set never listed. A "full jargon sweep" missed the first occurrence of the very term being glossed. |
| Re-derive by a different route | The one sweep that survived review was re-derived a second way and reconciled exactly. |
| Break the guard | A test suite where **5 of 11 tasks** shipped a test that could not fail — caught only by mutation, never by reading a diff. One prompt-injection strip could be deleted whole with 14 tests still green. |
| Build to the criteria | Until October 2026 an item's acceptance criteria were read only at Gate 7, after the build: no builder's brief carried them, so a slice could pass its own tests and still not be what the person had agreed done was. |
| Declare deviations | Two deliberate deviations were reviewed and **both ruled correct**. Agents hide what they expect to be punished for. |
