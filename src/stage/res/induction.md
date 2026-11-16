---
name: induction
description: Which functions of an Aeneas translation an invariant of a struct needs a base case or an induction step for, decided from the Rust source of the crate. Use when you choose the functions of an induction over a struct.
---

# The functions of an induction

An invariant of a struct holds for every value of the struct that the program creates, and keeps holding after every change. It is proven by induction: a base case for each function that creates the struct, and an induction step for each operation that changes it.

Decide which functions these are from the Rust source of the crate, not from the translation alone: the translation does not contain every caller of a function.

1. A function changes the struct if it writes into a whole value of the struct, directly or through a function that it calls. The value may be an argument, inside an argument, behind a `RefMut`, or built by the function. A function that has only a part of the struct, such as the value of one of its fields, does not change the struct, even if its caller passes it that part.
2. A base case is a function of the translation that builds a value of the struct: it returns a new value, or it writes every field of a value that it takes as `&mut` without reading the old value first.
3. A helper is a function that changes the struct, that the crate does not export, and whose every call in the Rust source of the crate is inside a function of the translation that changes the struct. The invariant only has to hold between operations, not in the middle of one. A function is exported if every module on its path from the crate root is `pub`, or if a `pub use` re-exports it. An exported function is never a helper.
4. An operation is a function of the translation that changes the struct and is neither a base case nor a helper.
5. A mutating operation is an operation that changes a value of the struct that it takes as `&mut`, alone or inside another value. The translation returns the new value, so the operation gets an induction step.
6. An unmodelled operation is an operation that changes a value of the struct through a write that the translation drops, such as a write through a `RefMut`. The translation does not return the new value, so the operation gets no theorem for that value. An operation that changes one value of the struct each way is both: its induction step covers the value that it takes as `&mut`.
