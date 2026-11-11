---
name: aeneas-partial-correctness
description: How to prove a theorem about an Aeneas translation that assumes that the call returns (a hypothesis `∃ r, f args = ok r`), how to use a bridge in such a proof, and how to state a counterexample. Use when you write a proof or a counterexample in `Spec/`.
---

# Proving theorems that assume that the call returns

Some theorems have a hypothesis of the form `∃ r, f args = ok r`: the call returns. The theorems of an invariant and the bridges have one. The proof of such a theorem does not show that the call returns. It works backwards from the returned value. For every other theorem, see the skill `lusterna:aeneas-tactics`.

The examples name the hypothesis `h_ok`. In a theorem it can have another name.

The examples are about this translation. They leave out the namespaces.

```lean
-- Rust: fn get_total(&self) -> u64 { self.a + self.b - self.c }
def Struct.get_total (self : Struct) : Result U64 := do
  let i ← self.a + self.b
  i - self.c

-- Rust: adds `x` to `a` if `x` is at most the total, and returns `Err` otherwise
def Struct.f (self : Struct) (x : U64) :
    Result (core.result.Result Unit Unit × Struct) := do
  let t ← self.get_total
  if x > t then ok (.Err (), self)
  else
    let a1 ← self.a + x
    ok (.Ok (), { self with a := a1 })

-- Rust: sets `c`
def Struct.g (self : Struct) (y : U64) : Result Struct :=
  ok { self with c := y }

-- The model of `get_total`, and an invariant that uses it
def Struct.getTotal (s : Struct) : Int := s.a.val + s.b.val - s.c.val
def Inv (s : Struct) : Prop := (s.c.val : Int) ≤ s.getTotal
```

## The steps of a proof

1. Name the returned value from the hypothesis: `obtain ⟨r, h_r⟩ := h_ok`. Now `h_r : f args = ok r`.
2. Reduce the goal to the postcondition for `r`: `rw [h_r]`, then `simp only [WP.spec_ok]`.
3. Unfold the function in the equation: `unfold f at h_r`.
4. Split the equation at each bind with `bind_eq_ok`. If a bind returns, its first part returns a value, and the rest returns for that value.
5. For each part, get the fact that follows from "this part returns". For an operation on scalars it is a helper lemma such as `U64.add_eq_ok`. For a call of a function that has a bridge it is the bridge, applied with `post_of_eq_ok`.
6. Split on each `if` and `match` of the function with `split at h_r`.
7. At a `ok v = ok r` equation, replace `r`: `obtain rfl := Result.ok_injective h_r`.
8. Close the goal from the facts.

`bind_eq_ok`, `post_of_eq_ok` and the lemmas for operations are not in Aeneas. "Helper lemmas" gives them.

## A bridge

```lean
@[step]
theorem Struct.get_total.spec (s : Struct)
    (h_ok : ∃ r, s.get_total = ok r) :
    s.get_total ⦃ r => (r.val : Int) = s.getTotal ⦄ := by
  -- Name the returned value, and reduce the goal to its postcondition
  obtain ⟨r, h_r⟩ := h_ok
  rw [h_r]
  simp only [WP.spec_ok]
  -- Split the equation at the bind
  unfold Struct.get_total at h_r
  obtain ⟨i, h_i, h_r⟩ := bind_eq_ok.mp h_r
  -- The facts that the two operations imply
  have h_add := U64.add_eq_ok h_i
  have h_sub := U64.sub_eq_ok h_r
  simp only [Struct.getTotal]
  scalar_tac
```

## An induction step that uses the bridge

