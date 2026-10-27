module
public import Aeneas
public import Solanalib.Primitives.Pubkey

/-!
# `solana_program::pubkey::Pubkey`

`solana_program::pubkey::Pubkey` with the name Aeneas gives it, as a
`Solanalib.Pubkey` of 32 bytes.
-/

@[expose] public section

open Aeneas.Std

/-- `solana_program::pubkey::Pubkey`. -/
structure solana_program.pubkey.Pubkey where
  /-- The key. -/
  key : Solanalib.Pubkey
  /-- A Solana public key has 32 bytes. -/
  size_eq : key.bytes.size = 32

/-- `<Pubkey as PartialEq>::eq`. -/
def solana_program.pubkey.Pubkey.Insts.CoreCmpPartialEqPubkey.eq
    (a b : solana_program.pubkey.Pubkey) : Result Bool :=
  .ok (decide (a.key = b.key))

/-- `<Pubkey as Default>::default`. -/
def solana_program.pubkey.Pubkey.Insts.CoreDefaultDefault.default :
    Result solana_program.pubkey.Pubkey :=
  .ok {
    key := ⟨⟨(List.replicate 32 (0 : UInt8)).toArray⟩⟩
    size_eq := by simp [ByteArray.size] }

/-- `Pubkey::new_from_array`. -/
def solana_program.pubkey.Pubkey.new_from_array (bytes : Array U8 32#usize) :
    Result solana_program.pubkey.Pubkey :=
  .ok {
    key := ⟨⟨(bytes.val.map fun b => UInt8.ofBitVec b.bv).toArray⟩⟩
    size_eq := by simp [ByteArray.size] }
