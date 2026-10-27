module
public import LusternaLib.Fixed.Model

/-!
# The opaque items of `fixed::types::U68F60`

Each item of `lusterna-stubs`'s `fixed` that Aeneas leaves opaque, under the
name and with the type that Aeneas gives it, defined from the model in
`LusternaLib.Fixed.Model`. The items whose behavior on overflow depends
on the overflow settings take them from the instance of
`Lusterna.OverflowSettings`.

This module does not define these items. The crate's `FunsExternal.lean`
defines them or keeps them as axioms:

* `from_num`, `checked_from_num`, `to_num`, `checked_to_num` and
  `saturating_to_num`. Their types name `fixed.traits.ToFixed` and
  `fixed.traits.FromFixed`, which Aeneas writes into the crate's own
  `Types.lean`. Each one calls the `to_fixed`, `checked_to_fixed`,
  `from_fixed`, `checked_from_fixed` or `saturating_from_fixed` of the trait
  instance that it gets.
* `Display` and `Debug`.
* `from_str` and `Sum`.
-/

@[expose] public section

open Aeneas Aeneas.Std Result

def fixed.types.U68F60.ZERO : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.zero

def fixed.types.U68F60.ONE : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.one

def fixed.types.U68F60.DELTA : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.delta

def fixed.types.U68F60.MIN : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.zero

def fixed.types.U68F60.MAX : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.maxValue

def fixed.types.U68F60.INT_NBITS : Result Std.U32 :=
  ok 68#u32

def fixed.types.U68F60.FRAC_NBITS : Result Std.U32 :=
  ok 60#u32

def fixed.types.U68F60.from_bits (n : Std.U128) : Result fixed.types.U68F60 :=
  ok ⟨n⟩

def fixed.types.U68F60.to_bits (a : fixed.types.U68F60) : Result Std.U128 :=
  ok a.bits

