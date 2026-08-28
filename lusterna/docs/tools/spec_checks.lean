/-
Lusterna mechanical spec checks — a TOOL, not a gate.

Three heuristics over a theorem's ELABORATED type (never its proof — these run on FORMALISE's
`sorry`-bodied statements just as well as on PROVE's finished ones). Each is named after the
`"check"` field its findings carry; there is deliberately no numbering, so a reference cannot go
stale when the set changes:

  • `checkAssumedPostcondition` (`assumed_postcondition`) — an INFORMATION-FLOW rule, and one of
    the checks that needs telling which functions are the TARGETS. Target patterns are matched by dotted SUFFIX so a bare
    `deposit` resolves against `crate.vault.deposit`, which also means a helper named `….deposit`
    can answer to it; pass fully-qualified names where the campaign has them. A fact about the
    subject's output or post-state may enter only through the subject's own execution relation: `f x s = ok (y, s')` taints `y`/`s'`,
    taint closes over chained calls and aliases (`z = s'.var`), and any OTHER hypothesis mentioning
    a tainted variable is reported — including a further fallible call on the post-state, unless it
    is a declared CONTINUATION. Catches a theorem that satisfies every local reading — its inputs
    reach a genuine precondition, its execution is real — but which also hands itself its own
    conclusion.

  • `checkInvariantTotality` — recognises the INVARIANT-PRESERVATION theorem
    (`Inv s = ok true` → target execution → `Inv s' = ok true`, targets matched as for
    `checkAssumedPostcondition`) and asks whether `Inv` is FAILURE-STRICT: does a failing
    measurement inside it make the invariant FALSE? The certified form is a `Result Bool` `def`
    whose every fallible call is BOUND — sound because `bind (fail e) k = fail e` and `ok true` is
    not `fail`, so no failure can reach `ok true`. One rule does the work: a `Result` may be bound
    or returned, NEVER PASSED, which is why it stays closed instead of blocklisting Aeneas's helper
    set. Reads equation lemmas rather than `dv.value`, so a RECURSIVE invariant (a fold over
    accounts — the realistic case) certifies instead of being unreadable.

THIS FILE RUNS NOTHING AUTOMATICALLY. The harness copies it into every crate's own lean tree, once,
at `lean/<Crate>/LusternaChecks.lean` (see `lean._write_lint_tool`) — harness-owned, like the
lakefile: do not edit or delete it. An agent (SPEC-JUDGE, primarily) invokes it ITSELF over Bash
when judging a spec module, and DECIDES what a finding means — a flagged hypothesis is a prompt to
read the theorem more closely, not an automatic defect. See docs/skills/mechanical-checks.md for
the exact invocation and worked examples of what each check does and does not catch (chained calls
and triples are clean everywhere; a hypothesis that merely restates its own defining equation and a
hypothesis constraining the post-state are not — while injectivity is a known false positive of
`checkAssumedPostcondition`, which `schema_conformance` supersedes by having such a theorem declare
`@[lusterna_freeform]` instead).
-/
import Aeneas
-- `LUSTERNA_CRATE` is substituted with the crate's lib name by `lean._write_lint_tool`. The
-- schemas file is a SUBMODULE of the crate lib (the lakefile globs `.andSubmodules <lib>` and
-- nothing else), so its module path is necessarily crate-specific and cannot be hardcoded in
-- this template. `_write_lakefile` interpolates the same lib name for the same reason.
import LUSTERNA_CRATE.LusternaSchemas
open Lean Elab Meta

namespace Lusterna.Checks

private def jesc (s : String) : String :=
  (((s.replace "\\" "\\\\").replace "\"" "\\\"").replace "\n" " ").replace "\t" " "

private def emit (s : String) : MetaM Unit := IO.println ("LUSTERNA_CHECK " ++ s)

private def lastComp (n : Name) : String := match n with | .str _ s => s | _ => ""

private def okCtorFields? (iname : Name) (e : Expr) : MetaM (Option (Array Expr)) := do
  let .const cname _ := e.getAppFn | return none
  unless lastComp cname == "ok" do return none
  let .ctorInfo cv ← getConstInfo cname | return none
  unless cv.induct == iname do return none
  let args := e.getAppArgs
  if args.size < cv.numParams + cv.numFields then return none
  return some (args.extract cv.numParams (cv.numParams + cv.numFields))

/-- `some (fn, args, outFields)` iff `e` is `f a1..an = ok y` (either orientation). -/
private def definingEq? (e : Expr) : MetaM (Option (Expr × Array Expr × Array Expr)) := do
  let some (ty, lhs, rhs) := e.eq? | return none
  let .const iname _ := (← whnf ty).getAppFn | return none
  let .inductInfo _ ← getConstInfo iname | return none
  if let some fs ← okCtorFields? iname rhs then return some (lhs.getAppFn, lhs.getAppArgs, fs)
  if let some fs ← okCtorFields? iname lhs then return some (rhs.getAppFn, rhs.getAppArgs, fs)
  return none

private partial def outputLeaves (e : Expr) : MetaM (Array FVarId) := do
  match e with
  | .fvar id => return #[id]
  -- Strip metadata, exactly as `canonicalOutput` does. If only one of them did, a wrapped output
  -- could be accepted as canonical while yielding NO leaves, so nothing would be seeded as tainted
  -- and every assumption about that output would go unreported.
  | .mdata _ b => outputLeaves b
  | _ =>
    let .const cn _ := e.getAppFn | return #[]
    let .ctorInfo cv ← getConstInfo cn | return #[]
    let args := e.getAppArgs
    if args.size < cv.numParams + cv.numFields then return #[]
    let mut out : Array FVarId := #[]
    for a in args.extract cv.numParams (cv.numParams + cv.numFields) do
      out := out ++ (← outputLeaves a)
    return out

private def ppTrunc (e : Expr) : MetaM String := do
  let s := (← ppExpr e).pretty
  let cs := s.toList
  return if cs.length > 220 then String.ofList (cs.take 220) ++ " ..." else s

/-- Aeneas Hoare triple: `spec e (fun y => ...)`/`dspec`. Its output is lambda-bound, never a
telescope fvar, so it is not an execution relation with a reachability-checkable argument. Used by
`checkAssumedPostcondition` to see that a subject IS present in a statement that binds no output. -/
private def isTripleLike (e : Expr) : Bool :=
  match e.getAppFn with
  | .const n _ => n == ``Aeneas.Std.WP.spec || n == ``Aeneas.Std.WP.dspec
  | _ => false

/-- Trusted by SOURCE MODULE, never by declaration name. A name-prefix blocklist is not a trust
boundary for a tool reading generated code — a spec module can write `namespace Aeneas … end Aeneas`
around anything and be skipped for free. The module a declaration was compiled into cannot be picked
that way: the harness's lakefile builds `lean/<Crate>/**` and nothing else, so generated code always
lands in a `<Crate>.…` module however its namespaces are spelled.

Still a blocklist, and for the original reason: an allowlist would silently skip any predicate that
is not namespaced the way the checker expected, and "clean" must mean "checked". A declaration with
NO module — elaborated in the current file, e.g. an ad hoc driver — is treated as project-local and
inspected. -/
private def trustedModulePrefixes : Array Name :=
  #[`Mathlib, `Lean, `Init, `Std, `Batteries, `Aeneas, `Qq, `Aesop, `Plausible, `ImportGraph,
    `LeanSearchClient, `Cli]

private def isTrustedDecl (n : Name) : MetaM Bool := do
  let some mod := (← getEnv).getModuleFor? n | return false
  return trustedModulePrefixes.any (fun p => p.isPrefixOf mod)

/-- What the predicate walk found at a constant. `notPredicate` really means "nothing to look at
here"; `uninspectable` means the opposite and must be reported, or an opaque predicate would be
indistinguishable from an irrelevant one. -/
private inductive PredBody where
  | body (value : Expr)
  | constructors (ctors : Array Name) (numParams : Nat)
  | notPredicate
  | uninspectable (kind : String)

private def predicateBody? (n : Name) : MetaM PredBody := do
  if ← isTrustedDecl n then return .notPredicate
  let info ← getConstInfo n
  let isPropValued ← forallTelescope info.type fun _ body => do
    match ← whnf body with
    | .sort .zero => return true
    | _ => return false
  unless isPropValued do return .notPredicate
  match info with
  | .defnInfo dv   => return .body dv.value
  | .axiomInfo _   => return .uninspectable "axiom"
  | .opaqueInfo _  => return .uninspectable "opaque"
  -- A Prop-valued inductive has no BODY, but its CONSTRUCTOR FIELDS are propositions and a guard
  -- sits in one perfectly well — `structure Solvent (s : State) : Prop where proof : ∀ v,
  -- total_supply s = ok v → Min ≤ v` is the same fail-open invariant, and a structure is the natural
  -- way to write one. Only the inductive's own name appears in a theorem's used constants, so the
  -- constructors have to be reached from here.
  | .inductInfo iv => return .constructors iv.ctors.toArray iv.numParams
  | _              => return .notPredicate

