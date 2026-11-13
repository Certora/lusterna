import Aeneas
import LusternaSpec.FSpec

/-!
# `#step_skip`

`#step_skip` takes one or more functions and adds, for each function `f`, the `@[step]` theorem
`f.fspec_skip`. It states `f x₁ … xₙ ⦃ _ => True ⦄fail` for all arguments `x₁ … xₙ` of `f`,
implicit and instance arguments included. With it, `step` goes past a call of `f` without looking
into `f`.
-/

open Lean Elab Command Meta

namespace Lusterna.Spec

/-- `#step_skip f₁ … fₙ`: adds `fᵢ.fspec_skip` for each of the one or more functions. -/
syntax (name := stepSkip) "#step_skip" (ppSpace ident)+ : command

/-- Adds the `@[step]` theorem `fn.fspec_skip`. Throws if the type of `fn` does not end in
`Result _`. -/
private def addFSpecSkip (fn : Name) : CommandElabM Unit := liftTermElabM do
  let info ← getConstInfo fn
  let (type, value) ← forallTelescope info.type fun xs ret => do
    unless ret.getAppFn.isConstOf ``Aeneas.Std.Result do
      throwError "`{fn}` does not return a `Result`"
    let proof ← mkAppM ``fspec_true #[mkAppN (mkConst fn (info.levelParams.map Level.param)) xs]
    return (← mkForallFVars xs (← inferType proof), ← mkLambdaFVars xs proof)
  let thm := fn ++ `fspec_skip
  addDecl (.thmDecl (mkTheoremValEx thm info.levelParams type value [thm]))
  Term.applyAttributes thm #[⟨.global, `step, .missing⟩]

@[command_elab stepSkip]
def elabStepSkip : CommandElab
  | `(#step_skip $fns*) => do
    for fn in fns do
      addFSpecSkip (← liftCoreM <| realizeGlobalConstNoOverloadWithInfo fn)
  | _ => throwUnsupportedSyntax

end Lusterna.Spec
