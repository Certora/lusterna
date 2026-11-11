import Aeneas
import LusternaSpec.Attr

/-!
# Check

`check` prints one JSON line for each theorem of the given modules. Run it with
``#eval Lusterna.Spec.check #[`Some.Module]`` in a file that imports those modules.

For a theorem labelled `@[lusterna id "title"]` it checks the statement: the conclusion must be an
Aeneas WP triple `m ⦃ r => post r ⦄`, and `post r` must
* mention `r`,
* contain no term of type `Result _`, because that computation can fail, and
* not hold for every `r`, as `True` or `Q → Q` do.

The last two rules look into the predicates that `post` uses, except the ones declared in Lean,
Aeneas or their dependencies.
-/

open Lean Elab Meta

namespace Lusterna.Spec

/-- How deep `resultMeasurement?` and `postConstrains?` walk into a term. -/
private def maxTermDepth : Nat := 256

/-- Pretty-prints `e`, cut to 220 characters. -/
private def ppTrunc (e : Expr) : MetaM String := do
  let s := (← ppExpr e).pretty
  let cs := s.toList
  return if cs.length > 220 then String.ofList (cs.take 220) ++ " ..." else s

/-- The module prefixes of Lean, Aeneas and their dependencies. -/
private def trustedModulePrefixes : Array Name :=
  #[`Mathlib, `Lean, `Init, `Std, `Batteries, `Aeneas, `Qq, `Aesop, `Plausible, `ImportGraph,
    `LeanSearchClient, `Cli]

/-- `n` is declared in a module of Lean, Aeneas or one of their dependencies. -/
private def isTrustedDecl (n : Name) : MetaM Bool := do
  let env ← getEnv
  let some idx := env.getModuleIdxFor? n | return false
  let some mod := env.header.moduleNames[idx.toNat]? | return false
  return trustedModulePrefixes.any (fun p => p.isPrefixOf mod)

/-- What `predicateBody?` finds at a constant. -/
private inductive PredBody where
  /-- A predicate defined by `value`. -/
  | body (value : Expr)
  /-- A Prop-valued inductive, such as a structure. -/
  | constructors (ctors : Array Name) (numParams : Nat)
  /-- Not a predicate, or a trusted one. -/
  | notPredicate
  /-- A predicate without a body: an axiom or an opaque constant. -/
  | uninspectable (kind : String)

/-- Looks up the constant `n`. Only a Prop-valued `n` that is not declared in a trusted module is a
predicate. -/
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
  | .inductInfo iv => return .constructors iv.ctors.toArray iv.numParams
  | _              => return .notPredicate

