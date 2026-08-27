# The mechanical spec gate — what your spec must clear

The harness runs three checks over every theorem's *elaborated type* (so they see through notation,
implicits and coercions a regex cannot) and **rejects the stage** if any fires. You do not invoke
them; you satisfy them. A rejection comes back naming the theorem, the check and the RULE, and you
fix it and resubmit.

The checks live in `lean/<Crate>/LusternaChecks.lean` and the schema attributes in
`lean/<Crate>/LusternaSchemas.lean` — both harness-owned: do not edit or delete either. `import
<Crate>.LusternaSchemas` in your spec module to write the annotations.

**Every theorem declares exactly one schema**, each on its own line above the theorem:

| annotation | what the gate then enforces |
| --- | --- |
| `@[lusterna_invariant]` | `Inv <pre> = ok true` → one declared-target execution → `Inv <post> = ok true`, `Inv` a failure-strict `Result Bool`, **and no other hypothesis** |
| `@[lusterna_hoare]` | `Pre <root inputs> = ok true` → exactly one declared-target execution → `Post <inputs, outputs> = ok true`, both failure-strict |
| `@[lusterna_freeform "why"]` | nothing but a non-empty reason — a supporting lemma, deliberately out of scope |

**Precedence, so a rejection stays actionable.** A theorem that does not match its declared schema
gets that finding *alone*; the provenance and strictness rules are only applied once the shape is
right, because a taint finding on a mis-shaped theorem buries the one message you can act on.

**`freeform` is the escape hatch, and it is honest, not free.** A relational or two-run property —
injectivity, determinism, cancellation, non-interference — genuinely needs two executions and cannot
be a Hoare triple, so it declares `freeform` and the shape rules do not run on it. The reason string
is reported, so do not use it to smuggle a property that really is one of the two schemas: SPEC-JUDGE
reads those justifications, and a misdeclared schema is a defect it will name.