/-- Backstop only. The walk terminates on its own — `seen` is monotone over a finite environment —
so reaching this means something pathological, and it is reported rather than absorbed. -/
private partial def conjuncts (e : Expr) : Array Expr :=
  match e.getAppFnArgs with
  | (``And, #[a, b]) => conjuncts a ++ conjuncts b
  | _ => #[e]

private def isPropSafe (e : Expr) : MetaM Bool := do
  try Meta.isProp e catch _ => return false

/-- `c` ends in `t` at a component boundary. Lets the caller pass either the bare function name or
the fully-qualified one, and maps INFER's `a::b::c` target patterns onto `a.b.c`. Written on
strings rather than a `Name` API so Lean's `_private.<Module>.0.<Name>` mangling still matches. -/
private def nameHasSuffix (c t : Name) : Bool :=
  let cs := toString c
  let ts := toString t
  cs == ts || cs.endsWith ("." ++ ts)

private def isSubjectCall (targets : Array Name) (fn : Expr) : Bool :=
  match fn with
  | .const c _ => targets.any (nameHasSuffix c)
  | _ => false

/-- `some z` iff `e` names tainted data: `z = <expr mentioning something tainted>` with `z` a bare
variable that is not tainted yet. This is the taint's transitive channel — `(hz : z = s'.var)` is
what makes a later `(hz2 : z > 0)` reachable.

PROPAGATION ONLY. The alias is NOT exempt from reporting: an equality is a proposition the caller
supplied, not a `let`, and it can be the whole cheat with nothing downstream to catch — `(h : s' = s)`
closes a preservation goal by itself, and `(h1 : z = s'.var) (h2 : z = s.var)` rebuilds one across
two hypotheses that each look innocent. -/
private def definitionAlias? (tainted : Array FVarId) (e : Expr) : MetaM (Option FVarId) := do
  let some (_, lhs, rhs) := e.eq? | return none
  let touches (x : Expr) : Bool := tainted.any (fun t => x.containsFVar t)
  if let .fvar id := lhs then
    if !tainted.contains id && touches rhs then return some id
  if let .fvar id := rhs then
    if !tainted.contains id && touches lhs then return some id
  return none

/-- The hypothesis IS the goal (or one of its conjuncts) — `exact hvar` closes the theorem. -/
private def matchesConclusion (concl bty : Expr) : MetaM Bool := do
  for c in conjuncts concl do
    if ← withNewMCtxDepth (isDefEq bty c) then return true
  return false

/-- Multi-constructor wrappers that do NOT count as pinning a result. Rust's own `Result` is the
only entry, and it earns it: Aeneas translates a fallible Rust function to
`Result (core.result.Result T E × State)`, so essentially every theorem about one is stated on the
`Ok` branch, and flagging all of them would be noise a reader learns to skip. `Option.some`,
`Sum.inl`, and a crate's own success variant are deliberately NOT here — choosing a branch there is
a claim about the result, and belongs in the conclusion. -/
private def neutralWrappers : Array Name := #[`core.result.Result.Ok]

/-- A wrapper is neutral only if it BOTH matches the allowlist by dotted suffix AND was compiled in
a trusted module. Aeneas emits the Rust result as `Aeneas.Std.core.result.Result.Ok`, so exact
equality against the bare name never fires and every theorem about a fallible Rust function draws a
spurious "output pins a value"; a bare suffix test would instead let any module define a constructor
ending in the same components and inherit the exception. Provenance settles it — generated code
cannot place a declaration in `Aeneas.Std.Core.Result`. -/
private def isNeutralWrapper (cn : Name) : MetaM Bool := do
  if !neutralWrappers.any (nameHasSuffix cn) then return false
  isTrustedDecl cn

/-- Canonical execution output: nothing but choice-free constructor applications over bare
variables. `ok (y, s')` passes. `ok (⟨true⟩, s')` does not — the `Bool` pins the result — nor does
`ok (y, { s with var := 10 })`, which is `State.mk 10 s.n s.total`, nor `ok (some y)`, which has
already settled that the call returns `some`. `Prod.mk`, `Unit.unit` and any single-constructor
structure are neutral; see `neutralWrappers` for the one exception. -/
private partial def canonicalOutput (e : Expr) (fuel : Nat := 8) : MetaM Bool := do
  match e with
  | .fvar _ => return true
  | .mdata _ b => canonicalOutput b fuel
  | _ =>
    let .const cn _ := e.getAppFn | return false
    let .ctorInfo cv ← getConstInfo cn | do
      -- NOT LITERALLY a constructor is not yet an answer. `Unit := PUnit`, so the `()` that Aeneas
      -- puts in every `ok (core.result.Result.Ok (), s')` is `Unit.unit` — a DEFINITION that
      -- reduces to `PUnit.unit`. Demanding a `.ctorInfo` head rejected it, which made the unit
      -- payload of every fallible Rust call read as a pinned output. One `whnf` and retry; a head
      -- that still is not a constructor genuinely pins something.
      if fuel == 0 then return false
      let e' ← whnf e
      if e' == e then return false
      return ← canonicalOutput e' (fuel - 1)
    let .inductInfo iv ← getConstInfo cv.induct | return false
    -- CHOOSING A CONSTRUCTOR is itself information, whether or not it carries fields: `ok (some y)`
    -- has already settled that the call returns `some` rather than `none`. So a wrapper is neutral
    -- only when its type offers no choice — one constructor — or when it is allowlisted above.
    -- Fields are then checked recursively, which is what rejects `Out.mk true` and
    -- `State.mk 10 s.n s.total`.
    unless iv.ctors.length == 1 || (← isNeutralWrapper cn) do return false
    if cv.numFields == 0 then return true
    let args := e.getAppArgs
    if args.size < cv.numParams + cv.numFields then return false
    for a in args.extract cv.numParams (cv.numParams + cv.numFields) do
      unless ← canonicalOutput a fuel do return false
    return true

private structure ExecInfo where
  idx     : Nat                -- which binder
  fn      : Expr               -- the function being run
  args    : Array Expr
  outputs : Array Expr         -- the `ok` constructor's fields, as written
  leaves  : Array FVarId       -- the variables inside those fields
  deriving Inhabited

/-- `assumed_postcondition`, continued — the CONCLUSION is exempt from the binder rule, which makes it the obvious
place to move the cheat to. Nothing in the theorem's own telescope constrains the post-state here:

    def WeakPreservation (s s' : State) : Prop := s'.var = s.var → s'.var = s.var
    theorem t … (hexec : f x s = ok (y, s')) : WeakPreservation s s'   -- `intro h; exact h`

and the same trick works inside a TRIPLE, where the post-state is lambda-bound rather than a
telescope variable:

    theorem t … : f x s ⦃ (y, s') => s'.var = s.var → s'.var = s.var ⦄

so `analyseConclusion` walks a conclusion COMPOSITIONALLY and applies the binder rule wherever it
lands. Narrower is not enough: keying on the conclusion's ROOT constant being a project-local `def`
lets `WeakPreservation s s' ∧ True` past (the root is `And`, a trusted inductive), along with
`True ∨ …`, `∃ z, …` and any trusted wrapper — and a fixed unfolding depth lets a chain of one-line
wrappers hide behind it.

WHAT IT TRAVERSES: `∀`/`→` chains (reporting each binder that depends on tainted data), every
argument of an application (which covers `∧`, `∨`, `↔` and arbitrary wrappers uniformly), lambdas
(which covers `∃` and a triple's postcondition), `let`, project-local definitions unfolded at the
use site, and the CONSTRUCTOR FIELDS of a project-local Prop-valued inductive — a
`structure WeakPreservation … : Prop where h : s'.var = s.var → s'.var = s.var` keeps its proposition
there, not in a body. `seen` and `fuel` bound it; running out sets `complete := false`, which the
caller reports rather than absorbing.

TWO REASONS, because silently exempting was not defensible. A binder whose type mentions tainted
data is an ASSUMED ANTECEDENT unless it looks like quantifier structure — non-propositional (`i :
Fin s'.len` is a quantifier, not a hypothesis), or an atomic guard on a variable the predicate itself
introduced (`∀ i, i < s'.n → …` bounds `i`). Those are reported too, at lower signal, because the
carve-out is bypassable in both directions: `∀ i, (i = i → P s') → P s'` mentions `i` and is still a
free assumption, so "mentions a local binder" cannot be a licence on its own. -/
private structure ConclFinding where
  hypothesis    : String
  vars          : Array String
  informational : Bool

private def taintedNames (tainted : Array FVarId) (t : Expr) : MetaM (Array String) := do
  let hit := tainted.filter (fun v => t.containsFVar v)
  hit.mapM fun fv => return toString (← fv.getDecl).userName

mutual
  /-- The binder rule, applied to one proposition in conclusion position. -/
  private partial def analyseConclProp (tainted : Array FVarId) (e : Expr) (seen : NameSet)
      (fuel : Nat) : MetaM (Array ConclFinding × Bool × NameSet) := do
    forallTelescope e fun ys body => do
      let mut out : Array ConclFinding := #[]
      for y in ys do
        let t ← instantiateMVars (← inferType y)
        let names ← taintedNames tainted t
        if names.isEmpty then continue
        let isProp ← isPropSafe t
        let boundHere := ys.any (fun z => t.containsFVar z.fvarId!)
        let atomic := match t with | .forallE .. => false | _ => true
        out := out.push { hypothesis := ← ppTrunc t, vars := names,
                          informational := !isProp || (boundHere && atomic) }
      let (more, ok, seen) ← analyseConclusion tainted body seen fuel
      return (out ++ more, ok, seen)

  /-- Structural + definitional descent. Everything the conclusion can hide behind. -/
  private partial def analyseConclusion (tainted : Array FVarId) (e : Expr) (seen : NameSet)
      (fuel : Nat) : MetaM (Array ConclFinding × Bool × NameSet) := do
    if fuel == 0 then return (#[], false, seen)
    -- An EMPTY taint set is not a reason to stop: a triple's postcondition binds the post-state in
    -- its own lambda, and this walk is what seeds it. Only prune when there is taint to look for.
    unless tainted.isEmpty || tainted.any (fun t => e.containsFVar t) do return (#[], true, seen)
    let mut out : Array ConclFinding := #[]
    let mut ok := true
    let mut seen := seen
    let recur (tainted : Array FVarId) (x : Expr) (seen : NameSet)
        : MetaM (Array ConclFinding × Bool × NameSet) := analyseConclusion tainted x seen (fuel - 1)
    match e with
    | .forallE .. =>
      let (a, b, sn) ← analyseConclProp tainted e seen (fuel - 1)
      return (a, b, sn)
    | .lam n t b bi =>
      -- Opening a lambda SEEDS its binders: a triple's postcondition binds the post-state here.
      let (a, k, sn) ← withLocalDecl n bi t fun z =>
        recur (tainted.push z.fvarId!) (b.instantiate1 z) seen
      return (a, k, sn)
    | .letE n t v b _ =>
      let (a, k, sn) ← withLetDecl n t v fun z => recur tainted (b.instantiate1 z) seen
      return (a, k, sn)
    | .mdata _ b  => return ← recur tainted b seen
    | .proj _ _ b => return ← recur tainted b seen
    | _ =>
      -- An application: every argument, then the head's own definition or constructors.
      for a in e.getAppArgs do
        let (fa, ka, sn) ← recur tainted a seen
        out := out ++ fa; ok := ok && ka; seen := sn
      let .const cn _ := e.getAppFn | return (out, ok, seen)
      if seen.contains cn then return (out, ok, seen)
      seen := seen.insert cn
      match ← predicateBody? cn with
      | .body _ =>
        match ← Meta.unfoldDefinition? e with
        | some u =>
          let (fu, ku, sn) ← recur tainted u seen
          return (out ++ fu, ok && ku, sn)
        | none => return (out, ok, seen)
      | .constructors ctors numParams =>
        -- INSTANTIATED at the use site. A constructor's type is generic (`∀ s s', … → WeakStruct
        -- s s'`), so scanning it as declared mentions none of the theorem's variables and finds
        -- nothing; applying the occurrence's own arguments is what makes the field mention `s'`.
        let args := e.getAppArgs
        for c in ctors do
          let ct ← instantiateForall (← getConstInfo c).type (args.extract 0 (min numParams args.size))
          let (fc, kc, sn) ← recur tainted ct seen
          out := out ++ fc; ok := ok && kc; seen := sn
        return (out, ok, seen)
      | .uninspectable _ => return (out, false, seen)
      | .notPredicate => return (out, ok, seen)
end

/-- A target run in TRIPLE form, `f args ⦃ y => φ y ⦄`. Its outputs are lambda-bound rather than
telescope variables, so there are no subject execution binders — but a subject IS present, and
reporting "no target execution found" would be wrong. The postcondition itself needs no special
extraction: `analyseConclusion` opens the `uncurry`/`uncurry'` wrappers and the postcondition lambda
as ordinary applications and lambdas, seeding each bound output as tainted on the way through.

The COMPUTATION argument only. `spec {α} (x : Result α) (p : Post α)` puts the program
second-to-last and the postcondition last, so scanning the whole application would read
`g x ⦃ y => SomeRelation (f y) ⦄` as an execution of `f` when what actually runs is `g`. -/
private partial def tripleOverTarget (targets : Array Name) (e : Expr) : Bool :=
  if isTripleLike e
     && (let sargs := e.getAppArgs
         sargs.size ≥ 2
         && sargs[sargs.size - 2]!.getUsedConstants.any (fun c => targets.any (nameHasSuffix c)))
  then true
  else match e with
    | .app f a          => tripleOverTarget targets f || tripleOverTarget targets a
    | .forallE _ t b _  => tripleOverTarget targets t || tripleOverTarget targets b
    | .lam _ t b _      => tripleOverTarget targets t || tripleOverTarget targets b
    | .letE _ t v b _   => tripleOverTarget targets t || tripleOverTarget targets v
                             || tripleOverTarget targets b
    | .mdata _ b        => tripleOverTarget targets b
    | .proj _ _ b       => tripleOverTarget targets b
    | _                 => false

/-- `assumed_postcondition` — an information-flow rule rather than a shape rule.

For the canonical `f : Input → State → Result (Output × State)`, the verification shape is
`∀ x s y s', Pre x s → f x s = ok (y, s') → Post x s y s'`. Adding `(hvar : s'.var = s.var)` to a
theorem CONCLUDING `s'.var = s.var` leaves it logically valid, provable by `exact hvar`, and
worthless: it no longer says anything about `f`. Nothing local distinguishes the two forms — the
execution is real, the precondition is genuine, every hypothesis type-checks. What separates them is
where the fact about `s'` CAME FROM, which is why this check tracks provenance rather than shape.

The invariant: let T be the values freshly produced by an execution of a named TARGET. No binder
may constrain anything in T's transitive dependency closure, except the target execution itself and
an explicitly authorized continuation.

  • FRESH OUTPUTS — every execution the statement is allowed to contain, subject or declared
    continuation, must bind fresh unconstrained variables. `f x s = ok (y, s)` is not a harmless
    in-place convention: it asserts the post-state IS the pre-state. Neither is `ok (⟨true⟩, s')`,
    `ok (y, { s with var := 10 })`, or `ok (some y)` — the result is pinned inside the execution
    binder instead of claimed in the conclusion. Checked PER EXECUTION, against that call's own
    arguments: a global input set would see a chained target's first outputs listed as the second
    call's inputs and report both as non-fresh.
  • ONE OWNER PER OUTPUT — a value may be consumed by any number of later calls, and produced by
    exactly one. Freshness is local to a single binder, so it cannot see two executions producing
    the SAME variable — and that is a way to write a relational premise without writing one:
    `(h1 : f x = ok y) (h2 : f z = ok y)` is injectivity's `a = b` with the equality hidden in the
    binder names, and every local test passes. A continuation's output must additionally not be a
    root input: `(hg : g y = ok x)` constrains the continuation instead of naming its result.
  • SEED — the output leaves of every subject execution that passed freshness. A value stays
    target-derived even once a later call consumes it, so ROOT INPUTS are what is left after
    subtracting those outputs from the subject arguments — computed in that order, or chaining
    would launder the very values being tracked. Outputs of a subject that FAILED freshness are
    excluded: they are not fresh, and treating them as new data would taint an aliased pre-state
    and move the finding onto that pre-state's innocent precondition.
  • CLOSURE — an execution handed a tainted argument taints its own outputs, authorized or not (an
    unauthorized one is reported AND propagates, so a constraint further downstream is still
    caught); an equality `z = <tainted>` taints `z`. Run to a fixpoint over a finite variable set,
    so the result never depends on binder order.
  • VIOLATION — any binder whose type mentions a tainted variable, the CONCLUSION aside. An
    execution binder is exempt only if it consumes nothing tainted and is either the subject or
    free of taint entirely, or if it consumes tainted data and its function is a declared
    CONTINUATION. The test is on the whole binder type and never on the arguments alone:
    `(hg : g z = ok y)` takes only clean arguments and still pins `y`, the subject's own output.
    And being a TARGET grants provenance, not the right to consume it — `f y s' = ok (z, s'')`
    assumes a second execution of `f` succeeds on the post-state, exactly the assumption
    `rebalance s' = ok …` would be, so an iterative theorem passes its target in `continuations`.

Three exemptions that look reasonable and are not — each one is a bypass:

  • "execution-shaped is benign". `g args = ok out` over tainted data is not a neutral observation:
    it asserts `g` SUCCEEDS there, which restricts the theorem just as a success-guarded antecedent
    would, and the equation can carry the whole postcondition by itself.
    `(hcheat : checkVarPreserved s s' = ok ())` proves `s'.var = s.var` by
    `simp [checkVarPreserved] at hcheat; exact hcheat`, and `checkInvariant s' = ok true` does the
    same with a `Bool`. Neither runs anything under test.
  • "an alias only names a value". `(hz : z = s'.var)` is a proposition the caller supplied, not a
    `let`. It propagates taint AND is reported: `(h : s' = s)` alone closes a preservation goal,
    and `(h1 : z = s'.var) (h2 : z = s.var)` reconstructs one across two of them.
  • "only `Prop` binders can carry an assumption". `(h : { u : Unit // s'.var = s.var })` is data,
    not a proof, and `exact h.property` closes the goal. Every binder's type is checked.

The cost of that strictness is that a post-state measurement named in a hypothesis
(`(hafter : total s' = ok t)`, or `… : ∀ m, total s' = ok m → k ≤ m`) is reported. That is the right
answer by this project's own doctrine — a measurement's failure must not excuse the property — and
the fix is the total form: the `⦃ ⦄` triple, or `∃ t, total s' = ok t ∧ φ t` — and in a schema-
conforming spec, a `Result Bool` `Pre`/`Post` that binds the measurement, which
`certifiedStrict` verifies directly.

CONTINUATIONS are opt-in, and default to none: a theorem about `deposit` composed with `rebalance`
passes `#[`crate.rebalance]` and gets the chaining exemption for exactly that function, while every
other fallible call on the post-state stays a finding. A target is NOT implicitly its own
continuation — an iterative theorem must name it in both lists.

TARGETS ARE REQUIRED, not inferred. Without them a precondition stated through a pre-state
measurement (`(hm : total_supply s = ok t) (hk : k ≤ t)`) reads as a violation, which is a common
enough shape to drown the check. SPEC-JUDGE has the campaign's `target_patterns` in
`infer/campaigns/<Campaign>.json`. An empty `targets`, or targets that match no execution
hypothesis here, emits `LUSTERNA_CHECK_SKIPPED` rather than returning silently — this check is
opt-in per theorem, so "clean" must not be indistinguishable from "never ran".

A target appearing ONLY in `⦃ ⦄` form is ANALYSED, not waved through. The post-state being
lambda-bound was once documented as making the defect structurally impossible; it is not —
`f x s ⦃ (y, s') => s'.var = s.var → s'.var = s.var ⦄` assumes exactly what it claims, and
`analyseConclusion` seeds the postcondition's own lambda binders and applies the binder rule inside
it.

RETURN VALUE. Each check returns a finding COUNT, and completeness is carried separately on stdout:
a `LUSTERNA_CHECK_SKIPPED` line means part of the analysis did not happen, so zero findings without
one means clean and zero with one does not. The count is not a completeness signal and is discarded
by every caller — the printed protocol is the interface.

ACCEPTED FALSE POSITIVE: a relational antecedent — injectivity's `hab : a = b`, determinism,
cancellation — is a hypothesis about outputs by construction, and no shape distinguishes it from a
cheat. Report those theorems clean after reading them. The clean long-term answer is a trusted
schema (`InjectiveOnSuccess`) that authorizes the relational premise explicitly, rather than
weakening this rule to accommodate it. -/
def checkAssumedPostcondition (qn : Name) (targets : Array Name)
    (continuations : Array Name := #[]) : MetaM Nat := do
  let ci ← getConstInfo qn
  let ty ← instantiateMVars ci.type
  let tlist := String.intercalate ", " (targets.toList.map (fun t => "\"" ++ toString t ++ "\""))
  if targets.isEmpty then
    IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"reason\": \"no targets given\"}"
    return 0
  forallTelescope ty fun xs concl => do
    let mut btys : Array Expr := #[]
    for x in xs do btys := btys.push (← instantiateMVars (← inferType x))
    let mut isProof : Array Bool := #[]
    for t in btys do isProof := isProof.push (← Meta.isProp t)
    let mut execIdx : Array Nat := #[]
    let mut execs : Array ExecInfo := #[]
    for i in [0:btys.size] do
      if let some (fn, args, outFields) ← definingEq? btys[i]! then
        let mut leaves : Array FVarId := #[]
        for fld in outFields do
          for l in ← outputLeaves fld do
            -- A proof-typed output leaf (a `Subtype`'s witness) carries no information: a proof
            -- term says nothing about which value was produced.
            unless ← Meta.isProp (← l.getType) do leaves := leaves.push l
        execIdx := execIdx.push i
        execs := execs.push { idx := i, fn := fn, args := args, outputs := outFields,
                              leaves := leaves }
    let subjects := execs.filter (fun e => isSubjectCall targets e.fn)
    if subjects.isEmpty && !tripleOverTarget targets ty then
      IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"reason\": \"no execution hypothesis for any named target\", \"targets\": [{tlist}]}"
      return 0
    let fnName (e : Expr) : String := match e with | .const c _ => toString c | _ => "?"
    -- `subjects` can be empty when the target appears only in `⦃ ⦄` form, so this must not index.
    let subjName := match subjects[0]? with
      | some e => fnName e.fn
      | none   => String.intercalate ", " (targets.toList.map toString)
    -- `fn` names the call a finding is actually about, not `subjects[0]` — with several targets in
    -- play, attributing every finding to the first one is misleading.
    let reportFn (fname : String) (bty : Expr) (vars : Array FVarId) (reason : String)
        : MetaM Unit := do
      let names ← vars.mapM fun fv => return toString (← fv.getDecl).userName
      let quoted := String.intercalate ", " (names.toList.map (fun s => "\"" ++ s ++ "\""))
      let isConcl ← matchesConclusion concl bty
      emit s!"\{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"fn\": \"{fname}\", \"tainted\": [{quoted}], \"hypothesis\": \"{jesc (← ppTrunc bty)}\", \"reason\": \"{reason}\", \"is_conclusion\": {isConcl}}"
    let report (e : ExecInfo) := reportFn (fnName e.fn)
    -- FRESHNESS, per execution: does THIS call's output reuse one of ITS OWN arguments, repeat a
    -- variable, or pin a value? A global input set cannot answer that — under one, a chained target
    -- (`f x s = ok (y, s')` then `f y s' = ok (z, s'')`) would see the first call's outputs listed
    -- as inputs and report both calls as non-fresh.
    let mut n := 0
    let mut freshOk : Array Nat := #[]
    let checkFresh (e : ExecInfo) : MetaM Bool := do
      let ownArgs := e.leaves.filter (fun l => e.args.any (fun a => a.containsFVar l))
      let dup := e.leaves.filter (fun l => (e.leaves.filter (· == l)).size > 1)
      let mut canonical := true
      for o in e.outputs do
        unless ← canonicalOutput o do canonical := false
      if !ownArgs.isEmpty then
        report e btys[e.idx]! ownArgs "output aliases an argument of the same call"
      else if !dup.isEmpty then
        report e btys[e.idx]! dup "output variable is bound twice"
      else if !canonical then
        report e btys[e.idx]! #[] "output pins a value or a constructor branch"
      else return true
      return false
    for e in subjects do
      if ← checkFresh e then freshOk := freshOk.push e.idx else n := n + 1
    -- TARGET-DERIVED vs ROOT INPUT. A value produced by a subject stays target-derived even when a
    -- later call consumes it, so the output set is computed FIRST and subtracted from the arguments
    -- — otherwise chaining would launder the very values the check exists to track. Outputs of a
    -- subject that failed freshness are excluded: they are not fresh, so treating them as new
    -- target-derived data would taint an aliased pre-state and move the finding onto that
    -- pre-state's own innocent precondition, away from the execution binder already reported.
    let mut tainted : Array FVarId := #[]
    for e in subjects do
      unless freshOk.contains e.idx do continue
      for l in e.leaves do
        unless tainted.contains l do tainted := tainted.push l
    let mut rootInputs : Array FVarId := #[]
    for e in subjects do
      for j in [0:xs.size] do
        let id := xs[j]!.fvarId!
        if e.args.any (fun a => a.containsFVar id) then
          unless tainted.contains id || rootInputs.contains id do rootInputs := rootInputs.push id
    -- A fixpoint over a FINITE set: every productive round adds at least one of the theorem's own
    -- variables, so `xs.size + 1` rounds cannot be reached. The bound is a backstop, not a budget —
    -- hitting it is reported, because an incomplete taint set must never read as a clean result.
    let mut changed := true
    let mut rounds := 0
    while changed && rounds < xs.size + 1 do
      changed := false
      rounds := rounds + 1
      for e in execs do
        if e.args.any (fun a => tainted.any (fun t => a.containsFVar t)) then
          for l in e.leaves do
            if rootInputs.contains l then continue
            unless tainted.contains l do tainted := tainted.push l; changed := true
      for i in [0:btys.size] do
        if !isProof[i]! || execIdx.contains i then continue
        if let some id ← definitionAlias? tainted btys[i]! then
          if rootInputs.contains id then continue
          unless tainted.contains id do tainted := tainted.push id; changed := true
    if changed then
      IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"reason\": \"taint closure did not converge; findings are incomplete\"}"
    -- THE TRACE: subjects, plus the declared continuations that actually consume tainted data.
    -- Membership is what a continuation is validated as; a declared continuation called on clean
    -- arguments is an ordinary precondition and gets no freshness demands, or `rebalance pre s =
    -- ok true` would be reported purely because its function name appears on the continuation list.
    let mut trace : Array ExecInfo := subjects
    for e in execs do
      if isSubjectCall targets e.fn then continue
      if isSubjectCall continuations e.fn
         && e.args.any (fun a => tainted.any (fun t => a.containsFVar t)) then
        if ← checkFresh e then freshOk := freshOk.push e.idx else n := n + 1
        trace := trace.push e
    -- OUTPUT OWNERSHIP. Freshness is local to one call, so it cannot see two executions PRODUCING
    -- the same variable — and that is a way to write a relational premise without writing one:
    -- `(h1 : f x = ok y) (h2 : f z = ok y)` is injectivity's `a = b` with the equality encoded in
    -- the binder names, and every local test passes. A leaf may be consumed by any number of later
    -- calls; it may be produced by exactly one.
    -- Considered only among executions that passed their OWN freshness check. When a sharer already
    -- failed one — `settle y s1 = ok (z, s1)` reusing its own argument — the sharing is a
    -- consequence of that, and reporting it again (on both calls, including the innocent subject
    -- that merely produced `s1` first) would bury the root cause under its own echoes.
    for e in trace do
      unless freshOk.contains e.idx do continue
      let shared := e.leaves.filter (fun l =>
        trace.any (fun o => o.idx != e.idx && freshOk.contains o.idx && o.leaves.contains l))
      unless shared.isEmpty do
        n := n + 1
        report e btys[e.idx]! shared "output is produced by more than one execution"
      -- A continuation must not land its result back on a value that existed before any target ran:
      -- `(hg : g y = ok x)` with `x` a root input constrains the continuation instead of naming its
      -- result. Subjects are already covered by the same-call test above.
      unless isSubjectCall targets e.fn do
        let aliased := e.leaves.filter (fun l => rootInputs.contains l)
        unless aliased.isEmpty do
          n := n + 1
          report e btys[e.idx]! aliased "output aliases a root input"
    -- What an execution binder is allowed to be. The test is on the WHOLE binder type, never on the
    -- arguments alone: `(hg : g z = ok y)` takes only clean arguments yet pins `y`, the subject's
    -- own output, which is an assumed postcondition wearing a second call's clothes.
    --
    -- Being a TARGET grants provenance, not the right to consume provenance. A target run on
    -- already-tainted data is a CHAIN like any other and needs the same explicit authorization —
    -- `f y s' = ok (z, s'')` assumes a second execution of `f` succeeds on the post-state, which is
    -- exactly the assumption `rebalance s' = ok …` would be. Pass the target in `continuations` to
    -- allow it for an iterative or compositional theorem.
    let mut exemptIdx : Array Nat := #[]
    for e in execs do
      let argsTainted := e.args.any (fun a => tainted.any (fun t => a.containsFVar t))
      let touchesTaint := tainted.any (fun t => btys[e.idx]!.containsFVar t)
      let exempt :=
        if argsTainted then isSubjectCall continuations e.fn
        else isSubjectCall targets e.fn || !touchesTaint
      if exempt then exemptIdx := exemptIdx.push e.idx
    for i in [0:btys.size] do
      if exemptIdx.contains i then continue
      let bty := btys[i]!
      let hit := tainted.filter (fun t => bty.containsFVar t)
      if hit.isEmpty then continue
      n := n + 1
      -- Attribute the finding to whichever execution PRODUCED the tainted variable, so a reader is
      -- pointed at the provenance rather than at an arbitrary element of the target list.
      let owner := trace.find? (fun e => hit.any (fun v => e.leaves.contains v))
      reportFn (match owner with | some e => fnName e.fn | none => subjName)
               bty hit (if execIdx.contains i then "assumes success on tainted state"
                        else "constrains tainted variable")
    -- The conclusion, and a target's triple postcondition, analysed the same way: the binder rule
    -- applied wherever the walk lands. For the triple the taint seed comes from the postcondition's
    -- OWN lambda binders, which is why an empty `tainted` is still worth walking.
    let (found, complete, _) ← analyseConclusion tainted concl {} 64
    for f in found do
      n := n + 1
      let quoted := String.intercalate ", " (f.vars.toList.map (fun v => "\"" ++ v ++ "\""))
      let reason := if f.informational then "quantifier over the post-state in the conclusion"
                    else "assumed antecedent in the conclusion"
      emit s!"\{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"fn\": \"{subjName}\", \"tainted\": [{quoted}], \"hypothesis\": \"{jesc f.hypothesis}\", \"reason\": \"{reason}\", \"is_conclusion\": false}"
    unless complete do
      IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"assumed_postcondition\", \"theorem\": \"{qn}\", \"reason\": \"conclusion traversal did not finish; its antecedents are only partly checked\"}"
    return n

/-! ══ `invariant_not_strict` — invariant preservation, and whether the invariant is FAILURE-STRICT ══

An invariant-preservation theorem is only worth what its invariant is worth. If a measurement
inside the invariant can FAIL and the invariant still comes out true, the theorem admits
pre-states that cannot even be described — `total_supply` reverts, so "the invariant holds" for
free — and a counterexample is then an artefact of the spec rather than a bug in the code.

This check recognises that theorem shape restrictively and asks one question about the invariant:
is it FAILURE-STRICT — does any failing call inside it make the invariant FALSE?

THE INVARIANT FORM THIS CHECK IS ABOUT: `def Inv … : Result Bool`, used as `Inv s = ok true`.
Strictness is then a THEOREM ABOUT THE MONAD, not a heuristic:

    bind (fail e) k = fail e     bind div k = div        -- Aeneas.Std.Primitives, bind_fail/bind_div
    ok true ≠ fail e             ok true ≠ div           -- constructor disjointness on Result

so a body that only ever BINDS its fallible calls cannot answer `ok true` after one of them fails.
Divergence is covered by the same argument, for free.

WHAT IS AND IS NOT CLAIMED. Membership in the fragment below IMPLIES failure-strictness (sound);
a strict invariant written outside the fragment is reported anyway (incomplete). That is the
intended trade — a false negative here costs a re-read, a false "certified" costs a wrong verdict.
Strictness is also NOT non-triviality: `def Inv _ : Result Bool := ok true` is perfectly strict and
says nothing. This check does not look at that.
-/

/-- `Aeneas.Std.Result _`, decided by TYPE after `whnf` — never by how the type was spelled.
A syntactic test on the head name would miss a `Result` reached through an `abbrev` or alias, and
would have to special-case `core.result.Result`, which is a DIFFERENT constant: Rust's own `Result`
is a value INSIDE the monad, so matching on its `Ok`/`Err` is a described state, not a failure to
describe the state. Keeping the test on `Aeneas.Std.Result` alone gets that distinction for free —
the same one `neutralWrappers` draws for `assumed_postcondition`. -/
private def isResultTy (t : Expr) : MetaM Bool := do
  let t ← try whnf t catch _ => pure t
  match t.getAppFn with
  | .const c _ => return c == ``Aeneas.Std.Result
  | _ => return false

/-- Why a subterm could not be certified, and at what severity.

`passes` is a `Result` VALUE handed to something: the definite defect, because whatever receives it
can match on it and answer `ok true` after a failure. `passesFn` is weaker and deliberately
separate — a Result-PRODUCING FUNCTION handed to a combinator (`List.foldlM (fun a => do …) …`) is
almost certainly strict, since `Aeneas.Std.Result` has no `MonadExcept`/`tryCatch` instance at all,
so a generic combinator has nothing to catch failures WITH. But "almost certainly" is not the
guarantee this check exists to give, so it is reported as un-certified rather than waved through.
`cannotTell` is the walk admitting it does not know. -/
private inductive Blame where
  | passes     (sub : String)
  | passesFn   (sub : String)
  | cannotTell (why : String)

/-- THE LOAD-BEARING HALF OF THE FRAGMENT: no subterm of `e` has type `Aeneas.Std.Result _`.

A `Result` may be BOUND or RETURNED, never PASSED. If no function ever receives a `Result` value,
no function can observe — hence discard — a measurement's failure, and that is what makes the rule
closed under Aeneas's helper set instead of a blocklist forever chasing it:

  • `ok (! ok? (balance a))` is rejected because `balance a` is a `Result` inside an argument,
    NOT because `ok?` is named anywhere here.
  • `match balance a with | fail _ => ok true | …` is rejected the same way: the elaborated form is
    `Inv.match_1 motive (balance a) …`, so the scrutinee is literally an argument.
  • `Result.ofOption`, by the same rule, is FINE — it never receives a `Result`, so it cannot
    launder one; it can only produce `fail`, which is the strict direction.

Conservative at every exit: out of budget, or a subterm whose type will not infer, is
`cannotTell`, never "free". -/
private partial def resultBlame? (e : Expr) (fuel : Nat) (inFn : Bool := false)
    : MetaM (Option Blame) := do
  if fuel == 0 then return some (.cannotTell "expression nesting exceeded the walk budget")
  match ← (try pure (some (← inferType e)) catch _ => pure none) with
  | none => return some (.cannotTell "a subterm's type could not be inferred")
  -- RENDERED HERE, not carried out. The offending subterm is often found underneath a
  -- `withLocalDecl`, and an `Expr` returned past that scope has a dangling fvar that fails to
  -- pretty-print ("failed to pretty print expression") — the detail field would be useless.
  | some ty =>
    if ← isResultTy ty then
      let s ← ppTrunc e
      return some (if inFn then .passesFn s else .passes s)
  match e with
  | .app f a          => match ← resultBlame? f (fuel - 1) inFn with
                         | some b => return some b
                         | none   => resultBlame? a (fuel - 1) inFn
  | .mdata _ b        => resultBlame? b (fuel - 1) inFn
  | .proj _ _ b       => resultBlame? b (fuel - 1) inFn
  -- Crossing INTO a lambda downgrades the severity: from here on any `Result` found is something
  -- this function would PRODUCE when called, not a failure being handed over already evaluated.
  | .lam n t b bi     =>
    if let some bl ← resultBlame? t (fuel - 1) inFn then return some bl
    withLocalDecl n bi t fun x => resultBlame? (b.instantiate1 x) (fuel - 1) true
  | .forallE n t b bi =>
    if let some bl ← resultBlame? t (fuel - 1) inFn then return some bl
    withLocalDecl n bi t fun x => resultBlame? (b.instantiate1 x) (fuel - 1) true
  | .letE n t v b _   =>
    if let some bl ← resultBlame? t (fuel - 1) inFn then return some bl
    if let some bl ← resultBlame? v (fuel - 1) inFn then return some bl
    withLetDecl n t v fun x => resultBlame? (b.instantiate1 x) (fuel - 1) inFn
  | _                 => return none

private def maxStrictNodes : Nat := 4000
private def maxTermDepth : Nat := 256

/-- `Bind.bind m inst α β x k` (what Aeneas's own `do` elaborator emits — it uses the `Bind`
instance, so the `Aeneas.Std.bind` spelling appears only when written by hand) or
`Aeneas.Std.bind α β x f`. Returns `(x, k)`. -/
private def bindParts? (e : Expr) : MetaM (Option (Expr × Expr)) := do
  let args := e.getAppArgs
  let .const c _ := e.getAppFn | return none
  if c == ``Bind.bind && args.size ≥ 6 then
    if ← isResultTy (← inferType args[4]!) then return some (args[4]!, args[5]!)
  if c == ``Aeneas.Std.bind && args.size ≥ 4 then
    if ← isResultTy (← inferType args[2]!) then return some (args[2]!, args[3]!)
  return none

private def strictBlame (bl : Blame) (site : String) : MetaM (String × String) := do
  match bl with
  | .passes sub    => return ("fail_open",
      s!"a Result-valued expression is handed to {site}, so its failure can be observed and \
discarded: {sub}")
  | .passesFn sub  => return ("not_certified",
      s!"{site} is a function that itself produces a Result ({sub}), so this walk cannot see what \
the callee does with a failure — write the fold as explicit recursion, which certifies")
  | .cannotTell why => return ("not_certified", s!"{site}: {why}")

mutual
  /-- `none` iff the `Result`-valued expression `e` is failure-strict. THE FRAGMENT, in full:

      strict(bind x k)      = strict(x) ∧ strict(k v)          -- the only way to consume a Result
      strict(ite c a b)     = resultFree(c) ∧ strict(a) ∧ strict(b)
      strict(match d alts)  = resultFree(d) ∧ ∀ alt, strict(alt)
      strict(f a₁ … aₙ)     = ∀ i, resultFree(aᵢ) ∧ certifiedStrict(f)
      otherwise             = REJECT

  `ok`, `pure` and `massert` need no clause of their own — they are ordinary calls whose arguments
  are `resultFree`, and they live in trusted modules, so the last clause already accepts them.
  DEFAULT-REJECT is deliberate: the fallthrough is a finding, never "unrecognised, keep walking". -/
  private partial def strictWalk (e : Expr) (seen : NameSet) (budget : IO.Ref Nat)
      : MetaM (Option (String × String)) := do
    if (← budget.get) == 0 then
      return some ("not_certified", s!"strictness walk hit the {maxStrictNodes}-node budget")
    budget.modify (· - 1)
    match e with
    | .mdata _ b => return ← strictWalk b seen budget
    | .letE n t v b _ =>
      -- A `let` of a `Result` is a bind wearing different clothes; anything else must be
      -- `resultFree`, or the failure could be observed in the value.
      if ← isResultTy t then
        if let some r ← strictWalk v seen budget then return some r
      else if let some bl ← resultBlame? v maxTermDepth then
        return some (← strictBlame bl "a let-bound value")
      return ← withLetDecl n t v fun x => strictWalk (b.instantiate1 x) seen budget
    | _ =>
      if let some (x, k) ← bindParts? e then
        if let some r ← strictWalk x seen budget then return some r
        return ← strictUnderLam k seen budget
      let args := e.getAppArgs
      if let .const c _ := e.getAppFn then
        -- `ite`/`dite` split on a PURE condition and both branches stay in the monad, so each is
        -- walked. Passing them through the generic clause instead would reject every branching
        -- invariant, since a branch is `Result`-valued and would fail `resultFree`.
        if (c == ``ite || c == ``dite) && args.size ≥ 5 then
          if let some bl ← resultBlame? args[1]! maxTermDepth then
            return some (← strictBlame bl s!"the condition of an `{c}`")
          for br in #[args[3]!, args[4]!] do
            let r ← if c == ``dite then strictUnderLam br seen budget
                    else strictWalk br seen budget
            if let some r := r then return some r
          return none
      if let some m ← (try matchMatcherApp? e catch _ => pure none) then
        for d in m.discrs do
          if let some bl ← resultBlame? d maxTermDepth then
            return some (← strictBlame bl s!"the scrutinee of a `match` (`{m.matcherName}`)")
        for r in m.remaining do
          if let some bl ← resultBlame? r maxTermDepth then
            return some (← strictBlame bl "an argument applied to a `match`")
        for i in [0:m.alts.size] do
          let np := m.altNumParams[i]?.getD 0
          let r ← lambdaBoundedTelescope m.alts[i]! np fun _ body => strictWalk body seen budget
          if let some r := r then return some r
        return none
      -- GENERIC CALL. Every argument must be `resultFree` — that is what makes the rule closed —
      -- and a PROJECT-LOCAL callee that itself returns `Result` must be certified too, or
      -- `do let r ← BadHelper s; ok r` would launder the whole defect one level down.
      let fnDesc := match e.getAppFn with | .const c _ => s!"`{c}`" | _ => "a function"
      for a in args do
        if let some bl ← resultBlame? a maxTermDepth then
          return some (← strictBlame bl s!"an argument of {fnDesc}")
      match e.getAppFn with
      | .const c _ => certifiedStrict c seen budget
      -- A function PARAMETER, handed only resultFree arguments. Accepted, and this is a real hole
      -- in "certified" rather than in coverage: `def Inv (f : St → Result Bool) s := f s` certifies
      -- whatever `f` is instantiated to later, since the walk sees only the binder. Nothing Aeneas
      -- generates has this shape, so it is documented rather than handled.
      | .fvar _    => return none
      | _          => return some ("not_certified",
                        s!"unrecognised head of a Result-valued expression: {← ppTrunc e}")

  /-- A bind continuation, or a `dite` branch. Eta-reduced forms are expanded rather than rejected:
  Aeneas's own generated code produces them. -/
  private partial def strictUnderLam (k : Expr) (seen : NameSet) (budget : IO.Ref Nat)
      : MetaM (Option (String × String)) := do
    let k ← if k.isLambda then pure k else (try Meta.etaExpand k catch _ => pure k)
    match k with
    | .lam n t b bi => withLocalDecl n bi t fun x => strictWalk (b.instantiate1 x) seen budget
    | _ => return some ("not_certified", s!"a continuation is not a function literal: {← ppTrunc k}")

  /-- Is `n` a failure-strict `Result`-valued function?

  Three exits before any walking. A name already on `seen` is a SELF- or MUTUAL call and is assumed
  strict — sound, because `bind` propagates whatever the recursive call yields, so the recursion
  cannot manufacture an `ok` out of a `fail`. A TRUSTED-module callee is accepted, on the same
  ground the arguments were already checked for: we never hand it a `Result`, so it cannot observe
  one of ours. A callee that does not return `Result` at all is irrelevant — its arguments were
  already `resultFree`.

  THE BODY COMES FROM `getEqnsFor?`, NOT `dv.value`. A recursive invariant — a fold over accounts,
  which is exactly what `sum(balances) = total_supply` is — compiles to
  `fun l => List.brecOn l Inv._f`, and no syntactic walk can read that. The equation lemmas give
  back the surface `do`-chain, one per branch, for structural AND well-founded recursion alike
  (verified on both). `dv.value` is the fallback for definitions that have no equations. Each RHS is
  walked INSIDE its own `forallTelescope`, never carried out of it. -/
  private partial def certifiedStrict (n : Name) (seen : NameSet) (budget : IO.Ref Nat)
      : MetaM (Option (String × String)) := do
    if seen.contains n then return none
    if ← isTrustedDecl n then return none
    let info ← getConstInfo n
    unless ← forallTelescope info.type (fun _ b => isResultTy b) do return none
    let seen := seen.insert n
    let eqns ← try getEqnsFor? n catch _ => pure none
    match eqns with
    | some eqs =>
      for eq in eqs do
        let t ← instantiateMVars (← getConstInfo eq).type
        let r ← forallTelescope t fun _ body => do
          let some (_, _, rhs) := body.eq?
            | return some ("not_certified", s!"equation `{eq}` of `{n}` is not an equation")
          strictWalk rhs seen budget
        if let some r := r then return some r
      return none
    | none =>
      match info with
      | .defnInfo dv => strictWalk dv.value seen budget
      | .axiomInfo _  => return some ("not_certified", s!"`{n}` is a Prop-less `axiom` with no body")
      -- A `partial def` also lands here: Lean compiles it to an opaque constant, so this one
      -- message has to cover both spellings or it reads as wrong for the commoner of the two.
      | .opaqueInfo _ => return some ("not_certified",
          s!"`{n}` is `opaque` or `partial`, so it has no body to inspect")
      | _ => return some ("not_certified", s!"`{n}` has no inspectable body")
end

/-- `some (inv, args)` iff `e` is `Inv a₁ … aₙ = ok true` for a constant `Inv`.

DISCRIMINATOR AGAINST `assumed_postcondition`'s EXECUTION RULE, and the thing a reader will trip on: this shape
also matches `definingEq?`, so an invariant hypothesis looks exactly like an execution hypothesis.
The separator is the OUTPUT — a real execution binds fresh variables (`ok (y, s')`, non-empty
`outputLeaves`), while an invariant claim PINS a literal (`ok true`, no leaves at all). Requiring
the literal `true` is also what keeps this restrictive: `ok b` with `b` a variable is an execution. -/
private def invariantClaim? (e : Expr) : MetaM (Option (Name × Array Expr)) := do
  let some (fn, args, outs) ← definingEq? e | return none
  let .const c _ := fn | return none
  unless outs.size == 1 do return none
  unless (← whnf outs[0]!) == mkConst ``Bool.true do return none
  return some (c, args)

/-- The single argument position at which `a` and `b` differ. `none` unless there is exactly one —
"the invariant, on a different state, with everything else the same" is the whole shape, and
allowing two differences would let an unrelated pair of predicate uses match. -/
private def soleDifference? (a b : Array Expr) : MetaM (Option Nat) := do
  unless a.size == b.size do return none
  let mut diff : Option Nat := none
  for i in [0:a.size] do
    unless ← withNewMCtxDepth (isDefEq a[i]! b[i]!) do
      if diff.isSome then return none
      diff := some i
  return diff

/-- The invariant-preservation SHAPE, factored out so both callers share one rule. `invariant_not_strict` reports a
failure as a near-miss `SKIPPED` — it was only guessing the theorem's intent — while
`checkSchemaConformance` reports the very same failure as a FINDING, because there the author
DECLARED that intent. One rule, two severities, decided by whether anyone claimed the shape.

`none` = the conclusion is not an `Inv args = ok true` claim at all, so not even a near miss.
`.error why` = it looks like one but the shape does not hold. `.ok inv` = the invariant's name. -/
private def invariantShape? (qn : Name) (targets : Array Name)
    : MetaM (Option (Except String (Name × Nat × Nat))) := do
  let ci ← getConstInfo qn
  forallTelescope (← instantiateMVars ci.type) fun xs concl => do
    let some (inv, cargs) ← invariantClaim? (← instantiateMVars concl) | return none
    if ← isTrustedDecl inv then
      return some (.error "the conclusion's invariant is library code, not project-local")
    -- the invariant, on some other state, among the hypotheses
    let mut pre : Option (Nat × Array Expr × Nat) := none
    for i in [0:xs.size] do
      let bty ← instantiateMVars (← inferType xs[i]!)
      if let some (c, hargs) ← invariantClaim? bty then
        if c == inv then
          -- FIRST match wins, so which hypothesis a finding is about does not depend on binder
          -- order.
          if pre.isNone then
            if let some j ← soleDifference? hargs cargs then pre := some (j, hargs, i)
    let some (j, hargs, preIdx) := pre
      | return some (.error "no hypothesis applies the same invariant to another state at exactly one differing argument")
    -- an execution of a DECLARED TARGET that consumes the pre-state and produces the post-state.
    -- Both states must be BARE VARIABLES of the telescope: an execution produces a variable, and a
    -- pre-state given as a compound expression is not the shape this check is about.
    let (.fvar preId, .fvar postId) := (hargs[j]!, cargs[j]!)
      | return some (.error "the differing argument is not a bare variable on both sides")
    for i in [0:xs.size] do
      let bty ← instantiateMVars (← inferType xs[i]!)
      if let some (fn, eargs, outs) ← definingEq? bty then
        unless isSubjectCall targets fn do continue
        unless eargs.any (fun a => a.containsFVar preId) do continue
        let mut leaves : Array FVarId := #[]
        for fld in outs do leaves := leaves ++ (← outputLeaves fld)
        -- `preIdx`/`i` are what let `schema_conformance` insist NOTHING ELSE propositional is here.
        if leaves.contains postId then return some (.ok (inv, preIdx, i))
    return some (.error "no execution hypothesis for a named target consumes the pre-state and produces the post-state")

/-- `invariant_not_strict` — an invariant-preservation theorem whose invariant is not certified
failure-strict.

THE SHAPE, and it is deliberately narrow — false negatives are the accepted cost:

    theorem preserves …
        (hinv  : Inv c₁ … s  … cₙ = ok true)   -- the invariant, on the PRE-state
        (hexec : f x s = ok (y, s'))            -- a DECLARED TARGET, producing s'
        : Inv c₁ … s' … cₙ = ok true            -- the SAME invariant, on the POST-state

All three must hold, and the `cᵢ` must agree: the hypothesis and the conclusion must name the same
constant, differ at exactly ONE argument position, the pre-state there must be consumed by the
execution, and the post-state there must be one of the execution's OWN output variables. Anything
looser fires on ordinary measurement chains.

WHAT SILENCE MEANS HERE, and it is weaker than for `assumed_postcondition`. Nothing is reported when the
theorem is not of this shape at ALL — a judge running all three checks over a whole module would
otherwise get a line per theorem. So silence means "certified strict, OR not an invariant-
preservation theorem in this form". A NEAR MISS — the conclusion is `Inv … = ok true` but the rest
of the shape does not hold — IS reported as `SKIPPED`, because that is the case where a judge
believes the check ran and it did not. An invariant written INLINE in the theorem rather than as a
`def` has no constant to walk and is therefore invisible; write it as a `def`. -/
def checkInvariantTotality (qn : Name) (targets : Array Name) : MetaM Nat := do
  let tlist := String.intercalate ", " (targets.toList.map (fun t => "\"" ++ toString t ++ "\""))
  if targets.isEmpty then
    IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"invariant_not_strict\", \"theorem\": \"{qn}\", \"reason\": \"no targets given\"}"
    return 0
  match ← invariantShape? qn targets with
  | none => return 0
  | some (.error why) =>
    IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"invariant_not_strict\", \"theorem\": \"{qn}\", \"reason\": \"{why}\", \"targets\": [{tlist}]}"
    return 0
  | some (.ok (inv, _, _)) =>
    -- SHAPE CONFIRMED. Now the only question that matters.
    let budget ← IO.mkRef maxStrictNodes
    match ← certifiedStrict inv {} budget with
    | none => return 0
    | some (reason, detail) =>
      emit s!"\{\"check\": \"invariant_not_strict\", \"theorem\": \"{qn}\", \"invariant\": \"{inv}\", \"reason\": \"{reason}\", \"detail\": \"{jesc detail}\"}"
      return 1

/-! ══ `schema_conformance` — verify the DECLARED intent ═══════════════════════════════════════════

The other checks hunt for bad shapes, which is why their SILENCE is ambiguous — "clean, or nothing I
recognise". This one inverts that. FORMALISE annotates each theorem with the schema it is written
to (`@[lusterna_invariant]`, `@[lusterna_hoare]`, `@[lusterna_freeform "why"]`), and this verifies
the annotation. A theorem that fails the schema IT DECLARED is a FACT, not a judgement — which is
what lets it block rather than merely report (see `checkSpecGate` below).

DEFAULT-REJECT IS ONLY SAFE BECAUSE OF THE ANNOTATION. Applied to arbitrary theorems it would flag
every honest use of `→`, `∨`, `¬`. Applied to a theorem whose author declared its schema, it is
precise — the annotation is what buys the strictness.

WHAT THIS DOES NOT CHECK: the annotation is written by the same agent whose work is being checked,
so this verifies FORM, not FITNESS. Nothing here stops a theorem being annotated `invariant` when
the property is really a Hoare triple, and conforming. Whether the declared schema is the RIGHT
schema, and whether the invariant is the right invariant, stays with SPEC-JUDGE.
-/

/-- Does this output carry INFORMATION a postcondition could state anything about? A proof term does
not, and neither does a value of a type that offers no choice — `Unit`, `PUnit`, any
single-constructor-no-field structure. Rule 6 demands the postcondition mention every output, and
`ok ((), s')` (an Aeneas void function) would otherwise be reported for ignoring its `Unit`: a
pedantic finding about a value with nothing to say. Same reasoning `canonicalOutput` already applies
to neutral wrappers — a type with one constructor and no fields carries no claim. -/
private def carriesInformation (l : FVarId) : MetaM Bool := do
  let ty ← l.getType
  if ← Meta.isProp ty then return false
  let .const cn _ := (← whnf ty).getAppFn | return true
  let .inductInfo iv ← getConstInfo cn | return true
  match iv.ctors with
  | [c] =>
    let .ctorInfo cv ← getConstInfo c | return true
    return cv.numFields != 0
  | _   => return true

/-- Reuses `assumed_postcondition`'s freshness rule: the execution binder says what was RUN, never
what came out. -/
private def execOutputsFresh (fn : Expr) (args outs : Array Expr) (leaves : Array FVarId)
    : MetaM (Option String) := do
  let ownArgs := leaves.filter (fun l => args.any (fun a => a.containsFVar l))
  unless ownArgs.isEmpty do
    return some "the execution's output aliases one of its own arguments"
  let dup := leaves.filter (fun l => (leaves.filter (· == l)).size > 1)
  unless dup.isEmpty do return some "an output variable is bound twice by the execution"
  for o in outs do
    unless ← canonicalOutput o do
      return some s!"the execution pins a value or a constructor branch: {← ppTrunc o}"
  let _ := fn
  return none

/-- `schema_conformance`. `targets` is the same list the other target-aware checks take. -/
def checkSchemaConformance (qn : Name) (targets : Array Name) : MetaM Nat := do
  let env ← getEnv
  let report (schema rule detail : String) : MetaM Unit :=
    emit s!"\{\"check\": \"schema_conformance\", \"theorem\": \"{qn}\", \"schema\": \"{schema}\", \"rule\": \"{rule}\", \"detail\": \"{jesc detail}\"}"
  let schemas := Lusterna.Schemas.declaredSchemas env qn
  -- NO SCHEMA is itself a failure. If unannotated meant "out of scope", not annotating would be
  -- strictly cheaper than annotating, and the whole scheme would be an opt-out.
  if schemas.isEmpty then
    report "none" "no_schema_declared"
      "every spec theorem must declare exactly one schema; a supporting lemma declares \
@[lusterna_freeform \"why\"]"
    return 1
  if schemas.size > 1 then
    report (String.intercalate "+" schemas.toList) "multiple_schemas_declared"
      s!"declares {schemas.size} schemas ({String.intercalate ", " schemas.toList}); pick one — \
being checked against the laxer of two is not a conformance result"
    return 1
  let schema := schemas[0]!
  -- A `private` theorem's attribute may not survive export (see Lean's `exportEntriesFnEx`), so a
  -- private annotated theorem could read back as unannotated. Reported, never silently missed.
  if (`_private).isPrefixOf qn then
    report schema "private_declaration"
      "a private theorem's schema annotation may not survive module export; spec theorems must be public"
    return 1
  if schema == "freeform" then
    -- The reason string is validated at attribute-elaboration time (it cannot be empty), so there
    -- is nothing left to verify. Deliberately NOT gated on — it is reported in the campaign report.
    return 0
  if targets.isEmpty then
    IO.println s!"LUSTERNA_CHECK_SKIPPED \{\"check\": \"schema_conformance\", \"theorem\": \"{qn}\", \"schema\": \"{schema}\", \"reason\": \"no targets given\"}"
    return 0
  if schema == "invariant" then
    match ← invariantShape? qn targets with
    | none =>
      report schema "invariant_shape"
        "the conclusion is not `Inv args = ok true` for a project-local definition"
      return 1
    | some (.error why) => report schema "invariant_shape" why; return 1
    | some (.ok (inv, preIdx, execIdx)) =>
      -- NOTHING BUT THE INVARIANT AND THE EXECUTION. The pre-state claim, the execution, and the
      -- conclusion — that is the whole theorem. A side condition left inline
      -- (`(hcl : c.val ≤ l.val)`) makes the entering assumption STRONGER than the invariant, so the
      -- theorem is no longer "this invariant is preserved" but a Hoare triple wearing its clothes,
      -- and the reader cannot tell which from the annotation. Fold the condition into the invariant,
      -- or declare `hoare` and name it in `Pre` — where it becomes an inspectable object a later
      -- campaign can reuse rather than a loose hypothesis. Without this the two schemas held their
      -- preconditions to different standards, and the choice between them was partly discretionary.
      let ci ← getConstInfo qn
      -- The count comes BACK from the telescope: `let mut` does not cross the closure boundary.
      let mut n ← forallTelescope (← instantiateMVars ci.type) fun xs _ => do
        let mut k := 0
        for i in [0:xs.size] do
          if i == preIdx || i == execIdx then continue
          let bty ← instantiateMVars (← inferType xs[i]!)
          if ← isPropSafe bty then
            report schema "extraneous_hypothesis"
              s!"an invariant-preservation theorem carries only the invariant on the pre-state and \
the execution; this is neither: {← ppTrunc bty} — fold it into `{inv}`, or declare `hoare` and name \
it in `Pre`"
            k := k + 1
        return k
      let budget ← IO.mkRef maxStrictNodes
      if let some (_, detail) ← certifiedStrict inv {} budget then
        report schema "predicate_not_failure_strict" s!"the invariant `{inv}` is not certified: {detail}"
        n := n + 1
      return n
  unless schema == "hoare" do return 0
  -- ── the FORWARD HOARE TRIPLE ────────────────────────────────────────────────────────────────
  let ci ← getConstInfo qn
  forallTelescope (← instantiateMVars ci.type) fun xs concl => do
    let mut n := 0
    -- Classify each binder. A `Pre args = ok true` hypothesis also matches `definingEq?`, so the
    -- EXECUTIONS are exactly the defining equations on a DECLARED TARGET — a precondition's head is
    -- never a target, so it lands among the propositions where it belongs.
    let mut execs : Array (Expr × Array Expr × Array Expr) := #[]
    let mut props : Array Expr := #[]
    for x in xs do
      let bty ← instantiateMVars (← inferType x)
      if let some (fn, args, outs) ← definingEq? bty then
        if isSubjectCall targets fn then
          execs := execs.push (fn, args, outs); continue
      if ← isPropSafe bty then props := props.push bty
    -- RULE 1 — exactly one execution. This is what makes the taint fixpoint unnecessary here:
    -- with one declared execution and a whitelisted hypothesis form, smuggling a fact about the
    -- post-state is structurally impossible rather than merely detectable.
    unless execs.size == 1 do
      report schema "execution_not_unique"
        s!"a Hoare triple has exactly one execution of a declared target; found {execs.size}"
      return 1
    let (fn, eargs, eouts) := execs[0]!
    let mut leaves : Array FVarId := #[]
    for fld in eouts do
      for l in ← outputLeaves fld do
        unless ← Meta.isProp (← l.getType) do leaves := leaves.push l
    -- RULE 2 — the execution binds fresh, unpinned outputs.
    if let some why ← execOutputsFresh fn eargs eouts leaves then
      report schema "execution_output_not_fresh" why
      n := n + 1
    -- RULE 3 — every other proposition is a PRECONDITION over root inputs.
    for bty in props do
      let some (pre, pargs) ← invariantClaim? bty
        | report schema "hypothesis_not_a_precondition"
            s!"not of the form `Pre args = ok true`: {← ppTrunc bty} — state it inside `Pre`"
          n := n + 1
          continue
      -- Naming WHICH argument, because "move it into `Pre`" is only actionable if you know which.
      for k in [0:pargs.size] do
        let hit := leaves.filter (fun l => pargs[k]!.containsFVar l)
        unless hit.isEmpty do
          let names ← hit.mapM fun v => return toString (← v.getDecl).userName
          report schema "precondition_mentions_output"
            s!"`{pre}` argument #{k} ({← ppTrunc pargs[k]!}) mentions the execution's own output \
{String.intercalate ", " names.toList}; a precondition may only speak about root inputs"
          n := n + 1
      let budget ← IO.mkRef maxStrictNodes
      if let some (_, detail) ← certifiedStrict pre {} budget then
        report schema "predicate_not_failure_strict"
          s!"the precondition `{pre}` is not certified: {detail}"
        n := n + 1
    -- RULE 4 — the conclusion IS a postcondition claim.
    let some (post, cargs) ← invariantClaim? (← instantiateMVars concl)
      | report schema "conclusion_not_a_postcondition"
          s!"the conclusion must be `Post args = ok true` for a project-local definition, not \
{← ppTrunc concl}"
        return n + 1
    -- RULE 5 — the postcondition is failure-strict.
    let budget ← IO.mkRef maxStrictNodes
    if let some (_, detail) ← certifiedStrict post {} budget then
      report schema "predicate_not_failure_strict"
        s!"the postcondition `{post}` is not certified: {detail}"
      n := n + 1
    -- RULE 6 — the postcondition actually USES what the execution produced. Without this a theorem
    -- conforms while saying nothing about the function: the data binders naming outputs in advance
    -- (`ok (.Ok eff, vfinal)`) are not propositions, so rule 3 never sees them, and nothing else
    -- would force `Post` to mention them.
    let unused ← leaves.filterM fun l => do
      unless ← carriesInformation l do return false
      return !(cargs.any (fun a => a.containsFVar l))
    unless unused.isEmpty do
      let names ← unused.mapM fun v => return toString (← v.getDecl).userName
      report schema "postcondition_ignores_output"
        s!"`{post}` never mentions the execution's output {String.intercalate ", " names.toList}, \
so the theorem claims nothing about what `{fn}` produced"
      n := n + 1
    return n

/-! ══ THE SPEC GATE — what the harness itself enforces ══════════════════════════════════════════

`checkSpecGate` composes all three checks into the one thing FORMALISE must clear. It is not a tool
an agent invokes and interprets: a finding here BLOCKS the stage and comes back as a concrete
critique, and FORMALISE runs again.

WHY BLOCKING IS SOUND NOW AND WAS NOT BEFORE. `checkAssumedPostcondition` has three documented,
unavoidable false positives — a relational antecedent (injectivity, determinism, cancellation,
non-interference), a bound on an intermediate, and a non-execution case split in the conclusion.
Blocking on those would make a CORRECT theorem unsubmittable, which is exactly why the checks used
to be advisory. The schema ANNOTATION retires all three, because each is excluded by the `hoare`
conformance rules themselves:

  • a relational antecedent needs TWO executions — `execution_not_unique` forbids that;
  • a bound on an intermediate is not `Pre <root inputs> = ok true` — `hypothesis_not_a_precondition`
    forbids it, and names the fix (move it into `Pre`);
  • a case split in the conclusion is not `Post … = ok true` — `conclusion_not_a_postcondition`.

The false positive's own rationale is that "the strict rule is about single-execution Hoare
properties and these are not". `@[lusterna_hoare]` is the author DECLARING that this one is. So on a
conforming theorem a finding is a defect rather than a prompt, and blocking is honest.

PRECEDENCE, so the critique stays actionable: a theorem that does not conform to its declared schema
gets THAT finding and nothing else. Piling taint findings on a theorem whose shape is already wrong
buries the one message FORMALISE can act on.

SCOPING: `@[lusterna_freeform]` is out of scope by declaration — it is where a relational or
two-run property lives, so the other two checks do not run on it and cannot block it.

CONTINUATIONS ARE ALWAYS `#[]`, and nothing is lost. A theorem genuinely about a COMPOSITION needs a
second execution, which `execution_not_unique` rejects — so such a theorem is `freeform`, and never
reaches the checks that would have needed the exemption. -/
def checkSpecGate (qn : Name) (targets : Array Name) : MetaM Nat := do
  let schemas := Lusterna.Schemas.declaredSchemas (← getEnv) qn
  -- Missing / doubled / freeform are all settled by the conformance check alone.
  if schemas.size != 1 || schemas[0]! == "freeform" then
    return ← checkSchemaConformance qn targets
  let conformance ← checkSchemaConformance qn targets
  if conformance > 0 then return conformance
  -- SHAPE IS RIGHT. Only now do the provenance rules mean what they say.
  let taint ← checkAssumedPostcondition qn targets #[]
  -- `invariant_not_strict` runs ONLY on a theorem that declared `invariant`. On a `hoare` theorem
  -- its own shape detection near-misses — the conclusion `Post args = ok true` IS an
  -- `Inv args = ok true` claim, with no matching pre-state hypothesis — so it emits `SKIPPED`, and a
  -- skip blocks. A conforming Hoare triple would be rejected for a check that does not apply to it.
  -- Nothing is lost by scoping it: for a `hoare` theorem the strictness of `Pre`/`Post` is already
  -- enforced by `schema_conformance`'s `predicate_not_failure_strict`, which calls the same
  -- `certifiedStrict`.
  let strict ← if schemas[0]! == "invariant" then checkInvariantTotality qn targets else pure 0
  return taint + strict

/-! ══ `assumption_legitimacy` — the trusted base may not relax a goal ═════════════════════════════

The declared trusted base (`axiom`s in `lean/<Crate>/Assumptions.lean`) may hold facts ONLY about the
SUBSTRATE, never a property of a TARGET function under verification: a GOAL is a property OF a target,
so an axiom that references a target could BE a goal, and admitting it would relax what must be proved.

This walks each axiom's ELABORATED type for target references — robust where a text scan is not: a
target reached through notation, a coercion, an `abbrev`, or a re-export is a real `.const` occurrence
in the type and is caught, whereas the surface spelling need not contain the target's name at all.

A referenced constant is a TARGET iff it is a project-local `def` (`isCrateDef` — a crate function,
NOT a trusted-library constant and NOT an Aeneas-emitted `axiom`; the external substrate is exactly
what an assumption MAY mention) AND, when explicit target patterns are given, its name carries one as
a dotted suffix. Empty *targets* is WHOLE-CRATE mode: every crate `def` is a target, so no assumption
about crate code passes. This mirrors `lean.target_defs` per mode, so the verdict matches the text
gate it replaces while catching the references that gate could not see. -/
def checkAssumptionLegitimacy (axioms : Array Name) (targets : Array Name) : MetaM Unit := do
  let env ← getEnv
  let isCrateDef (c : Name) : MetaM Bool := do
    if ← isTrustedDecl c then return false
    return match env.find? c with | some (.defnInfo _) => true | _ => false
  for ax in axioms do
    let some (.axiomInfo av) := env.find? ax | continue
    let hits ← av.type.getUsedConstants.filterM fun c => do
      if ← isCrateDef c then return targets.isEmpty || targets.any (nameHasSuffix c)
      else return false
    unless hits.isEmpty do
      let tl := String.intercalate ", " (hits.toList.map (fun t => "\"" ++ toString t ++ "\""))
      emit s!"\{\"check\": \"assumption_legitimacy\", \"axiom\": \"{ax}\", \"targets\": [{tl}], \"reason\": \"axiom `{ax}` references target function(s) it may not — the trusted base may hold facts only about the substrate, never a property of a target (that would relax a goal); prove it, do not assume it\"}"

end Lusterna.Checks
