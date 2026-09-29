# The mechanical spec gate — what your spec must clear

The harness runs `schema_conformance` over every theorem's *elaborated type* (so it sees through
notation, implicits and coercions a regex cannot) and **rejects the stage** if it fires. You do not
invoke it; you satisfy it. A rejection comes back naming the theorem, the check and the RULE, and you
fix it and resubmit.

The checks live in `lean/<Crate>/LusternaChecks.lean` and the attributes in
`lean/<Crate>/LusternaSchemas.lean` — both harness-owned: do not edit or delete either. `import
<Crate>.LusternaSchemas` in your spec module to write the annotations.

**Every theorem declares exactly one family**, each on its own line above the theorem:

| annotation | what the gate then enforces |
| --- | --- |
| `@[lusterna]` | a CHECKED PROPERTY: a WP triple over a declared target whose postcondition is pure, mentions the result, and genuinely constrains it |
| `@[lusterna_lemma "why"]` | nothing but a non-empty reason — a supporting lemma, deliberately out of scope |

There is no invariant-vs-hoare split, and no third family. A preservation, a postcondition, and a
conditional postcondition are one shape, differing only in whether a precondition is present.
**Invariance is not a label** — whether a preserved property holds across the whole system is the PROVE
stage's conclusion (a base case plus a preservation for every operation), never a per-theorem
attribute.

## The checked shape — a WP triple over a target

A `@[lusterna]` property is an Aeneas weakest-precondition triple over one of the campaign's declared
target functions:

```lean
@[lusterna]
theorem t (x : Input) : f x ⦃ r => <claim about r> ⦄
@[lusterna]
theorem t (x : Input) (hpre : Pre x) : f x ⦃ r => <claim about r> ⦄   -- with a precondition
```

The triple `f args ⦃ r => post r ⦄` says: `f args` runs, **terminates without failing or diverging**,
and its result satisfies `post`. That single form does for free everything the old equational shape had
to police by hand:

- **one execution** — the triple runs the target exactly once; you never write `f args = ok r` as a
  hypothesis (that equational form is no longer a checked shape).
- **totality** — a triple over a computation that could fail or diverge is *false*, not vacuously true,
  so the property can never be satisfied by a failing call.
- **a fresh, unpinned result** — `r` is bound by the triple, so it cannot be pinned or aliased in a
  hypothesis the way a hand-written binder could be.

`r` is whatever the target produces: a tuple for a multi-output function, a value-and-post-state pair
for a state transformer (`r.fst` the return, `r.snd` the post-state — destructure it in the
postcondition). A preservation is `f args s ⦃ r => Inv r.snd ⦄`.

**Everything the gate checks now lives in the postcondition.** It must:

1. **mention `r`** — otherwise it claims nothing about what `f` produced;
2. **be a PURE proposition** — ordinary `Nat`/arithmetic over the projected `.val` fields, naming no
   nested fallible measurement;
3. **genuinely constrain `r`** — not a self-assuming tautology, not `True`.

## The rules

A theorem that fails the shape its own author declared is a fact, not a judgement. Findings name the
**rule** that broke, so the fix is mechanical:

| rule | what it means |
| --- | --- |
| `no_schema_declared` | every spec theorem needs a family; a helper declares `@[lusterna_lemma "why"]` |
| `multiple_schemas_declared` | both `@[lusterna]` and `@[lusterna_lemma]` — a theorem is one or the other |
| `private_declaration` | a private theorem's annotation may not survive module export; make it non-private |
| `not_a_target_triple` | the conclusion is not a WP triple `f args ⦃ r => … ⦄` (e.g. an equational `f args = ok r → …`) |
| `triple_not_over_target` | a triple, but its computation is not one of the declared target functions |
| `conclusion_ignores_output` | the postcondition never mentions the result `r`, so it says nothing about what the call produced |
| `claim_not_failsafe` | the postcondition names a nested fallible `Result` measurement, which can fail OPEN |
| `vacuous_claim` | the postcondition does not constrain `r` — a self-assuming tautology, or `True` |

**Why default-reject is safe here and nowhere else.** Applied to arbitrary theorems these rules would
flag every honest use of `→`, `∨`, `¬`. Applied to a theorem whose author declared it a checked
property, they are exact — the annotation is what buys the strictness.

### `conclusion_ignores_output` — the property must be *about* the result

