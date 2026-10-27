module
public import Aeneas
public import LusternaLib.OverflowSettings

/-!
# Model of `fixed::types::U68F60`

`U68F60` is an unsigned fixed-point number with 60 fractional bits, stored as
a `U128` of its bits. Its value is `bits / 2^60`.

The behavior on overflow depends on two settings of the package `fixed` in
the target crate, given as an `OverflowSettings`:

* `overflowChecks`: `+` and `-` on `U68F60`, and `*` with a `u128`, fail on
  overflow. Otherwise they wrap.
* `debugAssertions`: `*` and `/` on `U68F60`, the conversions from and to
  integers and the rounding functions fail on overflow. Otherwise they wrap.

A division by zero and an overflow in `const_from_int` always fail.
-/

@[expose] public section

open Aeneas Aeneas.Std Result

/-- `fixed::types::U68F60`. -/
structure fixed.types.U68F60 where
  /-- The bits. The value is `bits / 2^60`. -/
  bits : Std.U128

/-- `fixed::ParseFixedError`. -/
inductive fixed.ParseFixedError where
  | invalidDigit
  | misplacedSign
  | noDigits
  | tooManyPoints
  | overflow
  | expInvalidDigit
  | expNoDigits
  | tooManyExp
  | expOverflow

namespace Lusterna.Fixed

abbrev U68F60 := fixed.types.U68F60

/-- `2^60`, the value of `ONE` in bits. -/
abbrev scale : Nat := 2 ^ 60

/-- The bits as a natural number. -/
def val (a : U68F60) : Nat := a.bits.val

/-- The `U68F60` with the bits `n mod 2^128`. -/
def wrap (n : Nat) : U68F60 := ⟨⟨BitVec.ofNat _ n⟩⟩

/-- `wrap n`, or a failure if `flag` is set and `n` does not fit. -/
def wrapOrFail (flag : Bool) (n : Nat) : Result U68F60 :=
  if n < 2 ^ 128 ∨ flag = false then ok (wrap n) else fail .integerOverflow

def zero : U68F60 := wrap 0
def one : U68F60 := wrap scale
def delta : U68F60 := wrap 1
def maxValue : U68F60 := wrap (2 ^ 128 - 1)

/-- `self + rhs` -/
def add (c : OverflowSettings) (a b : U68F60) : Result U68F60 :=
  wrapOrFail c.overflowChecks (val a + val b)

/-- `self - rhs` -/
def sub (c : OverflowSettings) (a b : U68F60) : Result U68F60 :=
  if val b ≤ val a then ok (wrap (val a - val b))
  else if c.overflowChecks then fail .integerOverflow
  else ok (wrap (val a + 2 ^ 128 - val b))

/-- `self * rhs`, rounded down. -/
def mul (c : OverflowSettings) (a b : U68F60) : Result U68F60 :=
  wrapOrFail c.debugAssertions (val a * val b / scale)

/-- `self / rhs`, rounded down. Fails if `rhs` is zero. -/
def div (c : OverflowSettings) (a b : U68F60) : Result U68F60 :=
  if val b = 0 then fail .divisionByZero
  else wrapOrFail c.debugAssertions (val a * scale / val b)

/-- `self % rhs`. Fails if `rhs` is zero. -/
def rem (a b : U68F60) : Result U68F60 :=
  if val b = 0 then fail .divisionByZero else ok (wrap (val a % val b))

/-- `self * rhs` for an integer `rhs`. -/
def mulInt (c : OverflowSettings) (a : U68F60) (n : Std.U128) : Result U68F60 :=
  wrapOrFail c.overflowChecks (val a * n.val)

/-- `self / rhs` for an integer `rhs`, rounded down. Fails if `rhs` is zero. -/
def divInt (a : U68F60) (n : Std.U128) : Result U68F60 :=
  if n.val = 0 then fail .divisionByZero else ok (wrap (val a / n.val))

/-- `const_from_int`: the number with the value `n`. Fails if it does not
fit. -/
def constFromInt (n : Std.U128) : Result U68F60 :=
  if n.val < 2 ^ 68 then ok (wrap (n.val * scale)) else fail .panic

/-- The integer part. -/
def int (a : U68F60) : U68F60 := wrap (val a / scale * scale)

/-- The fractional part. -/
def frac (a : U68F60) : U68F60 := wrap (val a % scale)

