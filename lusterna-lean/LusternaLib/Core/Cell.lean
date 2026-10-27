module
public import Aeneas

/-!
# `core::cell::Ref` and `core::cell::RefMut`

The guards that `RefCell::borrow` and `RefCell::borrow_mut` return, with the
names Aeneas gives them. A guard holds the borrowed value.

Aeneas translates a method that takes `&self` into a function that returns
no new `self`, so a write through a `RefMut` has no path back to the
`RefCell`. `RefMut`'s `deref_mut` here returns the value, and its back
functions drop the new value.
-/

@[expose] public section

open Aeneas.Std

/-- `core::cell::Ref<'_, T>`. -/
structure core.cell.Ref (T : Type) where
  /-- The borrowed value. -/
  val : T

/-- `core::cell::RefMut<'_, T>`. -/
structure core.cell.RefMut (T : Type) where
  /-- The borrowed value. -/
  val : T

/-- `<Ref<'_, T> as Deref>::deref`. -/
def core.cell.Ref.Insts.CoreOpsDerefDeref.deref {T : Type} (r : core.cell.Ref T) :
  Result T := .ok r.val

/-- `<RefMut<'_, T> as Deref>::deref`. -/
def core.cell.RefMut.Insts.CoreOpsDerefDeref.deref {T : Type} (r : core.cell.RefMut T) :
  Result T := .ok r.val

/-- Used by `deref_mut`, so that `#print axioms` lists it for every
declaration that depends on `deref_mut`. -/
axiom Lusterna.refMutWriteDropped : True

/-- `<RefMut<'_, T> as DerefMut>::deref_mut`. Both back functions return `r`
unchanged, so writes through the result are dropped. -/
def core.cell.RefMut.Insts.CoreOpsDerefDerefMut.deref_mut {T : Type}  (r : core.cell.RefMut T) :
  Result (T × (T → core.cell.RefMut T) × (core.cell.RefMut T → core.cell.RefMut T)) :=
    have _ := Lusterna.refMutWriteDropped
    .ok (r.val, fun _ => r, fun _ => r)
