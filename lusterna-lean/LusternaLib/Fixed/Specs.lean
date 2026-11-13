module
public import LusternaLib.Fixed.Specs.Basic
public import LusternaLib.Fixed.Specs.Add
public import LusternaLib.Fixed.Specs.Sub
public import LusternaLib.Fixed.Specs.Mul
public import LusternaLib.Fixed.Specs.Div
public import LusternaLib.Fixed.Specs.Rem
public import LusternaLib.Fixed.Specs.IntOps
public import LusternaLib.Fixed.Specs.Round
public import LusternaLib.Fixed.Specs.Cmp
public import LusternaLib.Fixed.Specs.Checked
public import LusternaLib.Fixed.Specs.Convert
public import LusternaLib.Fixed.Specs.ToFixed
public import LusternaLib.Fixed.Specs.FromFixed

/-!
# `@[step]` theorems of `fixed::types::U68F60`

For each item of `LusternaLib.Fixed.Glue` that can fail, a `spec` theorem whose hypotheses rule
out the failure, and an `fspec` theorem with those hypotheses in its postcondition, as in
`LusternaSpec.FSpecScalar`. For each item that cannot fail, a `spec` theorem. The postconditions
are about the bits of the numbers.

An `fspec` theorem of an item whose failure depends on the overflow settings holds only if the
setting makes the item fail, and asks for that as an instance `Fact (s.overflowChecks = true)` or
`Fact (s.debugAssertions = true)`. For the instance of `OverflowSettings` of an extracted crate,
instance search proves it with `Lusterna.Fixed.factBoolEq`.

Each module holds the theorems about the model and the `@[step]` theorems of the items that use
it:

* `Basic`: facts about the model that the other modules use.
* `Add`, `Sub`, `Mul`, `Div`, `Rem`: the arithmetic operators.
* `IntOps`: multiplication and division by a `u128`.
* `Round`: `int`, `floor`, `frac`, `ceil` and `round`.
* `Cmp`: the comparisons.
* `Checked`: the checked and saturating operations.
* `Convert`: the conversions from and to bits, from `bool` and the unsigned integers, and of
  `U68F60` to itself.
* `ToFixed`, `FromFixed`: the conversions from and to the integer types.
-/
