---
name: aeneas-models
description: How to write a pure Lean model of a translated function and the `@[step]` theorem that links the function to it. Use when a statement in `Spec/` needs the value of a translated function.
---

# Models of translated functions

A translated function returns `Result α`, and a postcondition must not contain a term of type `Result _`. When a postcondition or a helper `Prop` needs the value of a translated function, for example `s.get_total()`, it uses a model of that function instead. A model is a pure Lean `def` that computes the same value. Each model has a bridge: a `@[step]` theorem stating that the translated function returns the value of the model.

```lean
-- Rust: fn get_total(&self) -> u64 { self.a + self.b - self.c }

namespace my_crate.module.Struct

/-- `Struct::get_total` -/
def getTotal (s : Struct) : Int :=
  s.a.val + s.b.val - s.c.val

@[step]
theorem get_total.spec (s : Struct)
    (h_ok : ∃ r, get_total s = ok r) :
    get_total s ⦃ r => (r.val : Int) = s.getTotal ⦄ := by
  sorry

end my_crate.module.Struct
```

A statement then uses the model:

```lean
def Inv (s : Struct) : Prop := s.c.val ≤ s.getTotal
```

## Which functions get a model

Write a model for a translated function whose value a statement talks about. Do not write models for the functions that a function calls internally: the proof deals with those.

A function without a body, such as an `axiom` in `FunsExternal.lean`, gets no model.

## The model

1. The model is a `def` with a pure body. It does not call a translated function or anything else that returns `Result`. It may call other models.
2. The model is total. It computes on pure Lean values, outside the `Result` monad, for example of type `Nat`, `Int` or `ℚ`, and it has no case for a panic.
3. The model computes exactly what the function returns. Write rounding, floor division and saturation into the model as the code does them.
4. The model reads like the Rust function. Use the names of the fields and of the other models, not unfolded bit arithmetic.

## The bridge

1. The bridge is a theorem named `<function>.spec` with `@[step]`, and its body is `:= by sorry`. It carries no `@[lusterna]` label.
2. It is a triple that calls the translated function on plain arguments. Its postcondition states that the result equals the model: `r = model args`, or `r.val = model args` for a scalar, or one equality per component for a tuple.
3. Its only hypothesis is `h_ok : ∃ r, f args = ok r`: the call returns. Do not state when the function panics.
4. Write only the equality. A weaker relation, such as `r.val ≥ …`, is a lemma for the proof, not a bridge.

## Files and names

1. Models live in `Spec/Model/`, in one file per Rust module, with the path of the module: the models of `src/module/inner.rs` go in `Spec/Model/Module/Inner.lean`. The file holds the models of the methods and of the free functions of that module, with their bridges.
2. A model goes in the namespace of what it models. A model of a method goes in the namespace of its struct, so that `s.getTotal` works. A model of a free function goes in the namespace of the translated function's module.
3. Name the model after the Rust function in lowerCamelCase: `get_total` becomes `getTotal`. If the translated function already has that name, as a one-word name like `sum` does, append `Model`: `sumModel`. Give it a doc comment with the Rust path, for example `/-- \`Struct::get_total\` -/`.
4. Before you write a model, look for one in `Spec/Model/`. Reuse it, and do not write a second model of the same function.
5. A property file imports the model files that it uses.
