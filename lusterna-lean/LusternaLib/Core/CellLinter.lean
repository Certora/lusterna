module

public meta import Lean.Elab.Command
public import LusternaLib.Core.Cell

/-!
# The `linter.lusterna.refMutWrite` linter

Warns on a theorem that depends on `RefMut`'s `deref_mut`, whose writes are
dropped. Such a theorem can hold only because a write is lost.

The linter is on by default. `set_option linter.lusterna.refMutWrite false`
turns it off, and `set_option linter.lusterna.refMutWrite false in` before a
theorem turns it off for that theorem.
-/

meta section

open Lean Elab Command Linter

/-- Warn on a theorem that depends on `RefMut`'s `deref_mut`. -/
public register_option linter.lusterna.refMutWrite : Bool := {
  defValue := true
  descr := "warn on a theorem that depends on `RefMut`'s `deref_mut`"
}

@[inherit_doc linter.lusterna.refMutWrite]
def refMutWriteLinter : Linter where run := withSetOptionIn fun stx => do
  unless getLinterValue linter.lusterna.refMutWrite (← getLinterOptions) do
    return
  unless stx.isOfKind ``Lean.Parser.Command.declaration do
    return
  for tree in ← getInfoTrees do
    let decls := tree.foldInfo (init := #[]) fun _ info acc =>
      match info with
      | .ofTermInfo ti =>
        match ti.isBinder, ti.expr with
        | true, .const declName _ => acc.push (ti.stx, declName)
        | _, _ => acc
      | _ => acc
    for (ref, declName) in decls do
      unless (← getEnv).find? declName matches some (.thmInfo _) do
        continue
      let axioms ← liftCoreM (collectAxioms declName)
      if axioms.contains ``Lusterna.refMutWriteDropped then
        logLint linter.lusterna.refMutWrite ref
          m!"`{declName}` depends on `core.cell.RefMut.Insts.CoreOpsDerefDerefMut.deref_mut`. \
          Writes through a `RefMut`, such as the one that `AccountLoader::load_mut` returns, \
          are dropped, so the theorem can hold only because a write is lost."

initialize addLinter refMutWriteLinter
