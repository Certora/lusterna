---
name: induction
description: Which functions of an Aeneas translation an invariant of a struct needs a base case or an induction step for, decided from the Rust source of the crate. Use when you choose the functions of an induction over a struct.
---

# The functions of an induction

An invariant of a struct holds for every value of the struct that the program creates, and keeps holding after every change. It is proven by induction: a base case for each function that creates the struct, and an induction step for each operation that changes it.

Decide which functions these are from the Rust source of the crate, not from the translation alone: the translation does not contain every caller of a function.

The translate stage replaced some functions for the translation: the replacement of `f` is `f_lusterna` in `src/lusterna/`, and `f` calls it. A replacement is part of its original, and a function that Aeneas generates for a loop of `f`, whose name starts with the name of `f` followed by `_loop`, is part of `f`. List neither in an induction: list the original instead, and count a call inside them as a call inside the original.

1. A function changes the struct if it writes into a whole value of the struct, directly or through a function that it calls. The value may be an argument, inside an argument, behind a `RefMut`, or built by the function. A function that has only a part of the struct, such as the value of one of its fields, does not change the struct, even if its caller passes it that part.
2. A base case is a function of the translation that builds a value of the struct: it returns a new value, or it writes every field of a value that it takes as `&mut` without reading the old value first.
3. {helper}
4. An operation is a function of the translation that changes the struct and is neither a base case nor a helper.
5. A mutating operation is an operation that changes a value of the struct that it takes as `&mut`, alone or inside another value. The translation returns the new value, so the operation gets an induction step.
6. An unmodelled operation is an operation that changes a value of the struct through a write that the translation drops, such as a write through a `RefMut`. The translation does not return the new value, so the operation gets no theorem for that value. An operation that changes one value of the struct each way is both: its induction step covers the value that it takes as `&mut`.

## The lists of an induction

An induction names the functions of a struct in four lists: the base cases in `base_cases`, the mutating operations in `steps`, the unmodelled operations in `unmodelled_steps`, and the helpers in `helpers`. Each function is in the list that this skill gives it, and no list names another function.

1. Search the translation for the functions that change a value of the struct, and the Rust source of the crate for every call of them. To find the unmodelled operations, follow the calls of `deref_mut` on a `RefMut` of the struct up to the operations.
2. An entry of `helpers` names every function of the translation that calls the helper. Decide whether a function is a helper from every call of it in the Rust source of the crate.
3. An entry of `unmodelled_steps` gives the value of the struct that the operation changes and the write that the translation drops, as the Rust source of the write shows.