def fixed.types.U68F60.const_from_int (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.constFromInt n

def fixed.types.U68F60.unwrapped_div_int (a : fixed.types.U68F60) (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.divInt a n

def fixed.types.U68F60.int (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.int a)

def fixed.types.U68F60.frac (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.frac a)

def fixed.types.U68F60.is_zero (a : fixed.types.U68F60) : Result Bool :=
  ok (decide (Lusterna.Fixed.val a = 0))

def fixed.types.U68F60.floor (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.int a)

def fixed.types.U68F60.ceil [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ceil s a

def fixed.types.U68F60.round [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.round s a

def fixed.types.U68F60.checked_add (a b : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedAdd a b)

def fixed.types.U68F60.checked_sub (a b : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedSub a b)

def fixed.types.U68F60.checked_mul (a b : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedMul a b)

def fixed.types.U68F60.checked_div (a b : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedDiv a b)

def fixed.types.U68F60.saturating_add (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.saturatingAdd a b)

def fixed.types.U68F60.saturating_sub (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.saturatingSub a b)

def fixed.types.U68F60.saturating_mul (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.saturatingMul a b)

def fixed.types.U68F60.abs_diff (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.absDiff a b)

def fixed.types.U68F60.Insts.CoreOpsArithAddU68F60U68F60.add [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def fixed.types.U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def Shared0U68F60.Insts.CoreOpsArithAddU68F60U68F60.add [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def Shared1U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def fixed.types.U68F60.Insts.CoreOpsArithAddAssignU68F60.add_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def fixed.types.U68F60.Insts.CoreOpsArithAddAssignShared0U68F60.add_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.add s a b

def fixed.types.U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def fixed.types.U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def Shared0U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def Shared1U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def fixed.types.U68F60.Insts.CoreOpsArithSubAssignU68F60.sub_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def fixed.types.U68F60.Insts.CoreOpsArithSubAssignShared0U68F60.sub_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.sub s a b

def fixed.types.U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def fixed.types.U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def Shared0U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def Shared1U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def fixed.types.U68F60.Insts.CoreOpsArithMulAssignU68F60.mul_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def fixed.types.U68F60.Insts.CoreOpsArithMulAssignShared0U68F60.mul_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mul s a b

def fixed.types.U68F60.Insts.CoreOpsArithDivU68F60U68F60.div [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def fixed.types.U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def Shared0U68F60.Insts.CoreOpsArithDivU68F60U68F60.div [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def Shared1U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def fixed.types.U68F60.Insts.CoreOpsArithDivAssignU68F60.div_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def fixed.types.U68F60.Insts.CoreOpsArithDivAssignShared0U68F60.div_assign [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.div s a b

def fixed.types.U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.rem a b

def fixed.types.U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.rem a b

def Shared0U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.rem a b

def Shared1U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem (a b : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.rem a b

def fixed.types.U68F60.Insts.CoreOpsArithMulU128U68F60.mul [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mulInt s a n

def fixed.types.U68F60.Insts.CoreOpsArithDivU128U68F60.div (a : fixed.types.U68F60) (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.divInt a n

def fixed.types.U68F60.Insts.CoreOpsArithMulAssignU128.mul_assign [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mulInt s a n

def fixed.types.U68F60.Insts.CoreOpsArithDivAssignU128.div_assign (a : fixed.types.U68F60) (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.divInt a n

def U128.Insts.CoreOpsArithMulU68F60U68F60.mul [s : Lusterna.OverflowSettings] (n : Std.U128) (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.mulInt s a n

def fixed.types.U68F60.Insts.CoreCloneClone.clone (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok a

def fixed.types.U68F60.Insts.CoreDefaultDefault.default : Result fixed.types.U68F60 :=
  ok Lusterna.Fixed.zero

def fixed.types.U68F60.Insts.CoreCmpPartialEqU68F60.eq (a b : fixed.types.U68F60) : Result Bool :=
  ok (decide (Lusterna.Fixed.val a = Lusterna.Fixed.val b))

def fixed.types.U68F60.Insts.CoreCmpPartialOrdU68F60.partial_cmp (a b : fixed.types.U68F60) : Result (Option Ordering) :=
  ok (some (compare (Lusterna.Fixed.val a) (Lusterna.Fixed.val b)))

def fixed.types.U68F60.Insts.CoreCmpOrd.cmp (a b : fixed.types.U68F60) : Result Ordering :=
  ok (compare (Lusterna.Fixed.val a) (Lusterna.Fixed.val b))

def fixed.types.U68F60.Insts.CoreCmpPartialEqU64.eq (a : fixed.types.U68F60) (n : Std.U64) : Result Bool :=
  ok (decide (Lusterna.Fixed.val a = n.val * Lusterna.Fixed.scale))

def fixed.types.U68F60.Insts.CoreCmpPartialOrdU64.partial_cmp (a : fixed.types.U68F60) (n : Std.U64) : Result (Option Ordering) :=
  ok (some (compare (Lusterna.Fixed.val a) (n.val * Lusterna.Fixed.scale)))

def fixed.types.U68F60.Insts.CoreConvertFromBool.from (b : Bool) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.wrap (if b then Lusterna.Fixed.scale else 0))

def fixed.types.U68F60.Insts.CoreConvertFromU8.from (n : Std.U8) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.wrap (n.val * Lusterna.Fixed.scale))

def fixed.types.U68F60.Insts.CoreConvertFromU16.from (n : Std.U16) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.wrap (n.val * Lusterna.Fixed.scale))

def fixed.types.U68F60.Insts.CoreConvertFromU32.from (n : Std.U32) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.wrap (n.val * Lusterna.Fixed.scale))

def fixed.types.U68F60.Insts.CoreConvertFromU64.from (n : Std.U64) : Result fixed.types.U68F60 :=
  ok (Lusterna.Fixed.wrap (n.val * Lusterna.Fixed.scale))

def fixed.types.U68F60.Insts.FixedTraitsToFixed.to_fixed (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok a

def fixed.types.U68F60.Insts.FixedTraitsToFixed.checked_to_fixed (a : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (some a)

def fixed.types.U68F60.Insts.FixedTraitsFromFixed.from_fixed (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok a

def fixed.types.U68F60.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option fixed.types.U68F60) :=
  ok (some a)

def fixed.types.U68F60.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result fixed.types.U68F60 :=
  ok a

def U8.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.U8) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def U8.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.U8) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def U8.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.U8 :=
  Lusterna.Fixed.toUScalar s .U8 a

def U8.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.U8) :=
  ok (Lusterna.Fixed.checkedToUScalar .U8 a)

def U8.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.U8 :=
  ok (Lusterna.Fixed.saturatingToUScalar .U8 a)

def U16.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.U16) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def U16.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.U16) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def U16.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.U16 :=
  Lusterna.Fixed.toUScalar s .U16 a

def U16.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.U16) :=
  ok (Lusterna.Fixed.checkedToUScalar .U16 a)

def U16.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.U16 :=
  ok (Lusterna.Fixed.saturatingToUScalar .U16 a)

def U32.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.U32) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def U32.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.U32) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def U32.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.U32 :=
  Lusterna.Fixed.toUScalar s .U32 a

def U32.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.U32) :=
  ok (Lusterna.Fixed.checkedToUScalar .U32 a)

def U32.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.U32 :=
  ok (Lusterna.Fixed.saturatingToUScalar .U32 a)

def U64.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.U64) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def U64.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.U64) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def U64.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.U64 :=
  Lusterna.Fixed.toUScalar s .U64 a

def U64.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.U64) :=
  ok (Lusterna.Fixed.checkedToUScalar .U64 a)

def U64.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.U64 :=
  ok (Lusterna.Fixed.saturatingToUScalar .U64 a)

def U128.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.U128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def U128.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.U128) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def U128.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.U128 :=
  Lusterna.Fixed.toUScalar s .U128 a

def U128.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.U128) :=
  ok (Lusterna.Fixed.checkedToUScalar .U128 a)

