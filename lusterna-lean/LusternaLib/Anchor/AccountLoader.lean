module
public import Aeneas
public import Solanalib.Anchor.AccountLoader
public import LusternaLib.Core.Cell
public import LusternaLib.SolanaProgram.Pubkey

/-!
# `anchor_lang::accounts::account_loader::AccountLoader`

Anchor's `AccountLoader` with the name Aeneas gives it, as a
`Solanalib.Anchor.AccountLoader` with a 32-byte key.

The types of Aeneas's `load`, `load_mut` and `key` name `anchor_lang`
types that Aeneas writes into the crate's own `Types.lean`. So this file
gives `load`, `loadMut` and `key` in the namespace
`Lusterna.Anchor.AccountLoader`, and the crate's `FunsExternal.lean`
defines the Aeneas functions from them:

```
def anchor_lang.accounts.account_loader.AccountLoader.load_mut {T : Type}
    (_ : anchor_lang.ZeroCopy T) (_ : anchor_lang.Owner T) :=
  Lusterna.Anchor.AccountLoader.loadMut (T := T) toAnchorError
```

where `toAnchorError : Solanalib.Anchor.Error → anchor_lang.error.Error`
builds the crate's Anchor error.

`load` and `loadMut` return the value that the loader holds. Writes through
the `RefMut` that `loadMut` returns are dropped.
-/

@[expose] public section

open Aeneas.Std

/-- `anchor_lang::accounts::account_loader::AccountLoader<'info, T>`. -/
structure anchor_lang.accounts.account_loader.AccountLoader (T : Type) where
  /-- The account. -/
  loader : Solanalib.Anchor.AccountLoader T
  /-- A Solana public key has 32 bytes. -/
  key_size_eq : loader.key.bytes.size = 32

namespace Lusterna.Anchor.AccountLoader

variable {T E : Type}

/-- `AccountLoader::load`. `toError` converts the error to the crate's
Anchor error. -/
def load (toError : Solanalib.Anchor.Error → E)
    (l : anchor_lang.accounts.account_loader.AccountLoader T) :
    Result (core.result.Result (core.cell.Ref T) E) :=
  match l.loader.load with
  | .ok value => .ok (.Ok ⟨value⟩)
  | .error e => .ok (.Err (toError e))

/-- `AccountLoader::load_mut`. `toError` converts the error to the crate's
Anchor error. The back function returns nothing, as in the Aeneas
translation of a method that takes `&self`. -/
def loadMut (toError : Solanalib.Anchor.Error → E)
    (l : anchor_lang.accounts.account_loader.AccountLoader T) :
    Result ((core.result.Result (core.cell.RefMut T) E) ×
      (core.result.Result (core.cell.RefMut T) E → Unit)) :=
  match l.loader.loadMut with
  | .ok value => .ok (.Ok ⟨value⟩, fun _ => ())
  | .error e => .ok (.Err (toError e), fun _ => ())

/-- `<AccountLoader<'info, T> as Key>::key`. -/
def key (l : anchor_lang.accounts.account_loader.AccountLoader T) :
    Result solana_program.pubkey.Pubkey :=
  .ok ⟨l.loader.key, l.key_size_eq⟩

@[simp] theorem load_eq (toError : Solanalib.Anchor.Error → E)
    (l : anchor_lang.accounts.account_loader.AccountLoader T) :
    load toError l = .ok (.Ok ⟨l.loader.value⟩) := rfl

theorem loadMut_of_writable (toError : Solanalib.Anchor.Error → E)
    (l : anchor_lang.accounts.account_loader.AccountLoader T)
    (h : l.loader.isWritable = true) :
    loadMut toError l = .ok (.Ok ⟨l.loader.value⟩, fun _ => ()) := by
  simp [loadMut, Solanalib.Anchor.AccountLoader.loadMut, h]

end Lusterna.Anchor.AccountLoader