/-- Rounded up. -/
def ceil (c : OverflowSettings) (a : U68F60) : Result U68F60 :=
  if val a % scale = 0 then ok (int a)
  else wrapOrFail c.debugAssertions (val (int a) + scale)

/-- Rounded to the nearest, with ties away from zero. -/
def round (c : OverflowSettings) (a : U68F60) : Result U68F60 :=
  if val a % scale < scale / 2 then ok (int a)
  else wrapOrFail c.debugAssertions (val (int a) + scale)

/-- `wrap n`, or `none` if `n` does not fit. -/
def checked (n : Nat) : Option U68F60 :=
  if n < 2 ^ 128 then some (wrap n) else none

def checkedAdd (a b : U68F60) : Option U68F60 := checked (val a + val b)

def checkedSub (a b : U68F60) : Option U68F60 :=
  if val b ≤ val a then some (wrap (val a - val b)) else none

def checkedMul (a b : U68F60) : Option U68F60 := checked (val a * val b / scale)

def checkedDiv (a b : U68F60) : Option U68F60 :=
  if val b = 0 then none else checked (val a * scale / val b)

def saturatingAdd (a b : U68F60) : U68F60 := wrap (min (val a + val b) (2 ^ 128 - 1))

def saturatingSub (a b : U68F60) : U68F60 := wrap (val a - val b)

def saturatingMul (a b : U68F60) : U68F60 :=
  wrap (min (val a * val b / scale) (2 ^ 128 - 1))

def absDiff (a b : U68F60) : U68F60 := wrap (max (val a) (val b) - min (val a) (val b))

/-- The number with the value of the natural number `n`. -/
def ofNat (c : OverflowSettings) (n : Nat) : Result U68F60 :=
  if n < 2 ^ 68 ∨ c.debugAssertions = false then ok (wrap (n * scale))
  else fail .integerOverflow

def checkedOfNat (n : Nat) : Option U68F60 :=
  if n < 2 ^ 68 then some (wrap (n * scale)) else none

/-- The number with the value of the integer `n`. -/
def ofInt (c : OverflowSettings) (n : Int) : Result U68F60 :=
  if 0 ≤ n ∧ n < 2 ^ 68 then ok (wrap (n.toNat * scale))
  else if c.debugAssertions then fail .integerOverflow
  else ok ⟨⟨BitVec.ofInt _ (n * (scale : Int))⟩⟩

def checkedOfInt (n : Int) : Option U68F60 :=
  if 0 ≤ n ∧ n < 2 ^ 68 then some (wrap (n.toNat * scale)) else none

/-- The integer part as a `UScalar ty`. -/
def toUScalar (c : OverflowSettings) (ty : UScalarTy) (a : U68F60) : Result (UScalar ty) :=
  let i := val a / scale
  if i < 2 ^ ty.numBits ∨ c.debugAssertions = false then ok ⟨BitVec.ofNat _ i⟩
  else fail .integerOverflow

def checkedToUScalar (ty : UScalarTy) (a : U68F60) : Option (UScalar ty) :=
  let i := val a / scale
  if i < 2 ^ ty.numBits then some ⟨BitVec.ofNat _ i⟩ else none

def saturatingToUScalar (ty : UScalarTy) (a : U68F60) : UScalar ty :=
  ⟨BitVec.ofNat _ (min (val a / scale) (2 ^ ty.numBits - 1))⟩

/-- The integer part as an `IScalar ty`. -/
def toIScalar (c : OverflowSettings) (ty : IScalarTy) (a : U68F60) : Result (IScalar ty) :=
  let i := val a / scale
  if i < 2 ^ (ty.numBits - 1) ∨ c.debugAssertions = false then ok ⟨BitVec.ofNat _ i⟩
  else fail .integerOverflow

def checkedToIScalar (ty : IScalarTy) (a : U68F60) : Option (IScalar ty) :=
  let i := val a / scale
  if i < 2 ^ (ty.numBits - 1) then some ⟨BitVec.ofNat _ i⟩ else none

def saturatingToIScalar (ty : IScalarTy) (a : U68F60) : IScalar ty :=
  ⟨BitVec.ofNat _ (min (val a / scale) (2 ^ (ty.numBits - 1) - 1))⟩

end Lusterna.Fixed
