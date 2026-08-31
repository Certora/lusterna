import Aeneas
open Aeneas.Std

/-! Fixture for the TAINT GATE (`lean.target_footprint_gate`, the Python wrapper around
`checkDefAxioms`). Shaped like a real Aeneas translation — a CamelCase module (`KlendishModel`) whose
crate lives in a lowercase `namespace klendish` — so the fixture exercises the same namespace
resolution production hits. It reproduces the exact klend leak in miniature: a modelled value type
whose `Display`→`fmt` opaque a target transitively reaches. `verify.py`'s gate fixture drives the real
gate over these and asserts BLOCK / PASS / fail-closed. -/
namespace klendish

/-- Opaque substrate reached only through a Display/fmt path (mimics `core::fmt::Formatter` +
`FractionDisplay.fmt` that klend's in-place run leaked). -/
axiom Formatter : Type
axiom fracDisplayFmt : Nat → Formatter → Nat

/-- A cleanly modelled value surface: empty opaque footprint. -/
abbrev Fraction := Nat
def Fraction.add (a b : Fraction) : Fraction := a + b

/-- CLEAN target — only value ops; its footprint is empty. -/
def total_supply (x : Nat) : Nat := Fraction.add x 1

/-- LEAKY target — reaches the `Display`/`fmt` opaque, like `repay` reaching `FractionDisplay.fmt`. -/
noncomputable def repay (x : Nat) (f : Formatter) : Nat := fracDisplayFmt x f + 1

end klendish