/-- Splits `a ∧ b ∧ c` into `#[a, b, c]`. -/
private partial def conjuncts (e : Expr) : Array Expr :=
  match e.getAppFnArgs with
  | (``And, #[a, b]) => conjuncts a ++ conjuncts b
  | _ => #[e]

/-- `t` is `Aeneas.Std.Result _` after `whnf`. -/
private def isResultTy (t : Expr) : MetaM Bool := do
  let t ← try whnf t catch _ => pure t
  match t.getAppFn with
  | .const c _ => return c == ``Aeneas.Std.Result
  | _ => return false

/-- `e` is an Aeneas WP triple `m ⦃ r => post r ⦄`, that is `Aeneas.Std.WP.spec m post` or
`Aeneas.Std.WP.dspec m post`. -/
private def isTripleLike (e : Expr) : Bool :=
  match e.getAppFn with
  | .const n _ => n == ``Aeneas.Std.WP.spec || n == ``Aeneas.Std.WP.dspec
  | _ => false

/-- Returns the first subterm of `e` whose type is `Result _`, pretty-printed, or `none` if there is
none. It looks into the definitions and constructors of the predicates that `e` uses, each at most
once (`seen`). A predicate without a body, and running out of `fuel`, also return `some`. -/
private partial def resultMeasurement? (e : Expr) (seen : NameSet) (fuel : Nat)
    : MetaM (Option String) := do
  if fuel == 0 then return some "the claim nests deeper than the walk budget"
  match ← (try pure (some (← inferType e)) catch _ => pure none) with
  | some ty => if ← isResultTy ty then return some (← ppTrunc e)
  | none    => pure ()
  match e with
  | .mdata _ b      => resultMeasurement? b seen fuel
  | .proj _ _ b     => resultMeasurement? b seen (fuel - 1)
  | .lam n t b bi | .forallE n t b bi =>
    if let some r ← resultMeasurement? t seen (fuel - 1) then return some r
    withLocalDecl n bi t fun x => resultMeasurement? (b.instantiate1 x) seen (fuel - 1)
  | .letE n t v b _ =>
    if let some r ← resultMeasurement? t seen (fuel - 1) then return some r
    if let some r ← resultMeasurement? v seen (fuel - 1) then return some r
    withLetDecl n t v fun x => resultMeasurement? (b.instantiate1 x) seen (fuel - 1)
  | _ =>
    for a in e.getAppArgs do
      if let some r ← resultMeasurement? a seen (fuel - 1) then return some r
    let .const cn _ := e.getAppFn | return none
    if seen.contains cn then return none
    let seen := seen.insert cn
    match ← predicateBody? cn with
    | .body _ =>
      match ← Meta.unfoldDefinition? e with
      | some u => resultMeasurement? u seen (fuel - 1)
      | none   => return none
    | .constructors ctors numParams =>
      let args := e.getAppArgs
      for c in ctors do
        let ct ← instantiateForall (← getConstInfo c).type (args.extract 0 (min numParams args.size))
        if let some r ← resultMeasurement? ct seen (fuel - 1) then return some r
      return none
    | .uninspectable kind => return some s!"a claim rests on an {kind} predicate `{cn}` with no inspectable body"
    | .notPredicate       => return none

/-- `false` iff the postcondition `e` holds no matter what: `True`, `x = x`, `A → B` where every
conjunct of `B` is a conjunct of `A`, or a conjunction of these. It looks into the definitions of
the predicates that `e` uses, and a Prop-valued structure constrains if one of its fields does.
Anything it cannot look into, and running out of `fuel`, count as constraining. -/
private partial def postConstrains? (e : Expr) (fuel : Nat) : MetaM Bool := do
  if fuel == 0 then return true
  if e.isConstOf ``True then return false
  if let some (_, l, r) := e.eq? then
    if l == r then return false
  match e.getAppFnArgs with
  | (``And, #[a, b]) => return (← postConstrains? a (fuel - 1)) || (← postConstrains? b (fuel - 1))
  | _ =>
    match e with
    | .forallE _ dom body bi =>
      if bi == .default && (← Meta.isProp dom) && !body.hasLooseBVars then
        let ac := conjuncts dom
        let bc := conjuncts body
        if bc.all (fun bx => ac.any (fun ax => ax == bx)) then return false
        return (← postConstrains? body (fuel - 1))
      else
        return true
    | _ =>
      let .const cn _ := e.getAppFn | return true
      match ← predicateBody? cn with
      | .body _ =>
        match ← Meta.unfoldDefinition? e with
        | some u => postConstrains? u (fuel - 1)
        | none   => return true
      | .constructors ctors numParams =>
        let args := e.getAppArgs
        for c in ctors do
          let ct ← instantiateForall (← getConstInfo c).type (args.extract 0 (min numParams args.size))
          let fieldConstrains ← forallTelescope ct fun flds _ => do
            for f in flds do
              if ← postConstrains? (← inferType f) (fuel - 1) then return true
            return false
          if fieldConstrains then return true
        return false
      | .uninspectable _ => return true
      | .notPredicate    => return true

/-- A problem with the statement of a labelled theorem. -/
inductive Issue where
  /-- The conclusion is not an Aeneas WP triple `m ⦃ r => post r ⦄`. -/
  | notATriple
  /-- The postcondition does not mention the result. -/
  | ignoresResult
  /-- The postcondition may run a computation that can fail. `reason` is what
  `resultMeasurement?` returned. -/
  | canFail (reason : String)
  /-- The postcondition holds for every result. -/
  | vacuous
deriving ToJson

/-- What `check` prints for one theorem. -/
structure TheoremCheck where
  «name» : Name
  /-- The module that declares the theorem. -/
  «module» : Name
  /-- The id from `@[lusterna]`, or `none` if the theorem has no label. -/
  id : Option Nat
  /-- The title from `@[lusterna]`, or `none` if the theorem has no label. -/
  title : Option String
  /-- The head constant of the computation in the theorem's triple. `none` if the theorem has no
  label, its conclusion is not a triple, or the head is not a constant. -/
  function : Option Name
  /-- Empty if the theorem has no label. -/
  issues : Array Issue
deriving ToJson

/-- Checks the statement of the labelled theorem `qn`. Returns the head constant of the computation
in its triple and the problems with its statement. The triple `m ⦃ r => post r ⦄` elaborates to
`WP.spec m post`, whose last three arguments are the result type, `m` and `post`. `post` is
checked as `post r` for a fresh `r`. -/
private def checkTriple (qn : Name) : MetaM (Option Name × Array Issue) := do
  let ci ← getConstInfo qn
  forallTelescope (← instantiateMVars ci.type) fun _ concl => do
    let concl ← instantiateMVars concl
    let sargs := concl.getAppArgs
    unless isTripleLike concl && sargs.size ≥ 3 do
      return (none, #[.notATriple])
    let function := sargs[sargs.size - 2]!.getAppFn.constName?
    let α := sargs[sargs.size - 3]!
    let post := sargs[sargs.size - 1]!
    withLocalDeclD `r α fun r => do
      let body := (mkApp post r).headBeta
      let mut issues : Array Issue := #[]
      unless body.containsFVar r.fvarId! do
        issues := issues.push .ignoresResult
      if let some reason ← resultMeasurement? body {} maxTermDepth then
        issues := issues.push (.canFail reason)
      unless ← postConstrains? body maxTermDepth do
        issues := issues.push .vacuous
      return (function, issues)

/-- Prints one `TheoremCheck` as a JSON line for each theorem declared in `modules`. Only the
statements of theorems with `@[lusterna]` are checked. Throws if a module is not imported. -/
def check (modules : Array Name) : MetaM Unit := do
  let env ← getEnv
  for mod in modules do
    let some idx := env.getModuleIdx? mod | throwError "module {mod} is not imported"
    let some data := env.header.moduleData[idx.toNat]? | throwError "no data for module {mod}"
    for thm in data.constNames do
      let some (.thmInfo _) := env.find? thm | continue
      if thm.isInternal then continue
      let label := lusternaAttr.getParam? env thm
      let (function, issues) ← if label.isSome then checkTriple thm else pure (none, #[])
      let result : TheoremCheck := {
        function, issues
        «name» := thm
        «module» := mod
        id := label.map (·.1)
        title := label.map (·.2)
      }
      IO.println (toJson result).compress

end Lusterna.Spec
