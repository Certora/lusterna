# The mechanical spec gate — what your spec must clear

The harness runs two checks over every theorem's *elaborated type* (so they see through notation,
implicits and coercions a regex cannot) and **rejects the stage** if either fires. You do not invoke
them; you satisfy them. A rejection comes back naming the theorem, the check and the RULE, and you
fix it and resubmit.

The checks live in `lean/<Crate>/LusternaChecks.lean` and the attributes in
`lean/<Crate>/LusternaSchemas.lean` — both harness-owned: do not edit or delete either. `import
<Crate>.LusternaSchemas` in your spec module to write the annotations.

**Every theorem declares exactly one family**, each on its own line above the theorem:

| annotation | what the gate then enforces |
| --- | --- |
| `@[lusterna]` | a CHECKED PROPERTY: exactly one declared-target execution binding fresh outputs; every other hypothesis a FAIL-SAFE claim; a FAIL-SAFE conclusion that mentions ≥1 output |
| `@[lusterna_lemma "why"]` | nothing but a non-empty reason — a supporting lemma, deliberately out of scope |

A **fail-safe claim** is either a plain proposition over the produced values (naming no fallible
measurement), or `P args = ok true` for a project-local failure-strict `Result Bool` `P` — see
[Failure-strictness](#failure-strictness) below. There is no invariant-vs-hoare split: a preservation
(`Inv s → f → Inv s'`), a postcondition, and a conditional preservation are one shape,
differing only in whether a precondition is present. **Invariance is not a label** — whether a
preserved property holds across the whole system is the PROVE stage's conclusion (a base case plus a
preservation for every operation), never a per-theorem attribute.

**Precedence, so a rejection stays actionable.** A theorem that does not match the checked shape gets
that finding *alone*; provenance (`assumed_postcondition`) is only applied once the shape is right,
because a taint finding on a mis-shaped theorem buries the one message you can act on.

**`@[lusterna_lemma]` is the escape hatch, and it is honest, not free.** A relational or two-run
property — injectivity, determinism, cancellation, non-interference — genuinely needs two executions;
a bridge lemma or a pure helper has no single target execution. These declare a lemma and the shape
rules do not run on them. The reason string is reported, so do not use it to smuggle a property that
really is a checked one: SPEC-JUDGE reads those justifications, and a misdeclared family is a defect
it will name.

**Why the shape rules can block at all.** `assumed_postcondition` has three shapes it cannot tell
from a cheat — a relational antecedent, a bound on an intermediate, a case split in the conclusion —
which is why it was once advisory. The first needs two executions (`execution_not_unique` forbids it);
the other two constrain a produced value and are caught by provenance itself. So on a conforming
`@[lusterna]` theorem a finding is a defect rather than a prompt. The annotation is what buys that.

## `assumed_postcondition`

This check asks where a fact came from, rather than what shape a statement has:

> A fact about the subject's output or post-state may enter only through the subject's own
> execution relation.

For the canonical stateful shape `f : Input → State → Result (Output × State)`, the theorem should
read `∀ x s y s', Pre x s → f x s = ok (y, s') → Post x s y s'`. Everything before the execution
hypothesis talks about inputs and the pre-state; everything about the post-state is the conclusion.

```lean
-- clean: the only thing said about s' is the goal.
theorem t (x s s' y) (hpre : Pre x s) (hexec : f x s = ok (y, s')) : s'.var = s.var

-- FLAGGED, `"is_conclusion": true`: `exact hvar` closes it. Logically valid, and it verifies
-- nothing about f — the same statement holds for an f that does anything at all.
theorem t (x s s' y) (hpre : Pre x s) (hexec : f x s = ok (y, s'))
          (hvar : s'.var = s.var)     : s'.var = s.var
```

**Nothing local separates those two.** The execution is real, `Pre x s` is a genuine precondition,
every hypothesis type-checks, and both versions are provable. What differs is where the fact about
`s'` came from — which is why this check follows provenance rather than shape.

