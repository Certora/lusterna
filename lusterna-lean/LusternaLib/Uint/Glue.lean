module
public import LusternaLib.Uint.Model

/-!
# The opaque items of `uint`

Each function of `lusterna-stubs`'s `uint` that Aeneas leaves opaque, under
the name and with the type that Aeneas gives it, defined from the model in
`LusternaLib.Uint.Model`. The methods and trait impls that `construct_uint!`
writes into the crate call these functions on the limbs, and Aeneas
translates them.

This module does not define `uint.fmt`, which `Display` and `Debug` call.
-/

@[expose] public section

open Aeneas Aeneas.Std Result Lusterna.Uint

def uint.zero (N : Std.Usize) : Result (Array Std.U64 N) :=
  ok (ofNat N 0)

def uint.one (N : Std.Usize) : Result (Array Std.U64 N) :=
  ok (ofNat N 1)

def uint.max_value (N : Std.Usize) : Result (Array Std.U64 N) :=
  ok (ofNat N (modulus N - 1))

def uint.low_u64 {N : Std.Usize} (a : Array Std.U64 N) : Result Std.U64 :=
  ok ⟨BitVec.ofNat _ (val a)⟩

def uint.low_u128 {N : Std.Usize} (a : Array Std.U64 N) : Result Std.U128 :=
  ok ⟨BitVec.ofNat _ (val a)⟩

def uint.is_zero {N : Std.Usize} (a : Array Std.U64 N) : Result Bool :=
  ok (decide (val a = 0))

def uint.bits {N : Std.Usize} (a : Array Std.U64 N) : Result Std.Usize :=
  ok ⟨BitVec.ofNat _ (Nat.size (val a))⟩

def uint.from_u64 (N : Std.Usize) (v : Std.U64) : Result (Array Std.U64 N) :=
  ok (ofNat N v.val)

def uint.from_u128 (N : Std.Usize) (v : Std.U128) : Result (Array Std.U64 N) :=
  ok (ofNat N v.val)

def uint.from_i64 (N : Std.Usize) (v : Std.I64) : Result (Array Std.U64 N) :=
  if v.val < 0 then fail .panic else ok (ofNat N v.val.toNat)

def uint.from_i128 (N : Std.Usize) (v : Std.I128) : Result (Array Std.U64 N) :=
  if v.val < 0 then fail .panic else ok (ofNat N v.val.toNat)

def uint.as_u32 {N : Std.Usize} (a : Array Std.U64 N) : Result Std.U32 :=
  toUScalar .U32 a

def uint.as_u64 {N : Std.Usize} (a : Array Std.U64 N) : Result Std.U64 :=
  toUScalar .U64 a

def uint.as_usize {N : Std.Usize} (a : Array Std.U64 N) : Result Std.Usize :=
  toUScalar .Usize a

def uint.as_u128 {N : Std.Usize} (a : Array Std.U64 N) : Result Std.U128 :=
  toUScalar .U128 a

def uint.try_into_u64 {N : Std.Usize} (a : Array Std.U64 N) :
    Result (core.result.Result Std.U64 Str) :=
  if val a < 2 ^ 64 then ok (.Ok ⟨BitVec.ofNat _ (val a)⟩)
  else ok (.Err (toStr "integer overflow when casting to u64"))

def uint.try_into_u128 {N : Std.Usize} (a : Array Std.U64 N) :
    Result (core.result.Result Std.U128 Str) :=
  if val a < 2 ^ 128 then ok (.Ok ⟨BitVec.ofNat _ (val a)⟩)
  else ok (.Err (toStr "integer overflow when casting to u128"))

def uint.eq {N : Std.Usize} (a b : Array Std.U64 N) : Result Bool :=
  ok (decide (val a = val b))

def uint.cmp {N : Std.Usize} (a b : Array Std.U64 N) : Result Ordering :=
  ok (compare (val a) (val b))

def uint.add {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  Lusterna.Uint.add a b

def uint.sub {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  Lusterna.Uint.sub a b

def uint.mul {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  Lusterna.Uint.mul a b

def uint.mul_u64 {N : Std.Usize} (a : Array Std.U64 N) (b : Std.U64) : Result (Array Std.U64 N) :=
  ofNatOrFail N (val a * b.val)

def uint.div {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) := do
  let (q, _) ← divMod a b
  ok q

def uint.rem {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) := do
  let (_, r) ← divMod a b
  ok r

def uint.div_mod {N : Std.Usize} (a b : Array Std.U64 N) :
    Result ((Array Std.U64 N) × (Array Std.U64 N)) :=
  divMod a b

def uint.shl {N : Std.Usize} (a s : Array Std.U64 N) : Result (Array Std.U64 N) :=
  Lusterna.Uint.shl a s

def uint.shr {N : Std.Usize} (a s : Array Std.U64 N) : Result (Array Std.U64 N) :=
  Lusterna.Uint.shr a s

def uint.overflowing_add {N : Std.Usize} (a b : Array Std.U64 N) :
    Result ((Array Std.U64 N) × Bool) :=
  ok (overflowing N (val a + val b))

def uint.overflowing_sub {N : Std.Usize} (a b : Array Std.U64 N) :
    Result ((Array Std.U64 N) × Bool) :=
  ok (ofNat N (val a + modulus N - val b), decide (val a < val b))

def uint.overflowing_mul {N : Std.Usize} (a b : Array Std.U64 N) :
    Result ((Array Std.U64 N) × Bool) :=
  ok (overflowing N (val a * val b))

def uint.checked_add {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (checked N (val a + val b))

def uint.checked_sub {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (if val b ≤ val a then some (ofNat N (val a - val b)) else none)

def uint.checked_mul {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (checked N (val a * val b))

def uint.checked_div {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (if val b = 0 then none else some (ofNat N (val a / val b)))

def uint.checked_rem {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (if val b = 0 then none else some (ofNat N (val a % val b)))

def uint.checked_pow {N : Std.Usize} (a b : Array Std.U64 N) :
    Result (Option (Array Std.U64 N)) :=
  ok (checked N (val a ^ val b))

def uint.saturating_add {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  ok (saturating N (val a + val b))

def uint.saturating_sub {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  ok (ofNat N (val a - val b))

def uint.saturating_mul {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  ok (saturating N (val a * val b))

def uint.pow {N : Std.Usize} (a b : Array Std.U64 N) : Result (Array Std.U64 N) :=
  ofNatOrFail N (val a ^ val b)

def uint.exp10 (N : Std.Usize) (n : Std.Usize) : Result (Array Std.U64 N) :=
  ofNatOrFail N (10 ^ n.val)

def uint.integer_sqrt {N : Std.Usize} (a : Array Std.U64 N) : Result (Array Std.U64 N) :=
  ok (ofNat N (Nat.sqrt (val a)))
