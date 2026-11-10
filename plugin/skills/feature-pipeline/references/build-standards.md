# Build standards

Non-negotiable for every task in this pipeline: **vertical slices**, **test
first**, **hexagonal core**, **features as plugins**.

---

## Vertical slices

A task is **one thin capability working end to end** — core rule, port, adapter,
UI, and its test — not one layer of many capabilities.

```
        HORIZONTAL (never)              VERTICAL (always)
   ┌────────────────────────┐     ┌────┬────┬────┬─────────┐
UI │  task 3: all the UI    │     │    │    │    │         │
   ├────────────────────────┤     │ s1 │ s2 │ s3 │   s4    │
API│  task 2: all the routes│     │    │    │    │         │
   ├────────────────────────┤     │    │    │    │         │
DB │  task 1: all the schema│     │    │    │    │         │
   └────────────────────────┘     └────┴────┴────┴─────────┘
   nothing demoable until 3        each slice demoable at close
```

**The test: could you demo it the moment the task closes?** If the answer needs
"once task N lands too", it is horizontal.

**Why this pipeline requires it.** Horizontal slicing is the mechanism behind
the worst failure this pipeline exists to catch: every layer present, every task
green, and nothing wired together — a renderer returning `null` while scripts,
configs, and schema all look live. With vertical slices that state is
unreachable, because a slice that isn't wired never went green in the first
place.

It also makes Gate 3 continuous rather than terminal: each closed slice is
browser-verifiable immediately, so Phase 7 confirms what you already saw instead
of discovering it.

### Slicing well

- **Thinnest first.** A walking skeleton — one narrow path all the way through —
  before any widening. It de-risks the boundaries earliest.
- **Slice by capability, not by entity.** "Learner picks a run" beats "runs
  table", which is a layer wearing a capability's name.
- **Shared groundwork rides the first slice that needs it.** A migration is not
  its own task; it lands with the slice that requires it.
- **If a slice can't be thin, the contract is wrong.** Widening it is the fix of
  last resort — first check whether the host is coupled to feature internals
  (below).

### The relationship to the other three

| | Says |
|---|---|
| Hexagonal | where the layers are |
| Vertical slicing | cut **across** them, never along |
| Plugin edges | a slice is self-contained, registered, deletable |
| TDD | each slice is red → green → refactor, on its own |

`tdd.md`'s warning against writing all the tests first is this same rule one layer down: don't write
all the tests then all the code, and don't build all the layers then wire them.

---

## TDD — red before green, always

The loop, what makes a good test, and the mocking rules are in
`references/tdd.md`, self-contained. What this section adds:

**Paste the red.** Task reports must include the failing output from before the
implementation existed, not only the passing run. This is the pipeline's
evidence that the test was ever capable of failing.

**Red-before-green *is* Gate 2, applied at write time.** A test observed failing
at birth is already mutation-verified. Done properly, Phase 6 becomes a check on
the ones that slipped rather than a full sweep — which is why TDD here is a
requirement, not a preference.

**A test that passes the first time you run it is broken until proven
otherwise.** Break the code deliberately, watch it go red, restore.

**Never mock the thing under test.** A mocked `apply` cannot observe any bug
whose mechanism is state actually changing — that substitution hid a Critical
through four review gates. If mocking is the only way to make a test tractable,
that is a design signal: the logic is tangled with an adapter and belongs in the
core (below).

### Test-shape checklist

- [ ] Observed failing before the implementation existed, output pasted
- [ ] Asserts an outcome, not a call sequence — `toHaveBeenCalled()` alone is
      not an assertion; a callback firing 13 times passes it
- [ ] Subject list derived from a registry, never hand-written
- [ ] Covers both directions of any threshold — a cap invites gutting, a floor
      invites padding
- [ ] Would fail if the feature were deleted whole

---

## Hexagonal core, plugin edges

### Ports and adapters

```
        ┌─────────────── adapters (swappable) ───────────────┐
        │  HTTP route   DB repo   Stripe client   React view │
        └──────┬──────────┬───────────┬──────────────┬───────┘
               │          │           │              │
            ┌──▼──────────▼───────────▼──────────────▼──┐
            │              PORTS (interfaces)            │
            ├────────────────────────────────────────────┤
            │   DOMAIN CORE - pure, no framework imports  │
            │   rules, state machines, calculations       │
            └────────────────────────────────────────────┘
```

**The core imports nothing from the edges.** No framework, no ORM, no SDK, no
`fetch`. If the domain layer imports your database client or payment SDK, it is
not the core — it is an adapter wearing the core's name.

The test for whether you got it right: **the core is testable with no mocks at
all.** You cannot construct a scenario needing one, because there is nothing
external in scope. If a core test needs a mock, the boundary is in the wrong
place — move the dependency out to an adapter behind a port.

### Features as plugins

New capability is **registered against a stable contract**, never bolted into a
switch statement or a growing conditional.

- Define the contract once — what a feature must provide to participate.
- Each feature implements it and **registers itself**.
- The host iterates the registry. It knows the contract; it never knows the
  members.

Consequences to insist on:

- Adding a feature touches **its own files plus one registration line.** If it
  requires edits scattered across the host, the contract is wrong.
- Removing a feature is deleting its directory and its registration.
- **Derive every subject list from the registry.** A hand-written list that
  duplicates the registry silently stops covering new members — a defect Gate 2
  finds repeatedly.

### Where this bites in practice

- A rule that lives in a React component or a route handler is core logic in an
  adapter. It cannot be tested without mounting the adapter, so it gets tested
  with mocks, so the mocks hide the bug.
- A domain calculation reaching for the request object has taken a hidden
  dependency on transport. Pass it a value.
- Two adapters sharing logic means that logic is core. Move it inward rather
  than extracting a helper sideways.

---

## Domain skill routing

Optional. Invoke any that exist **at design time (Phase 1), before the plan
exists**; they change what gets built, not how it gets reviewed. The run is
correct without them.

| The work touches | Invoke |
|---|---|
| Any UI, layout, visual design | `frontend-design:frontend-design`, then `impeccable` for polish and a11y |
| Payments, checkout, subscriptions, pricing | `stripe:stripe-best-practices`, `stripe:stripe-docs` |
| Postgres, schema, RLS, migrations, query performance | `supabase-postgres-best-practices` |
| Charts, dashboards, any data visualisation | `dataviz` |
| Architecture-level restructuring | `improve-codebase-architecture` |
| Anything Claude/Anthropic API-shaped | `claude-api` |

If a listed skill is not installed, say so once and continue.