def U128.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.U128 :=
  ok (Lusterna.Fixed.saturatingToUScalar .U128 a)

def Usize.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.Usize) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofNat s n.val

def Usize.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.Usize) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfNat n.val)

def Usize.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.Usize :=
  Lusterna.Fixed.toUScalar s .Usize a

def Usize.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.Usize) :=
  ok (Lusterna.Fixed.checkedToUScalar .Usize a)

def Usize.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.Usize :=
  ok (Lusterna.Fixed.saturatingToUScalar .Usize a)

def I8.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.I8) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def I8.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.I8) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def I8.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.I8 :=
  Lusterna.Fixed.toIScalar s .I8 a

def I8.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.I8) :=
  ok (Lusterna.Fixed.checkedToIScalar .I8 a)

def I8.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.I8 :=
  ok (Lusterna.Fixed.saturatingToIScalar .I8 a)

def I16.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.I16) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def I16.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.I16) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def I16.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.I16 :=
  Lusterna.Fixed.toIScalar s .I16 a

def I16.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.I16) :=
  ok (Lusterna.Fixed.checkedToIScalar .I16 a)

def I16.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.I16 :=
  ok (Lusterna.Fixed.saturatingToIScalar .I16 a)

def I32.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.I32) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def I32.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.I32) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def I32.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.I32 :=
  Lusterna.Fixed.toIScalar s .I32 a

def I32.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.I32) :=
  ok (Lusterna.Fixed.checkedToIScalar .I32 a)

def I32.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.I32 :=
  ok (Lusterna.Fixed.saturatingToIScalar .I32 a)

def I64.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.I64) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def I64.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.I64) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def I64.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.I64 :=
  Lusterna.Fixed.toIScalar s .I64 a

def I64.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.I64) :=
  ok (Lusterna.Fixed.checkedToIScalar .I64 a)

def I64.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.I64 :=
  ok (Lusterna.Fixed.saturatingToIScalar .I64 a)

def I128.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.I128) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def I128.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.I128) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def I128.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.I128 :=
  Lusterna.Fixed.toIScalar s .I128 a

def I128.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.I128) :=
  ok (Lusterna.Fixed.checkedToIScalar .I128 a)

def I128.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.I128 :=
  ok (Lusterna.Fixed.saturatingToIScalar .I128 a)

def Isize.Insts.FixedTraitsToFixed.to_fixed [s : Lusterna.OverflowSettings] (n : Std.Isize) : Result fixed.types.U68F60 :=
  Lusterna.Fixed.ofInt s n.val

def Isize.Insts.FixedTraitsToFixed.checked_to_fixed (n : Std.Isize) : Result (Option fixed.types.U68F60) :=
  ok (Lusterna.Fixed.checkedOfInt n.val)

def Isize.Insts.FixedTraitsFromFixed.from_fixed [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) : Result Std.Isize :=
  Lusterna.Fixed.toIScalar s .Isize a

def Isize.Insts.FixedTraitsFromFixed.checked_from_fixed (a : fixed.types.U68F60) : Result (Option Std.Isize) :=
  ok (Lusterna.Fixed.checkedToIScalar .Isize a)

def Isize.Insts.FixedTraitsFromFixed.saturating_from_fixed (a : fixed.types.U68F60) : Result Std.Isize :=
  ok (Lusterna.Fixed.saturatingToIScalar .Isize a)