**Taint closes, and an equality is an assumption.** Naming a tainted value does not launder it, and
naming it is itself a hypothesis the theorem was handed:

```lean
(hz : z = s'.var)   -- FLAGGED, and it also makes z tainted
(hz2 : 0 < z)       -- FLAGGED — z is tainted, and this constrains it
```

Both are reported: two findings, one root cause, report it once. An equality is not a `let` — it is
a proposition the caller supplied, and it can be the entire cheat with nothing downstream to catch.
`(h : s' = s)` closes a preservation goal by `exact h`, and `(h1 : z = s'.var) (h2 : z = s.var)`
rebuilds one across two hypotheses that each look innocent.

**An execution must bind fresh, unpinned results.** The execution binder says what was run; it must
not also say what came out. This applies to declared continuations too — authorization buys the
chaining edge, not a licence to constrain.

```lean
(hexec : f x s = ok (y, s))                    -- FLAGGED: the post-state IS the pre-state
(hexec : f x s = ok (⟨true⟩, s'))              -- FLAGGED: the output value is pinned
(hexec : f x s = ok (y, { s with var := 10 })) -- FLAGGED: the post-state is pinned structurally
(hexec : f x s = ok (some y, s'))              -- FLAGGED: `some` rather than `none` is a claim
(hstep : rebalance y s' = ok (z, s'))          -- FLAGGED even when authorized: state pinned
(hexec : f x s = ok (y, s'))                   -- clean: fresh, unconstrained, claim it in the goal
```

Each names what went wrong: `output aliases an argument of the same call`, `output variable is bound
twice`, or `output pins a value or a constructor branch`. Rust's own `Result` is the one exception
to the constructor rule: a Rust function that returns `Result<T,E>` translates with its success as
`ok (Result.Ok v, s')` — the Aeneas execution `ok` around the Rust outcome `.Ok` — so choosing that
`.Ok` is the translation's own shape, not a value you pinned, and it stays clean. A declared continuation called on *clean* arguments is exempt from all of this: it
never touches target-derived data, so it is an ordinary precondition.

**One execution owns each output.** A value may be *consumed* by any number of later calls and
*produced* by exactly one, because sharing an output variable is a way to state a relational premise
without writing one:

```lean
-- FLAGGED (`output is produced by more than one execution`): injectivity's `a = b` with the
-- equality hidden in the binder names. Every per-call test passes.
theorem t (x z y) (h1 : f x = ok y) (h2 : f z = ok y) : x = z

-- FLAGGED (`output aliases a root input`): `x` existed before any target ran, so this constrains
-- the continuation instead of naming its result. Bind `ok z` and relate `z = x` in the goal.
(hg : g y = ok x)
```

**The conclusion is exempt, so a named predicate is where the cheat moves.** The theorem's own
telescope can be spotless while the antecedent sits inside the postcondition:

```lean
def WeakPreservation (s s' : State) : Prop := s'.var = s.var → s'.var = s.var
-- FLAGGED (`postcondition predicate assumes its own antecedent`): `intro h; exact h`.
theorem t … (hexec : f x s = ok (y, s')) : WeakPreservation s s'
```

The conclusion is walked **compositionally**, so the predicate cannot hide behind logical structure:
`WeakPreservation s s' ∧ True`, `∃ z, …`, a trusted wrapper, or three layers of one-line `def`s all
resolve. Project-local definitions are unfolded at the use site, Prop-valued **structures and
inductives** have their constructor fields instantiated and scanned, and running out of budget
reports `SKIPPED` rather than returning clean.

Two reasons distinguish the findings. `assumed antecedent in the conclusion` is the high-signal one.
`quantifier over the post-state in the conclusion` covers a bounded quantification like
`∀ i, i < s'.n → arr[i] = …`, which is usually fine — but it is reported rather than silently
exempted, because "mentions a locally bound variable" is not a licence: `∀ i, (i = i → P s') → P s'`
satisfies it and is still a free assumption.

