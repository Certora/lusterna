module
import LusternaLib

/-!
# LusternaLibTest

Regression tests for LusternaLib. Each `example` passes iff the file
type-checks.
-/

open Aeneas.Std

/-- `deref_mut` drops the write: the back function returns the old guard. -/
example (r : core.cell.RefMut Nat) :
    core.cell.RefMut.Insts.CoreOpsDerefDerefMut.deref_mut r =
      .ok (r.val, fun _ => r, fun _ => r) := rfl

/-- `load_mut` on a loader that is not writable returns `AccountNotMutable`. -/
example (l : anchor_lang.accounts.account_loader.AccountLoader Nat)
    (h : l.loader.isWritable = false) :
    Lusterna.Anchor.AccountLoader.loadMut id l =
      .ok (.Err .accountNotMutable, fun _ => ()) := by
  simp [Lusterna.Anchor.AccountLoader.loadMut,
    Solanalib.Anchor.AccountLoader.loadMut, h]

/-- `MAX + 1` on `U256` panics. -/
example :
    uint.add (Lusterna.Uint.ofNat 4#usize (2 ^ 256 - 1)) (Lusterna.Uint.ofNat 4#usize 1) =
      .fail .panic := rfl

/-- `0.overflowing_sub(1)` on `U256` wraps to `MAX` and reports the overflow. -/
example :
    uint.overflowing_sub (Lusterna.Uint.ofNat 4#usize 0) (Lusterna.Uint.ofNat 4#usize 1) =
      .ok (Lusterna.Uint.ofNat 4#usize (2 ^ 256 - 1), true) := rfl

/-- `checked_mul` on `U256` returns `None` on overflow. -/
example :
    uint.checked_mul (Lusterna.Uint.ofNat 4#usize (2 ^ 128)) (Lusterna.Uint.ofNat 4#usize (2 ^ 128)) =
      .ok none := rfl

/-- Division by zero on `U256` panics. -/
example :
    uint.div (Lusterna.Uint.ofNat 4#usize 1) (Lusterna.Uint.ofNat 4#usize 0) = .fail .panic := by
  have h : Lusterna.Uint.val (Lusterna.Uint.ofNat 4#usize 0) = 0 := by decide +kernel
  simp [uint.div, Lusterna.Uint.divMod, h]

/-- `as_u64` on `U256` panics on `2^64`. -/
example : uint.as_u64 (Lusterna.Uint.ofNat 4#usize (2 ^ 64)) = .fail .panic := rfl

/-- `ofNat` keeps every value below `2^256`. -/
example : Lusterna.Uint.val (Lusterna.Uint.ofNat 4#usize (2 ^ 256 - 1)) = 2 ^ 256 - 1 := by
  decide +kernel

/-- `?` on `none` breaks with the residual `none`, which converts back to `none`. -/
example :
    core.option.Option.Insts.CoreOpsTry_traitTry.branch (none : Option Nat) = .ok (.Break none) ∧
    core.option.Option.Insts.CoreOpsTry_traitFromResidualOptionNever.from_residual Nat none =
      .ok none := ⟨rfl, rfl⟩

/-- `unwrap_or_default` on `none` returns the default value. -/
example :
    core.option.Option.unwrap_or_default { default := .ok false } (none : Option Bool) =
      .ok false := rfl

/-- `Result::ok` drops the error, and `is_err` sees it. -/
example :
    core.result.Result.ok (.Err 1 : core.result.Result Nat Nat) = .ok none ∧
    core.result.Result.is_err (.Err 1 : core.result.Result Nat Nat) = .ok true := ⟨rfl, rfl⟩

/-- `u64::try_from(u128)` fails above `u64::MAX`. -/
example : U64.Insts.CoreConvertTryFromU128TryFromIntError.try_from 18446744073709551616#u128 =
    .ok (.Err ()) := by
  simp [U64.Insts.CoreConvertTryFromU128TryFromIntError.try_from, core.num.tryFromUScalar,
    U64.max_eq]

/-- `u64::try_from(i64)` fails on a negative value. -/
example : U64.Insts.CoreConvertTryFromI64TryFromIntError.try_from (-1)#i64 = .ok (.Err ()) := by
  simp [U64.Insts.CoreConvertTryFromI64TryFromIntError.try_from, core.num.tryFromIScalarToUScalar]

/-- `saturating_mul` stops at `MAX` for unsigned and at `MIN` for signed types. -/
example : core.num.U8.saturating_mul 16#u8 16#u8 = .ok 255#u8 := by
  simp [core.num.U8.saturating_mul, UScalar.saturating_mul, U8.max_eq]
  decide
example : core.num.I8.saturating_mul (-16)#i8 16#i8 = .ok (-128)#i8 := by
  simp [core.num.I8.saturating_mul, IScalar.saturating_mul, I8.max_eq, I8.min_eq]
  decide

/-- `max_value` is `MAX`. -/
example : core.num.U64.max_value = .ok core.num.U64.MAX := rfl