```lean
theorem f_keeps_inv
    -- State
    (s : Struct)
    -- Inputs
    (x : U64)
    -- Induction hypothesis
    (h_inv : Inv s)
    -- No panic
    (h_ok : ∃ r, s.f x = ok r) :
    -- Statement
    s.f x ⦃ (res, s') =>
      match res with
      | .Ok _ => Inv s'
      | .Err _ => True ⦄ := by
  obtain ⟨r, h_r⟩ := h_ok
  rw [h_r]
  simp only [WP.spec_ok]
  unfold Struct.f at h_r
  -- The call of `get_total` returns some `t`, and its bridge gives the value of `t`
  obtain ⟨t, h_t, h_r⟩ := bind_eq_ok.mp h_r
  have h_total := post_of_eq_ok h_t (Struct.get_total.spec s ⟨t, h_t⟩)
  split at h_r
  · -- `x > t`: the function returns `Err`
    obtain rfl := Result.ok_injective h_r
    trivial
  · -- Otherwise the function adds `x` to `a`
    obtain ⟨a1, h_a1, h_r⟩ := bind_eq_ok.mp h_r
    have h_add := U64.add_eq_ok h_a1
    obtain rfl := Result.ok_injective h_r
    simp [Inv, Struct.getTotal] at *
    scalar_tac
```

## Helper lemmas

Put these in a file under `Spec/Lemmas/` and import it. Write them once.

```lean
/-- If a bind returns `r`, its first part returns some `x` and the rest returns `r` for that `x`. -/
theorem bind_eq_ok {α β} {m : Result α} {k : α → Result β} {r : β} :
    (do let x ← m; k x) = ok r ↔ ∃ x, m = ok x ∧ k x = ok r := by
  constructor
  · intro h
    cases m using Result.cases <;> simp_all
  · rintro ⟨x, hm, hk⟩
    simp [hm, hk]

/-- If a computation returns `r` and a triple holds for it, the postcondition holds for `r`. -/
theorem post_of_eq_ok {α} {m : Result α} {post : α → Prop} {r : α}
    (h : m = ok r) (hs : m ⦃ r => post r ⦄) : post r := by
  rw [h] at hs
  simpa using hs

/-- If `x + y` returns `z`, then `z` is the sum. -/
theorem U64.add_eq_ok {x y z : U64} (h : x + y = ok z) : z.val = x.val + y.val := by
  have := UScalar.add_equiv x y
  rw [h] at this
  simp_all

/-- If `x - y` returns `z`, then `y ≤ x` and `z` is the difference. -/
theorem U64.sub_eq_ok {x y z : U64} (h : x - y = ok z) :
    y.val ≤ x.val ∧ z.val = x.val - y.val := by
  have := UScalar.sub_equiv x y
  rw [h] at this
  simp_all
```

Write a lemma of this kind for each operation that the functions use: if the operation returns `z`, what holds for `z`. The two lemmas above follow from `UScalar.add_equiv` and `UScalar.sub_equiv` of Aeneas, which state what an operation returns and when it fails. Look for the theorem of this kind for the operation that you need.

## Pitfalls

- `cases h` on `h : ok a = ok b` fails, because `ok` is not a constructor of `Result`. Use `Result.ok_injective h`.
- `simp only` with the definitions does not reduce the `match` on the returned tuple in the goal. Reduce it with `simp` before the arithmetic tactic, as in `f_keeps_inv`.
- `Nat` subtraction stops at zero. Keep the bound `y ≤ x` that a subtraction lemma gives.

## A counterexample

A counterexample of a theorem states that there are values for its parameters that satisfy every hypothesis, and that its conclusion fails for them. For a theorem `g_keeps_inv` with the hypotheses `h_inv : Inv s` and `h_ok : ∃ r, s.g y = ok r` and the conclusion `s.g y ⦃ s' => Inv s' ⦄`:

```lean
theorem g_keeps_inv_counterexample :
    ∃ (s : Struct) (y : U64),
      Inv s ∧ (∃ r, s.g y = ok r) ∧ ¬ (s.g y ⦃ s' => Inv s' ⦄) := by
  refine ⟨⟨0#u64, 0#u64, 0#u64⟩, 1#u64, ?_, ⟨_, rfl⟩, ?_⟩
  · simp [Inv, Struct.getTotal]
  · simp [Struct.g, Inv, Struct.getTotal]
```

A theorem for `reachable` is a triple over a `do` block. The block starts with a function that creates the state, and the postcondition says that the property fails. For a function `Struct.new` that creates a `Struct` with every field zero:

```lean
theorem g_breaks_inv_reachable :
    (do let s ← Struct.new; s.g 1#u64) ⦃ s' => ¬ Inv s' ⦄ := by
  simp [Struct.new, Struct.g, Inv, Struct.getTotal]
```