**An assumption need not be a proof.** Every binder's type is checked, not just `Prop`-typed ones —
`(h : { u : Unit // s'.var = s.var })` is data, and `exact h.property` closes the goal.

**Being execution-shaped does not make a hypothesis trustworthy.** `g args = ok out` over the
post-state is not a neutral observation — it asserts that `g` *succeeds* there, which restricts the
theorem just as a success-guarded antecedent would, and the equation can carry the whole
postcondition on its own:

```lean
def checkVarPreserved (before after : State) : Result Unit :=
  if after.var = before.var then ok () else fail ...

-- FLAGGED: `simp [checkVarPreserved] at hcheat; exact hcheat`. Nothing under verification runs in
-- this hypothesis, yet it matches `g args = ok out` exactly.
theorem t (…) (hexec : f x s = ok (y, s')) (hcheat : checkVarPreserved s s' = ok ())
    : s'.var = s.var
```

So a fallible call is trusted only when it consumes nothing tainted and mentions nothing tainted (a
precondition expressed through a measurement of the inputs and pre-state), or when it consumes
tainted data and its function is a **declared continuation**. Note the first test covers the whole
hypothesis, not just its arguments: `(hg : g z = ok y)` runs on clean inputs and still pins `y`, the
subject's own output. Anything else touching the post-state is reported with
`"reason": "assumes success on tainted state"`; a plain constraint on a tainted variable reports
`"reason": "constrains tainted variable"`.

**A target is not implicitly its own continuation.** Being the target makes a function a source of
values, not a licence to consume them: `(hagain : f x2 s' = ok (z, s''))` assumes a *second* run of
`f` succeeds on the post-state, which is the same assumption any other call there would be. An
iterative or compositional theorem names its target in both lists.

Two consequences worth knowing before you read a finding:

- **A post-state measurement named in a hypothesis is reported** — `(hafter : total s' = ok t)`, and
  the same shape written as `… : ∀ m, total s' = ok m → k ≤ m`. That is not a false positive by this
  project's doctrine: a measurement's failure must not excuse the property. The fix is the one check
  2 already prescribes — `total s' ⦃ m => k ≤ m ⦄`, or `∃ m, total s' = ok m ∧ k ≤ m`, both of which
  are clean here.
- **A genuine next step needs declaring.** `(hstep : settle y s1 = ok (z, s2))` is reported unless
  `settle` is passed as a continuation. That is deliberate: the exemption is a decision you make
  about the property, not something the shape earns.

**`"is_conclusion": true`** means the hypothesis is defeq to the goal or one of its conjuncts —
the theorem is closed by `exact`. That is the strongest form of this defect and rarely anything
else. Without it, read the theorem.

**What stays clean**, verified: the canonical shape; a precondition stated through a measurement of
the *pre*-state (`(hm : measure s = ok t) (hk : k ≤ t)` — clean arguments, so `t` is not
tainted); a chain through a *declared* continuation; both total forms of a post-state measurement in
the conclusion (the triple and the existential); and the `⦃ ⦄` triple over the subject itself.

A triple over the subject is **analysed**, not waved through — the post-state being lambda-bound
does not make the defect impossible, and
`f x s ⦃ (y, s') => s'.var = s.var → s'.var = s.var ⦄` assumes exactly what it claims. The
postcondition's own binders are seeded and the binder rule applied inside it.

**Known false positives, both unavoidable:**

- **Relational antecedents.** Injectivity `(h1 : f x = ok a) (h2 : f y = ok b) (hab : a = b) : x = y`
  is a hypothesis about outputs by construction — as are determinism, cancellation, and
  non-interference. Nothing distinguishes them from a cheat, because the strict rule is about
  single-execution properties and these are not. Declare a genuine two-run property
  `@[lusterna_lemma "why"]`, where the shape rules do not run on it.
- **Bounds on intermediates.** `(hexec : f x s = ok (y, s1)) (hb : s1.n < 100)` before a downstream
  call is a real overflow guard *and* a domain restriction on the subject's own output. Decide
  which one it is by asking whether the bound is needed to state the property or to dodge it.