**Why the shape rules can block at all.** `assumed_postcondition` has three shapes it cannot tell
from a cheat — a relational antecedent, a bound on an intermediate, a case split in the conclusion —
which is why it was once advisory. Each is excluded by `hoare` conformance itself
(`execution_not_unique`, `hypothesis_not_a_precondition`, `conclusion_not_a_postcondition`), so on a
conforming theorem a finding is a defect rather than a prompt. The annotation is what buys that.

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
to the constructor rule — Aeneas wraps every fallible function in it, so `ok (Result.Ok v, s')`
stays clean. A declared continuation called on *clean* arguments is exempt from all of this: it
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
the *pre*-state (`(hm : total_supply s = ok t) (hk : k ≤ t)` — clean arguments, so `t` is not
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
  single-execution Hoare properties and these are not. If the theorem is a genuine two-run property,
  report it clean.
- **Bounds on intermediates.** `(hexec : f x s = ok (y, s1)) (hb : s1.n < 100)` before a downstream
  call is a real overflow guard *and* a domain restriction on the subject's own output. Decide
  which one it is by asking whether the bound is needed to state the property or to dodge it.

A non-execution case split in the conclusion (`… : s'.ok = true → s'.total = s.total`) is also
flagged: an elaborated type has no hypothesis/conclusion boundary to find, so it is indistinguishable
from a cheat placed after the execution hypothesis. It is arguably a weakening in its own right.

## `invariant_not_strict`

`assumed_postcondition` asks about a theorem in general. This one recognises one particular — the
**invariant-preservation** theorem — and then asks the only question that makes such a theorem
worth proving:

> If a measurement inside the invariant FAILS, does the invariant come out false?

If it does not, the theorem admits pre-states that cannot even be described — `total_supply`
reverts, so "the invariant holds" for free — and a counterexample is an artefact of the spec rather
than a bug in the code.

**The invariant form this check is about** is a `Result Bool` definition, claimed as `= ok true`:

```lean
def Solvent (s : State) : Result Bool := do
  let t ← total_supply s
  let b ← sum_balances s
  ok (t == b)

theorem transfer_preserves_solvency (amt : U64) (s s' : State) (y : Unit)
    (hinv  : Solvent s = ok true)                 -- the invariant, on the PRE-state
    (hexec : transfer amt s = ok (y, s'))         -- a DECLARED TARGET, producing s'
    : Solvent s' = ok true                        -- the SAME invariant, on the POST-state
```

Why that form: strictness is then a theorem about the monad, not a heuristic.
`bind (fail e) k = fail e` and `bind div k = div`, while `ok true` is neither `fail` nor `div` — so
a body that only ever *binds* its fallible calls cannot answer `ok true` after one of them failed.
Divergence comes along for free. You can watch it happen:

```lean
#eval sum_balances overflowing   -- fail integerOverflow  (the measurement really does fail)
#eval Solvent overflowing        -- fail integerOverflow  — NOT `ok true`. This is the point.
#eval SolventOpen overflowing    -- ok true               — the fail-open version, "solvent" for free
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

`ok`, `pure` and `massert` need no rule of their own — they are ordinary calls whose arguments are
`resultFree`. **Default-reject:** the last line is a finding, never "unrecognised, carry on".

This is what makes the check closed rather than a blocklist forever chasing Aeneas's helper set:

```lean
ok (! ok? (total_supply s))                                -- reported: `total_supply s` is an ARGUMENT
match total_supply s with | fail _ => ok true | …          -- reported: the scrutinee is an argument
if ok? (total_supply s) then … else ok true                -- reported: so is an `ite` condition
let r := total_supply s; match r with | fail _ => ok true  -- reported: a plain `let` is not a bind
do let b ← BadHelper s; ok b                               -- reported: followed INTO BadHelper
do let a ← Result.ofOption s.head? .panic; ok (a == 0)     -- CLEAN: ofOption never RECEIVES a Result
```

That last line matters: `Result.ofOption` cannot launder a failure because none is ever handed to
it — it can only *produce* `fail`, which is the strict direction. A blocklist naming Aeneas's
`Result` helpers would have rejected it.

**Recursive invariants certify**, which is the difference between a useful check and a toy one — a
real invariant folds over accounts. `def Inv : List Acct → Result Bool | [] => ok true | a :: r =>
do …` compiles to `fun l => List.brecOn l Inv._f`, unreadable to any syntactic walk, so the check
reads the *equation lemmas* instead. Structural and well-founded recursion both work.

**Two severities, and the difference is worth reading.**

- `"reason": "fail_open"` — a `Result` value really is handed to something that can discard its
  failure. High signal, and normally a defect.
- `"reason": "not_certified"` — no evidence of a defect, only that the walk cannot certify: an
  `opaque` or `partial` invariant, or a monadic combinator it cannot see inside.
  `List.foldlM (fun … => do …) init l` is the common one, and it is *almost certainly* strict —
  `Aeneas.Std.Result` has no `MonadExcept`/`tryCatch` instance, so `foldlM` has nothing to catch a
  failure *with*. "Almost certainly" is not the guarantee this check exists to give, so it is
  reported. Rewrite the fold as explicit recursion and it certifies.

**The shape gate is deliberately narrow** — false negatives are the accepted cost. All three parts
must hold, and the non-state arguments must agree: the same invariant constant in a hypothesis and
in the conclusion, differing at exactly ONE argument position, whose pre-state is consumed by the
execution and whose post-state is one of the execution's OWN output variables, for a function you
named as a target. Anything looser fires on ordinary measurement chains.

**Silence here is weaker than for `assumed_postcondition` — unless the theorem is annotated.** Nothing is
reported when the theorem is not of this shape at all, since otherwise a whole-module run would print
a line per theorem. So silence means *"certified strict, OR not an invariant-preservation theorem in
this form"*. For a theorem carrying `@[lusterna_invariant]` that ambiguity is gone: `schema_conformance` verifies
the declared shape and reports a mismatch as a finding, so silence there means conforming. That is
the whole reason `schema_conformance` exists. A **near miss** — the conclusion is `Inv … = ok true` but the rest of the shape is absent —
is reported as `SKIPPED`, because that is precisely the case where you would believe the check ran
on your invariant when it did not. An invariant written **inline** in the theorem rather than as a
`def` lands there too (its head is `Bind.bind`, library code, with nothing project-local to walk):
name it as a `def` and re-run.

**What this does NOT say.** Strictness is not non-triviality: `def Inv _ : Result Bool := ok true`
is perfectly strict and says nothing at all. Nor is it a claim that the invariant is the *right*
invariant. Both are still yours to read. One genuine hole in "certified", rather than in coverage:
an invariant that takes a `Result`-returning function as a PARAMETER
(`def Inv (f : St → Result Bool) (s : St) : Result Bool := f s`) certifies whatever `f` is later
instantiated to, because the walk sees only the parameter. Nothing generated looks like this, but do
not write it by hand and read the silence as a guarantee.

**A Prop-valued invariant is out of scope here** — `def Solvent … : Prop` is
`assumed_postcondition`'s business, and this check stays silent on it.

## `schema_conformance`

The other two checks hunt for bad shapes, which is why their *silence* is ambiguous: "clean, or nothing I
recognise". This one inverts that. FORMALISE annotates each theorem with the schema it was written
to, and this verifies the annotation:

```lean
@[lusterna_invariant]   -- Inv pre = ok true → target execution → Inv post = ok true
@[lusterna_hoare]       -- Pre inputs = ok true → target execution → Post … = ok true
@[lusterna_freeform "pure arithmetic helper"]   -- a supporting lemma, declared out of scope
```

**A theorem that fails the schema its own author declared is a fact, not a judgement.** Findings
name the **rule** that broke, so the fix is mechanical:

| rule | what it means |
| --- | --- |
| `no_schema_declared` | every spec theorem needs exactly one schema; a helper declares `freeform` |
| `multiple_schemas_declared` | two annotations — being checked against the laxer of them is not a result |
| `execution_not_unique` | a Hoare triple has exactly one execution of a declared target |
| `execution_output_not_fresh` | the execution binder pins its own output (`assumed_postcondition`'s rule, reused) |
| `hypothesis_not_a_precondition` | a hypothesis that is not `Pre <root inputs> = ok true` — state it inside `Pre` |
| `precondition_mentions_output` | the precondition speaks about the post-state; the finding names *which* argument |
| `conclusion_not_a_postcondition` | the conclusion must be `Post … = ok true` |
| `predicate_not_failure_strict` | `Inv`/`Pre`/`Post` is not in `invariant_not_strict`'s failure-strict fragment |
| `postcondition_ignores_output` | conforms otherwise, but says nothing about what the call produced |
| `invariant_shape` | declared `invariant` without the preservation shape |
| `extraneous_hypothesis` | an `invariant` theorem carrying anything besides the invariant and the execution |
| `private_declaration` | a private theorem's annotation may not survive module export |

**Why default-reject is safe here and nowhere else.** Applied to arbitrary theorems this rule would
flag every honest use of `→`, `∨`, `¬`. Applied to a theorem whose author declared its schema, it is
exact — the annotation is what buys the strictness.

**Two rules worth understanding before you read a finding.**

`postcondition_ignores_output` is what stops a conforming theorem from being vacuous. The data
binders that name outputs in advance — `(hok : deposit … = ok (.Ok eff, vfinal))` — are not
propositions, so the precondition rule never sees them, and without this nothing would force `Post`
to mention `eff` or `vfinal`. An output of a type that carries no information (`Unit`, any
single-constructor-no-field type) is exempt: there is nothing to say about it.

`hypothesis_not_a_precondition` is where `assumed_postcondition`'s "bound on an intermediate" false
positive goes.
It is not a finding to adjudicate but a conformance failure with a prescribed fix: move
the bound into `Pre`, where it becomes a named, inspectable object rather than a loose hypothesis.
Injectivity and the other relational properties are not `hoare` at all; declare them `freeform`.

**What this does NOT check.** The annotation is written by the same agent whose work is checked, so
this verifies **form, not fitness**. A Hoare triple annotated `invariant` can conform perfectly and
still be mislabelled, and a `freeform` justification can be a dodge. Whether the declared schema is
the *right* one, and whether the invariant is the right invariant, is yours.

## The principle behind the checks, if you want it in one line

A fact about a function's output should enter the proof through that function's own execution, not
through an independent hypothesis that happens to mention the same variable. `assumed_postcondition`
asks whether the call's *outputs* were smuggled in from somewhere other than the call;
`invariant_not_strict` asks whether the invariant a preservation theorem is *about* can be satisfied
by a state nobody could describe; `schema_conformance` asks whether the theorem is the kind of
statement its author said it was. None is a substitute for reading
the theorem — they're cheap enough to run on everything and precise enough to
be worth reading when they fire.

## `assumed_postcondition` and `invariant_not_strict` on the same `= ok true` shape

`assumed_postcondition` names `checkInvariant s' = ok true` an anti-pattern, and
`invariant_not_strict` asks you to write exactly that. Both are right, because the POSITION is what
differs. A Bool invariant on the **post-state, handed to the theorem as a hypothesis** carries the
whole conclusion — that is `assumed_postcondition`'s finding, and it stands. The invariant schema
instead requires `Inv <post-state> = ok true` to be the **conclusion** (which
`assumed_postcondition` exempts), with only `Inv <pre-state> = ok true` among the hypotheses — clean
arguments, nothing tainted, so there is no complaint about it. Write it the other way round and
`assumed_postcondition` will tell you.
