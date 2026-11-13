module
public import Aeneas

/-!
# `fspec`

A triple for a computation that may panic: `fspec m p` holds if `p` holds for the value that `m`
returns, and also if `m` panics or diverges. It is built like `Aeneas.Std.WP.dspec`, and `step`
works on it.
-/

@[expose] public section

namespace Lusterna.Spec

open Aeneas Aeneas.Std Result WP
open Aeneas.Data.Coinductive

section ResultImplementation

unseal Result

/-- The WP of the effects of Rust programs (`RustEffect`) in which a panic satisfies every
postcondition. -/
@[reducible]
def failEffectWP : EffectWP RustEffect where
  State := Unit
  wp effect _ _ :=
    match effect with
    | .fail _ => True

instance : EffectWP.Monotonic failEffectWP where
  wp_monotonic := @fun effect _ _ _ _ _ => by trivial

instance : EffectWP.Conjunctive failEffectWP where
  wp_conj := @fun effect _ _ _ _ => by trivial

/-- `fspec m p` holds if `p` holds for the value that `m` returns, and if `m` panics or
diverges. -/
def fspec {α} (m : Result α) (p : Post α) : Prop :=
  DWLP failEffectWP m (fun value _ => p value) ()

/-- For a computation that returns `x`, `fspec` is the postcondition for `x`. -/
@[simp, grind =, agrind =]
theorem fspec_ok {α} {p : Post α} (x : α) : fspec (ok x) p ↔ p x := DWLP.ret_iff

/-- `fspec` holds for `.vis e k`. The only effect `e` of `RustEffect` is a panic. -/
@[simp, grind =, agrind =]
theorem fspec_vis {α} {p : Post α} (e k) : fspec (.vis e k : Result α) p ↔ True :=
  iff_true_intro (DWLP.vis (by cases e; trivial))

/-- `fspec` holds for a computation that diverges. -/
@[simp, grind =, agrind =]
theorem fspec_div {α} {p : Post α} : fspec (div : Result α) p ↔ True := iff_true_intro DWLP.div

/-- `fspec` holds for a computation that panics. -/
@[simp, grind =, agrind =]
theorem fspec_fail {α} {p : Post α} (e : Error) : fspec (fail e : Result α) p ↔ True := by
  simp [Result.fail_eq_vis]

/-- Mono rule used by `step`. -/
theorem fspec_mono {α} {P₁ : Post α} {m : Result α} {P₀ : Post α} (h : fspec m P₀) :
    (∀ x, P₀ x → P₁ x) → fspec m P₁ :=
  fun HMonPost => DWLP.mono h fun value _ => HMonPost value

/-- Bind rule used by `step`. -/
theorem fspec_bind {α β} {k : α → Result β} {Pₖ : Post β} {m : Result α} {Pₘ : Post α} :
    fspec m Pₘ →
    (∀ x, Pₘ x → fspec (k x) Pₖ) →
    fspec (Std.bind m k) Pₖ :=
  fun Hm Hk => DWLP.bind (k := k) Hm fun value _ => Hk value

end ResultImplementation

/-- `fspec m p` says that `p` holds for the value that `m` returns. -/
theorem fspec_iff {α} {m : Result α} {p : Post α} : fspec m p ↔ ∀ r, m = ok r → p r := by
  cases m using Result.cases with
  | ret x => simp
  | vis e k => simp
  | div => simp

/-- A triple gives an `fspec`. `step` uses it to apply a `spec` theorem to an `fspec` goal. -/
theorem spec_fspec (α) (x : Result α) (p : Post α) : spec x p → fspec x p := by
  intro h; grind[fspec_iff]


/-- If `m` returns, an `fspec` of `m` gives the triple. -/
theorem fspec_to_spec {α} {m : Result α} {p : Post α} (h_ok : ∃ r, m = ok r)
  (h : fspec m p) : spec m p := by grind

/-- A triple of `m` with any postcondition and an `fspec` of `m` give the triple with the
postcondition of the `fspec`. -/
theorem spec_of_fspec {α} {m : Result α} {p q : Post α} (h_ret : spec m q) (h : fspec m p) :
    spec m p :=
  let ⟨r, hr⟩ : ∃ r, m = ok r := by
    cases m using Result.cases with
    | ret x => exact ⟨x, rfl⟩
    | vis e k => simp at h_ret
    | div => simp at h_ret
  fspec_to_spec ⟨r, hr⟩ h

/-- A triple that assumes that `m` returns gives an `fspec` of `m`. -/
theorem fspec_of_spec {α} {m : Result α} {p : Post α}
  (h : (∃ r, m = ok r) → spec m p) : fspec m p := by apply fspec_iff.mpr; grind

/-!
## Hoare triple notation

`m ⦃ r => p ⦄fail` is `fspec m (fun r => p)`. The binders are those of `m ⦃ r => p ⦄`.
-/

scoped syntax:54 term:55 " ⦃ " term+ " => " term " ⦄fail" : term
scoped syntax:54 term:55 " ⦃ " term " ⦄fail" : term

/-- `fspec% t` elaborates `t`, a triple `m ⦃ … ⦄`, as an `fspec`. -/
scoped syntax "fspec% " term : term

open Lean Elab Term in
elab_rules : term
  | `(fspec% $t) => do
    let e ← instantiateMVars (← elabTerm t (some (mkSort 0)))
    unless e.isAppOfArity ``spec 3 do
      throwError "not a triple"
    return mkAppN (mkConst ``fspec e.getAppFn.constLevels!) e.getAppArgs

macro_rules
  | `($e ⦃ $x => $p ⦄fail) => `(fspec% ($e ⦃ $x => $p ⦄))
  | `($e ⦃ $p ⦄fail) => `(fspec% ($e ⦃ $p ⦄))

open Lean PrettyPrinter Delaborator SubExpr in
/-- Delaborator for `fspec e post` → `e ⦃ binders => body ⦄fail`. -/
@[scoped delab app.Lusterna.Spec.fspec]
meta def delabFSpec : Delab := do
  let e ← getExpr
  guard <| e.isAppOfArity ``fspec 3
  let asSpec := mkAppN (mkConst ``spec e.getAppFn.constLevels!) e.getAppArgs
  let stx ← withTheReader SubExpr (fun s => { s with expr := asSpec }) delab
  match stx with
  | `($m ⦃ $x $xs:term* => $p ⦄) => `($m ⦃ $x $xs:term* => $p ⦄fail)
  | `($m ⦃ $p ⦄) => `($m ⦃ $p ⦄fail)
  | _ => failure

#register_spec_info {
    spec_name := ``Lusterna.Spec.fspec
    arity := 3
    program_index := 1
    post_index := 2
    mk_spec_mono := ``Lusterna.Spec.fspec_mono
    mk_spec_mono_skip_args := 2
    mk_spec_bind := ``Lusterna.Spec.fspec_bind
    mk_spec_bind_skip_args := 4
    prepare_intro_outputs := ``Aeneas.Step.prepareIntroOutputs
    to_mvcgen := .none
    liftings := #[
      { from_statement := ``Std.WP.spec
        conversion_thm := ``Lusterna.Spec.spec_fspec
        conversion_thm_inferred_args := 3 }
    ]
  }

end Lusterna.Spec
