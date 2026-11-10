# Test-first, as this pipeline requires it

Self-contained. The `tdd` skill, if installed, adds depth; nothing here
depends on it.

## The loop

One behaviour at a time: write the test, run it, **watch it fail**, write the
least code that passes, run it green, refactor only while green. Then the
next behaviour. Never all the tests then all the code: bulk-written tests
describe imagined behaviour and end up asserting shapes rather than outcomes.

**Paste the red.** Every task report carries the failing output from before
the implementation existed, in a fenced block. The pipeline's check for
closing Phase 5 looks for it. A test that passes the first time you run it is
broken until proven otherwise: break the code, watch it go red, restore.

Red-before-green is Gate 2 applied at write time. Done properly, Phase 6 is a
sweep for the ones that slipped, not the first mutation.

## What a good test is

- Exercises a real code path through the public interface. It reads like a
  specification: "learner picks a run" says what capability exists.
- Asserts an outcome, not a call sequence. `toHaveBeenCalled()` alone is not
  an assertion; a callback firing thirteen times passes it.
- Survives an internal refactor. If renaming a private function breaks it, it
  was testing implementation.
- Would fail if the feature were deleted whole.
- Covers both directions of any threshold: a cap invites gutting, a floor
  invites padding.
- Derives its subject list from the registry, never a hand-written list.

## Mocking

Mock at system boundaries only: external APIs, payment, email, time,
randomness. Do not mock your own modules, internal collaborators, or anything
you control.

**Never mock the thing under test.** A mocked `apply` cannot observe any bug
whose mechanism is state actually changing; that substitution hid a Critical
through four gates. If a test is only tractable with a mock, the logic is
tangled with an adapter and belongs in the core (`build-standards.md`).

Design boundaries for mockability: inject the dependency instead of
constructing it inside; one specific function per external operation instead
of a generic fetcher with conditionals.

## Vertical slices

A task is one thin capability end to end: core rule, port, adapter, UI, its
test. Its test of thinness: could you demo it the moment it closes? Thinnest
first, a walking skeleton, then widen. `build-standards.md` has the full
rule; this is the same discipline one layer down.

## Task report shape

```
# S<n> <slice name>
Brief claims: CONFIRMED / PARTIALLY CONFIRMED / NOT CONFIRMED, each with file:line
## Red
<fenced failing output, before the implementation>
## Green
<fenced passing output>
## Control
<what was broken to prove the test bites, and the red it produced>
## Done
<each criterion from "What done looks like", word for word: MET with what was run and seen, or NOT MET with why>
## Deviations
<declared, with why>
## What went wrong
<kept in>
```