The postcondition must mention at least one INFORMATIVE component of `r`. A preservation that speaks
only of the post-state (`r.snd`) and ignores the return value is fine — "at least one", not "every". An
output of a type that carries no information (`Unit`, any single-constructor-no-field type) does not
count, so a void `Unit` return leaves nothing to demand.

### `claim_not_failsafe` — never let a measurement fail open

A postcondition is **pure**: it names no nested `Aeneas.Std.Result` measurement. The moment a
measurement of the result appears inside the postcondition, its failure can make the claim vacuously
true — the exact defect the gate exists to prevent:

```lean
f x s ⦃ r => g r.snd = ok v ⦄                 -- FLAGGED: true whenever `g r.snd` fails
f x s ⦃ r => measure r.snd = ok t → φ t ⦄     -- FLAGGED: the implication is vacuous on failure
f x s ⦃ r => r.snd.total.val = s.total.val ⦄  -- clean: pure arithmetic over projected `.val` fields
```

If the property is genuinely about a measurement of the result, **project through it** — state the
claim over the `.val` fields directly — or bridge the measurement in a separate `@[lusterna_lemma]` and
reference that. The triple already guarantees `f` itself succeeds; you never need to re-assert it.

### `vacuous_claim` — the postcondition must say something

The check peels the postcondition looking for a claim that assumes itself. It sees through logical
structure and definitions, so a tautology cannot hide:

```lean
f x ⦃ r => r > k → r > k ⦄                     -- FLAGGED: `intro h; exact h`
f x ⦃ r => SelfImplied r ⦄                     -- FLAGGED even behind layers of one-line `def`s
f x ⦃ r => (r > k → r > k) ∧ True ⦄            -- FLAGGED: peeled through `∧`
f x ⦃ r => Wrapper r ⦄                         -- FLAGGED: Prop-structure fields are instantiated
f x ⦃ r => r > 0 → r > k ⦄                     -- CLEAN: a genuine conditional (`B` not among `A`'s conjuncts)
```

Project-local definitions are unfolded at the use site, and Prop-valued structures/inductives have
their fields scanned. Running out of budget reports `SKIPPED` rather than returning clean.

## Governance — the checked family cannot be quietly emptied

Three module-level rules keep the checked family honest. They block the stage the same way a
per-theorem finding does; you will see them attributed to `(module)`.

| rule | what it means |
| --- | --- |
| `unbridged_anchor` | a measurement INFER named an anchor is referenced by NO theorem — add a bridge tying your projection to the real function |
| `anchor_uncovered` | an anchor is referenced only by an exempt lemma (or nothing), never by a CHECKED triple — a real anchor must be carried by a checked property |
| `no_checked_execution` | SYSTEMIC: no theorem is a checked triple over ANY target — the checked family is empty, so the gate saw nothing to constrain |

`no_checked_execution` is the backstop against the whole spec being written as lemmas. If it fires,
state at least one target's property as a checked triple rather than demoting the core to
`@[lusterna_lemma]`. `anchor_uncovered` is the finer version: a getter the properties reason about must
itself be measured by a `@[lusterna]` triple (`getter args ⦃ t => t.val = <projection> ⦄`), so the
core is grounded in a *checked* fact, not merely a stated one.

## `@[lusterna_lemma]` is the escape hatch — honest, not free

A relational or two-run property — injectivity, determinism, cancellation, non-interference — genuinely
needs two executions; a bridge lemma or a pure arithmetic helper has no single target execution. These
declare a lemma and the shape rules do not run on them. The reason string is reported, so do not use it
to smuggle a property that really is a checked one: SPEC-JUDGE reads those justifications, and a
misdeclared family is a defect it will name. The governance rules above are what stop the escape hatch
from swallowing the core.

## What this does NOT check

The annotation is written by the same agent whose work is checked, so this verifies **form, not
fitness**. A property dressed as a lemma with a plausible-but-wrong reason, or a projection that does
not faithfully mirror the real code, conforms perfectly. Whether the property means the right thing is
SPEC-JUDGE's job, and yours.

## The principle behind the check, if you want it in one line

A checked property runs its target exactly once and states a pure, non-vacuous claim about what came
out — so it cannot be satisfied by a call that failed, cannot pin its own result, and cannot assume the
very thing it claims. State it as a triple over a target with a plain proposition inside `⦃ r => … ⦄`,
and it clears the gate.
