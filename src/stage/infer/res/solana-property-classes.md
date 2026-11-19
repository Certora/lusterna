---
name: solana-property-classes
description: The classes of properties that a Solana program adds to those of the skill `defi-property-classes`. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Property classes of a Solana program

The accounts of an instruction are inputs that the caller chooses. Ask each question below of every handler that reads or writes an account.

A property names functions of the crate. State a property of this skill only for a check that a function of the crate performs in its own body. A check that Anchor generates from the attributes of an accounts struct is in no function that a property can name.

## The classes

**Who signed.** Which accounts must have signed for the instruction to do what it does? A property states that the handler fails if that signer is missing, or is not the key that the state names. The check is against the key in the state, in addition to the signer flag.

**Who owns the account.** Every account that the handler deserializes and trusts must be owned by the program that defines its type. A property states that the handler rejects an account that another program owns.

**Which account it is.** An account of the right type and owner can still be the wrong one. A property ties each account to the state that names it, and states that the handler rejects a substitute.

**Arithmetic that a release build does not check.** A release build can wrap an integer operation that overflows. Where the program uses unchecked arithmetic, a property states that the result is in range, or that the handler rejects the inputs that would take it out of range.

**The accounts that the instruction does not declare.** The remaining accounts, and accounts that a handler takes by position, have no constraint unless the code checks them. A property states what the handler checks before it trusts one.