A non-execution case split in the conclusion (`… : s'.ok = true → s'.total = s.total`) is also
flagged: an elaborated type has no hypothesis/conclusion boundary to find, so it is indistinguishable
from a cheat placed after the execution hypothesis. It is arguably a weakening in its own right.

## `schema_conformance`

`assumed_postcondition` hunts for a bad shape, which is why its *silence* is ambiguous: "clean, or
nothing I recognise". This check inverts that. FORMALISE declares each theorem's family, and this
verifies the declaration:

```lean
@[lusterna]                            -- a checked property: one target run, a fail-safe claim
@[lusterna_lemma "pure arithmetic helper"]   -- a supporting lemma, declared out of scope
```

**A theorem that fails the shape its own author declared is a fact, not a judgement.** Findings name
the **rule** that broke, so the fix is mechanical:

| rule | what it means |
| --- | --- |
| `no_schema_declared` | every spec theorem needs a family; a helper declares `@[lusterna_lemma "why"]` |
| `multiple_schemas_declared` | both `@[lusterna]` and `@[lusterna_lemma]` — a theorem is one or the other |
| `execution_not_unique` | a checked property runs exactly one declared target (found 0 or ≥2) |
| `execution_output_not_fresh` | the execution binder pins its own output (`assumed_postcondition`'s rule, reused) |
| `claim_not_failsafe` | a precondition or the conclusion names a measurement but is neither a pure proposition nor `P args = ok true` for a failure-strict `P` |
| `predicate_not_failure_strict` | the `Result Bool` predicate in a `P args = ok true` claim is not in the failure-strict fragment (below) |
| `conclusion_ignores_output` | conforms otherwise, but mentions none of the execution's outputs, so it says nothing about what the call produced |
| `private_declaration` | a private theorem's annotation may not survive module export |

**Why default-reject is safe here and nowhere else.** Applied to arbitrary theorems this rule would
flag every honest use of `→`, `∨`, `¬`. Applied to a theorem whose author declared it a checked
property, it is exact — the annotation is what buys the strictness.

**Two rules worth understanding before you read a finding.**

`conclusion_ignores_output` is what stops a conforming theorem from being vacuous: the conclusion
must mention at least one INFORMATIVE output of the execution. "At least one", not "every" — a
preservation that speaks only of the post-state and ignores the return value is a first-class checked
property. An output of a type that carries no information (`Unit`, any single-constructor-no-field
type) does not count, so a void `Unit` return leaves nothing to demand.

`claim_not_failsafe` is where a measurement stated the wrong way lands. The two accepted forms —
a pure proposition, or a failure-strict `P args = ok true` — are exactly the total forms; a
measurement guarded in a hypothesis (`g s' = ok v`), an implication antecedent
(`measure s = ok t → …`), an inline existential, or an inline `do`-block over library `Bind.bind` is
none of them. State the property over the projected `.val` values, or bind the measurement in a
`Result Bool` predicate.

### Failure-strictness — the fragment behind `predicate_not_failure_strict`

When a claim IS a `Result Bool` `P args = ok true`, this fragment certifies `P`: if a measurement
inside `P` FAILS, does `P` come out false rather than let the failure through?

```lean
def Inv (s : State) : Result Bool := do
  let t ← measure s
  let b ← other s
  ok (t == b)                        -- every fallible call BOUND, decide on pure data
```

Strictness is then a theorem about the monad, not a heuristic. `bind (fail e) k = fail e` and
`bind div k = div`, while `ok true` is neither `fail` nor `div` — so a body that only ever *binds*
its fallible calls cannot answer `ok true` after one of them failed. You can watch it happen:

```lean
#eval measure bad   -- fail integerOverflow  (the measurement really does fail)
#eval Inv bad       -- fail integerOverflow  — NOT `ok true`. This is the point.
#eval InvOpen bad   -- ok true               — the fail-open version, true "for free" on that state
```

**The rule, in full.** A `Result` may be BOUND or RETURNED, never PASSED. If nothing ever *receives*
a `Result` value, nothing can observe — hence discard — a failure:

```
strict(bind x k)      = strict(x) ∧ strict(k v)          -- the only way to consume a Result
strict(ite c a b)     = resultFree(c) ∧ strict(a) ∧ strict(b)
strict(match d alts)  = resultFree(d) ∧ ∀ alt, strict(alt)
strict(f a₁ … aₙ)     = ∀ i, resultFree(aᵢ)  ∧  f itself certified, if project-local
otherwise             = REPORTED
```

`ok`, `pure` and `massert` need no rule of their own — their arguments are `resultFree`.
**Default-reject:** the last line is a finding, never "unrecognised, carry on". This is what makes
the check closed rather than a blocklist forever chasing Aeneas's helper set:

```lean
ok (! ok? (measure s))                                -- reported: `measure s` is an ARGUMENT
match measure s with | fail _ => ok true | …          -- reported: the scrutinee is an argument
if ok? (measure s) then … else ok true                -- reported: so is an `ite` condition
let r := measure s; match r with | fail _ => ok true  -- reported: a plain `let` is not a bind
do let b ← BadHelper s; ok b                           -- reported: followed INTO BadHelper
do let a ← Result.ofOption s.head? .panic; ok (a == 0) -- CLEAN: ofOption never RECEIVES a Result
```

**Recursive predicates certify**, the difference between a useful check and a toy one — a real
invariant folds over accounts. `def Inv : List Acct → Result Bool | [] => ok true | a :: r => do …`
compiles to `fun l => List.brecOn l Inv._f`, unreadable to any syntactic walk, so the check reads the
*equation lemmas*. Structural and well-founded recursion both work. A `List.foldlM (fun … => do …)`
the walk cannot see inside reports as **not certified** (no evidence of a defect — `Aeneas.Std.Result`
has no `tryCatch`, so it is almost certainly strict — but "almost" is not the guarantee); rewrite it
as explicit recursion and it certifies. One genuine hole: a predicate taking a `Result`-returning
function as a PARAMETER (`def Inv (f : St → Result Bool) s := f s`) certifies whatever `f` is later
instantiated to; nothing generated looks like this, but do not write it by hand.

**Prefer the pure form.** All of this machinery is dormant when the claim names no measurement: a
plain proposition over `.val` projections is fail-safe with nothing to certify, and it is the
readable form. Reach for a `Result Bool` predicate only when you genuinely need to bind a measurement.

**What this does NOT check.** The annotation is written by the same agent whose work is checked, so
this verifies **form, not fitness**. A property dressed as a lemma to skip the checks, or a projection
that does not faithfully mirror the real code, conforms perfectly. Whether the property means the
right thing is SPEC-JUDGE's job, and yours.

## The principle behind the checks, if you want it in one line

A fact about a function's output should enter the proof through that function's own execution, not
through an independent hypothesis that happens to mention the same variable, and a claim must never be
satisfiable by a state nobody could describe. `assumed_postcondition` asks whether the call's
*outputs* were smuggled in from somewhere other than the call; `schema_conformance` asks whether the
theorem has the one checked shape and whether its claim can fail open. Neither is a substitute for
reading the theorem — they're cheap enough to run on everything and precise enough to be worth reading
when they fire.

## `assumed_postcondition` and `schema_conformance` on the same `= ok true` shape

`assumed_postcondition` names `checkInvariant s' = ok true` **as a hypothesis** an anti-pattern, while
a checked preservation concludes exactly `Inv s' = ok true`. Both are right, because the POSITION
is what differs. A predicate on the **post-state, handed to the theorem as a hypothesis** carries the
whole conclusion — that is `assumed_postcondition`'s finding, and it stands. A preservation instead
puts `Inv <post-state> = ok true` in the **conclusion** (which `assumed_postcondition` exempts),
with only `Inv <pre-state> = ok true` (clean arguments, nothing tainted) among the hypotheses.
Write it the other way round and `assumed_postcondition` will tell you.
