module
public import Aeneas

/-!
# Model of the numbers of `construct_uint!` of `uint`

A number with `n` limbs is an `Array Std.U64 n` of its limbs, with the lowest
limb first. Its value is `val a`, which is below `2^(64 n)`.

`uint` panics on overflow, whatever the overflow settings of the build. The
macro `construct_uint!` gives every number at least one limb.
-/

@[expose] public section

open Aeneas Aeneas.Std Result

namespace Lusterna.Uint

/-- The value of the limbs `l`, with the lowest limb first. -/
def limbsVal : List Std.U64 → Nat
  | [] => 0
  | x :: l => x.val + 2 ^ 64 * limbsVal l

/-- The value. -/
def val {n : Std.Usize} (a : Array Std.U64 n) : Nat := limbsVal a.val

/-- `2^(64 n)`, the number of values with `n` limbs. -/
def modulus (n : Std.Usize) : Nat := 2 ^ (64 * n.val)

/-- The number with `n` limbs and the value `m mod 2^(64 n)`. -/
def ofNat (n : Std.Usize) (m : Nat) : Array Std.U64 n :=
  Array.from (List.ofFn fun (i : Fin n.val) => (⟨BitVec.ofNat _ (m / 2 ^ (64 * i.val))⟩ : Std.U64))
    (by simp)

/-- `ofNat n m`, or a panic if `m` does not fit. -/
def ofNatOrFail (n : Std.Usize) (m : Nat) : Result (Array Std.U64 n) :=
  if m < modulus n then ok (ofNat n m) else fail .panic

/-- `ofNat n m`, or `none` if `m` does not fit. -/
def checked (n : Std.Usize) (m : Nat) : Option (Array Std.U64 n) :=
  if m < modulus n then some (ofNat n m) else none

/-- `ofNat n m`, or the largest value if `m` does not fit. -/
def saturating (n : Std.Usize) (m : Nat) : Array Std.U64 n :=
  ofNat n (min m (modulus n - 1))

/-- `ofNat n m` and whether `m` does not fit. -/
def overflowing (n : Std.Usize) (m : Nat) : Array Std.U64 n × Bool :=
  (ofNat n m, decide (modulus n ≤ m))

/-- The value as an unsigned integer of type `ty`, or a panic if it does not
fit. -/
def toUScalar (ty : UScalarTy) {n : Std.Usize} (a : Array Std.U64 n) : Result (UScalar ty) :=
  if val a < 2 ^ ty.numBits then ok ⟨BitVec.ofNat _ (val a)⟩ else fail .panic

/-- `self + other`. Panics on overflow. -/
def add {n : Std.Usize} (a b : Array Std.U64 n) : Result (Array Std.U64 n) :=
  ofNatOrFail n (val a + val b)

/-- `self - other`. Panics if `other` is larger. -/
def sub {n : Std.Usize} (a b : Array Std.U64 n) : Result (Array Std.U64 n) :=
  if val b ≤ val a then ok (ofNat n (val a - val b)) else fail .panic

/-- `self * other`. Panics on overflow. -/
def mul {n : Std.Usize} (a b : Array Std.U64 n) : Result (Array Std.U64 n) :=
  ofNatOrFail n (val a * val b)

/-- `self / other` and `self % other`. Panics if `other` is zero. -/
def divMod {n : Std.Usize} (a b : Array Std.U64 n) :
    Result (Array Std.U64 n × Array Std.U64 n) :=
  if val b = 0 then fail .panic else ok (ofNat n (val a / val b), ofNat n (val a % val b))

/-- `self << shift`. The bits above the highest limb are lost. Panics if
`shift` does not fit in a `usize`. -/
def shl {n : Std.Usize} (a s : Array Std.U64 n) : Result (Array Std.U64 n) :=
  if val s < 2 ^ UScalarTy.Usize.numBits then ok (ofNat n (val a * 2 ^ val s)) else fail .panic

/-- `self >> shift`. Panics if `shift` does not fit in a `usize`. -/
def shr {n : Std.Usize} (a s : Array Std.U64 n) : Result (Array Std.U64 n) :=
  if val s < 2 ^ UScalarTy.Usize.numBits then ok (ofNat n (val a / 2 ^ val s)) else fail .panic

end Lusterna.Uint
