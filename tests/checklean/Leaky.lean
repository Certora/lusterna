import Aeneas
open Aeneas.Std

/-! Fixture for the TAINT GATE (`lean.target_footprint_gate`, the Python wrapper around
`checkDefAxioms`). Shaped like a real Aeneas translation — a CamelCase module (`LeakyModel`) whose
crate lives in a lowercase `namespace leaky` — so the fixture exercises the same namespace
resolution production hits. It reproduces the same class of leak in miniature: a modelled value type
whose `Display`→`fmt` opaque a target transitively reaches. `verify.py`'s gate fixture drives the real
gate over these and asserts BLOCK / PASS / fail-closed. -/
namespace leaky

/-- Opaque substrate reached only through a Display/fmt path (mimics `core::fmt::Formatter` +
`ValueDisplay.fmt` that an in-place run leaked). -/
axiom Formatter : Type
axiom displayFmt : Nat → Formatter → Nat

/-- A cleanly modelled value surface: empty opaque footprint. -/
abbrev Amount := Nat
def Amount.add (a b : Amount) : Amount := a + b

/-- CLEAN target — only value ops; its footprint is empty. -/
def cleanTarget (x : Nat) : Nat := Amount.add x 1

/-- LEAKY target — reaches the `Display`/`fmt` opaque, like `leakyOp` reaching `ValueDisplay.fmt`. -/
noncomputable def leakyOp (x : Nat) (f : Formatter) : Nat := displayFmt x f + 1

end leaky
