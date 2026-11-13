---
name: invariants
description: How to state an invariant of a struct as Lean theorems about an Aeneas translation: which functions need a base case or an induction step, and the shape of those theorems. Use when you state an invariant.
---

# Invariants of a struct

An invariant of a struct holds for every value of the struct that the program creates, and keeps holding after every change. It is proven by induction: a base case for each function that creates the struct, and an induction step for each mutating operation.

## The functions

1. The mutating operations of the struct are the outermost functions, among those reachable from the entrypoints, that take the struct or a value containing it as `&mut`, so that the translation returns its new value. A function that does not change the struct is not one. A helper that only such a function calls is not one either, because the invariant only has to hold between operations, not in the middle of one. A function that changes the struct only through a write that the translation drops, such as a write through a `RefMut`, does not change it in the model and is not one.
2. The base cases are every function that creates a value of the struct.

## The theorems

1. Base case: for each function that creates the struct, write one theorem. It calls the function and asserts in the postcondition that the invariant holds for the created value.
2. Induction step: for each mutating operation, write one theorem. Its induction hypothesis `h_inv` is that the invariant holds for the value before the call. It calls the mutating operation and asserts in the postcondition that the invariant holds for the value after the call. A Rust function that takes `&mut` returns the new value as part of its result.
3. In the base case and induction step theorems, the triple calls the function directly, not through a `do` block.
4. An invariant is about the values that the program reaches, and a call that panics reaches none. So a base case or induction step theorem is a `⦄fail` triple. A base case has no hypothesis, and an induction step has no hypothesis other than `h_inv`.
5. If the function returns a Rust `Result`, the postcondition asserts the invariant in the `.Ok` case and `True` in the `.Err` case.
6. A type parameter or an instance that the type of the struct argument needs comes before `-- State`.
7. Write the invariant inline in each theorem, or as a helper `def` that the theorems share.

```lean
theorem f_keeps_inv
    -- State
    (s : Struct)
    -- Inputs
    (x : U64)
    -- Induction hypothesis
    (h_inv : Inv s) :
    -- Statement
    f s x ⦃ (res, s') =>
      match res with
      | .Ok _ => Inv s'
      | .Err _ => True ⦄fail := by
  sorry
```
