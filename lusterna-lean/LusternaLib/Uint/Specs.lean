module
public import LusternaLib.Uint.Specs.Basic
public import LusternaLib.Uint.Specs.Arith
public import LusternaLib.Uint.Specs.Checked
public import LusternaLib.Uint.Specs.Convert

/-!
# `@[step]` theorems of the numbers of `uint`

For each item of `LusternaLib.Uint.Glue` that can fail, a `spec` theorem whose hypotheses rule
out the failure, and an `fspec` theorem with those hypotheses in its postcondition, as in
`LusternaSpec.FSpecScalar`. For each item that cannot fail, a `spec` theorem. The postconditions
are about the values `Lusterna.Uint.val` of the numbers.

* `Basic`: the value of `ofNat`, the bound of a value, and `ofNatOrFail`.
* `Arith`: `add`, `sub`, `mul`, `mul_u64`, `div`, `rem` and `div_mod`.
* `Checked`: the checked operations.
* `Convert`: the conversions from and to `u64` and `u128`, and the comparisons.
-/
