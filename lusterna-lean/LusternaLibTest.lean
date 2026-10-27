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
