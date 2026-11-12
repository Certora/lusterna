---
name: aeneas-tactics
description: How to prove a theorem about an Aeneas translation with `step`: the two triples, the theorem that each call needs, branches, which tactic fits which goal, slow proofs, and how to state a counterexample. Use when you write or repair a proof in `Spec/`.
---

# Proving theorems about an Aeneas translation

The examples are about this translation. They leave out the namespaces.

```lean
-- Rust: fn get_total(&self) -> u64 { self.a + self.b - self.c }
def Struct.get_total (self : Struct) : Result U64 := do
  let i ← self.a + self.b
  i - self.c

-- Rust: writes a log line
def Struct.log (self : Struct) : Result Unit := ok ()

-- Rust: adds `x` to `a` if `x` is at most the total, and returns `Err` otherwise
def Struct.f (self : Struct) (x : U64) :
    Result (core.result.Result Unit Unit × Struct) := do
  let _ ← self.log
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
def Covered (s : Struct) : Prop := (s.c.val : Int) ≤ s.getTotal
```

## The two triples

- `m ⦃ r => post ⦄fail` says: if `m` returns `r`, then `post` holds for `r`. It holds if `m` panics. It is `Lusterna.Spec.fspec m (fun r => post)`, and a file that uses it needs `import LusternaSpec` and `open Lusterna.Spec`.
- `m ⦃ r => post ⦄` says that `m` returns and that `post` holds for its result. It is `Aeneas.Std.WP.spec m (fun r => post)`.

`step` and `step*` work on both.

## Seeing the goal

You have no editor. Build one module with `lake build Spec.<Module>`, for example `lake build Spec.Lemmas.Scalar`. An error message shows the goal at the place of the error. To see the goal at another place, put the tactic `trace_state` there and build: the build output shows the goal.

## The standard proof

Unfold the function of the theorem, then let `step` handle one call at a time. `step` looks for a theorem with the attribute `@[step]` about the function that the goal calls next, applies it, and adds its postcondition to the context.

```lean
@[step]
theorem Struct.get_total.spec (s : Struct) :
    s.get_total ⦃ r => (r.val : Int) = s.getTotal ⦄fail := by
  unfold Struct.get_total
  -- `self.a + self.b`: the sum did not overflow (`h_max`), and `i` is the sum (`h_i`)
  step as ⟨i, h_max, h_i⟩
  -- `i - self.c`: `self.c ≤ i` (`h_le`), and `r` is the difference (`h_r`)
  step as ⟨r, h_le, h_r⟩
  simp only [Struct.getTotal]
  scalar_tac
```

The forms of `step`:

- `step`: applies the `@[step]` theorem that matches the next call.
- `step as ⟨x, h1, h2⟩`: also names the result and the parts of the postcondition, in order. Without `as` they get names that `step` chooses.
- `step with my_theorem`: applies the given theorem. Use it when several `@[step]` theorems match the call, or when none is registered.
- `step*`: repeats `step` and splits on each branch. It stops where a call has no theorem and where the function returns.
- `step*?`: like `step*`, and prints the proof script that it found. Use it to get a script that you then repair where it stops.

Unfold only the function of the theorem. Do not unfold a function that it calls: `step` then has to go through every path of both functions, and a function that several functions call is checked again in each of them.

## A theorem for every call

`step` needs a `@[step]` theorem about each function that the function of the theorem calls. On a `⦄fail` goal it looks for a `⦄fail` theorem first, and for a `⦃ ⦄` theorem if there is none.

1. **Arithmetic on scalars.** `LusternaSpec` has `⦄fail` theorems for it. Each one has the hypotheses of the `⦃ ⦄` theorem of Aeneas in its postcondition: if `x + y` returns `z`, then `x.val + y.val ≤ U64.max` and `z.val = x.val + y.val`.
2. **A function with a bridge.** The bridge is its theorem.
3. **A function of the crate whose result the proof needs.** State a `@[step]` theorem about it that says what the proof needs, and prove it like any other theorem: unfold the function and use `step`. If the function leaves the values of the property unchanged, the theorem says that.
4. **A function whose result the proof does not need.** State a theorem with the postcondition `True`. Its proof is always the same:

```lean
@[step]
theorem Struct.log.fspec_any (s : Struct) : s.log ⦃ _ => True ⦄fail :=
  fspec_iff.mpr fun _ _ => trivial
```

With these theorems the proof of `f` does not look into `get_total` or `log`:

```lean
theorem f_keeps_inv
    -- State
    (s : Struct)
    -- Inputs
    (x : U64)
    -- Induction hypothesis
    (h_inv : Covered s) :
    -- Statement
    s.f x ⦃ (res, s') =>
      match res with
      | .Ok _ => Covered s'
      | .Err _ => True ⦄fail := by
  unfold Struct.f
  -- `self.log`
  step
  -- `self.get_total`: the bridge gives the value of `t`
  step as ⟨t, h_t⟩
  split
  · -- `x > t`: the function returns `Err`
    simp
  · -- Otherwise the function adds `x` to `a`
    step as ⟨a1, h_max, h_a1⟩
    simp [Covered, Struct.getTotal] at *
    scalar_tac
```

If `step` leaves a goal that an operation does not overflow, or another condition under which a call does not panic, it applied a `⦃ ⦄` theorem. The call has no `⦄fail` theorem. Do not try to prove that goal on a `⦄fail` theorem: it does not follow. Write the `⦄fail` theorem for the call, with the condition in the postcondition:

```lean
-- Rust: fn twice(x: u64) -> u64 { x + x }
@[step]
theorem twice.fspec (x : U64) :
    twice x ⦃ r => 2 * x.val ≤ U64.max ∧ r.val = 2 * x.val ⦄fail := by
  unfold twice
  step*
```

For an operation that is not a function of the crate, `fspec_of_spec_pre` builds the `⦄fail` theorem from the `⦃ ⦄` theorem of Aeneas and a proof that the operation returns only if the hypothesis of that theorem holds.

## Branches

When the function has an `if` or a `match`, split the goal with `split`, and prove each case in its own block. `simp_ifs` simplifies an `if` whose condition follows from the hypotheses.

If the function starts with an `if` or a `match`, split before the first `step`. Otherwise `step` can apply the theorem that you are proving to itself, and Lean then reports a termination error for a proof that looks finished.

`step*` stops at a `let` that destructures a variable, such as `let (a, b) := p`. Destructure the variable and continue:

```lean
obtain ⟨a, b⟩ := p
simp only [Aeneas.Std.uncurry_apply_pair]
step*
```

The operator `?` of Rust becomes a call of `branch` and a `match` on its result. After `step`, the context has a hypothesis that says, for each case of the Rust `Result`, which case of the `match` the function took. To use it, split on the Rust `Result` with `cases`, and simplify with `simp_all`.

## Where the function returns

Where the function returns `v`, the goal is `ok v ⦃ post ⦄fail`. `simp` reduces it to `post v`, and `simp only [fspec_ok]` does so without rewriting anything else. Close it from the facts that the `step`s gave.

## A `⦃ ⦄` goal at a call whose theorem is `⦄fail`

`step` does not apply a `⦄fail` theorem to a `⦃ ⦄` goal, because the `⦄fail` theorem does not say that the call returns. Prove that the call returns, as a `⦃ ⦄` theorem with the postcondition `True` and the hypotheses that it needs. `spec_of_fspec` then gives the `⦃ ⦄` theorem with the postcondition of the `⦄fail` theorem:

```lean
theorem Struct.get_total.returns (s : Struct)
    (h1 : s.a.val + s.b.val ≤ U64.max) (h2 : s.c.val ≤ s.a.val + s.b.val) :
    s.get_total ⦃ _ => True ⦄ := by
  unfold Struct.get_total
  step*

@[step]
theorem Struct.get_total.total_spec (s : Struct)
    (h1 : s.a.val + s.b.val ≤ U64.max) (h2 : s.c.val ≤ s.a.val + s.b.val) :
    s.get_total ⦃ r => (r.val : Int) = s.getTotal ⦄ :=
  spec_of_fspec (Struct.get_total.returns s h1 h2) (Struct.get_total.spec s)
```

## Which tactic for which goal

| Goal | Try first | Then |
|---|---|---|
| The next call of a function | `step` | `step with <theorem>` |
| Arithmetic on scalars and their bounds | `scalar_tac` | `agrind` |
| Arithmetic with products of variables | `scalar_tac +nonLin` | a `have` for the product, then `scalar_tac` |
| An expression to simplify, such as `a % n` with `a < n` | `simp_scalar` | `simp_scalar [lemma]` |
| Lists, arrays and slices with reads and writes | `agrind` | `simp_lists [*]` |
| Bit operations | `bv_tac <width>` | `bvify <width>`, then `bv_tac <width>` |
| A statement modulo `n` | `zmodify`, then `ring` | |
| An `if` | `simp_ifs` | `split` |
| A conjunction | `split_conjs <;> agrind` | prove the hard parts by hand |
| Booleans and propositions | `simp_bool_prop` | `tauto` |
| A fact about fixed numbers | `decide` | |
| Anything else | `agrind` | `simp [*]; agrind` |

## Pitfalls

- Do not use `omega` on a goal with scalars. It does not know the bounds of the scalar types or `U64.max`. Use `scalar_tac`.
- Prefer `agrind` to `grind`. `grind` can run for a long time.
- `simp_all` can remove a hypothesis that you still need. Use `simp [h1, h2]` or `simp [*]`.
- `Nat` subtraction stops at zero. Keep the bound `y ≤ x` with a difference `x - y`, or state the fact as a sum.
- Do not use `native_decide`. It declares an axiom, and the harness then does not count the theorem as proven.
- Do not unfold the definitions of the Aeneas standard library in a proof. Search the library for a lemma. If there is none, state the lemma and prove it on its own.
- `exact h` and `assumption` compare the goal with a hypothesis by unfolding both. On two large structures that do not match, this can run out of recursion depth. Reduce the goal to the fields that the statement is about first, with `simp only` and the definitions of the statement.

## Loops and recursive functions

Aeneas translates a loop to a function of its own, whose name ends in `_loop`. Write a separate theorem with `@[step]` for each such function. Its hypothesis is the loop invariant.

If the function is recursive, unfold it, split on its condition, and use `step`. In the case of the recursive call, `step` applies the theorem that you are proving. End the theorem with a measure that decreases at each call:

```lean
@[step]
theorem my_loop.spec (x : State) (h : loop_invariant x) :
    my_loop x ⦃ r => postcondition r ⦄ := by
  unfold my_loop
  split
  · -- the case that calls `my_loop` again
    step*
    split_conjs <;> agrind
  · -- the case that leaves the loop
    step*
termination_by measure x
decreasing_by scalar_decr_tac
```

If the function uses the operator `loop`, apply `loop.spec_decr_nat`. It takes a measure in `Nat`, an invariant and a postcondition. You then show that the body keeps the invariant and decreases the measure when it continues, and gives the postcondition when it is done. `loop.spec_decr_nat` is about `⦃ ⦄`. `LusternaSpec` has no rule for a `⦄fail` triple over `loop`.

## Large functions and slow proofs

- Aeneas copies the code after a branch into each case of the branch. `step*` goes through each copy. If a function is long, state a lemma for a part of it and use the lemma in the proof of the whole.
- Give a hard fact as a `have` before the tactic that needs it.
- If a proof takes more than a minute to check, split it into lemmas.
- If Lean reports that the maximum number of heartbeats has been reached, put `set_option maxHeartbeats 1000000 in` in front of the theorem. Do not set a larger value. If that is not enough, split the proof into lemmas.

## A counterexample

A counterexample of a theorem states that there are values for its parameters that satisfy every hypothesis, and that its conclusion fails for them. A `⦄fail` triple fails only if the call returns a value for which the postcondition does not hold. For a theorem `g_keeps_inv` with the hypothesis `h_inv : Covered s` and the conclusion `s.g y ⦃ s' => Covered s' ⦄fail`:

```lean
theorem g_keeps_inv_counterexample :
    ∃ (s : Struct) (y : U64), Covered s ∧ ¬ (s.g y ⦃ s' => Covered s' ⦄fail) := by
  refine ⟨⟨0#u64, 0#u64, 0#u64⟩, 1#u64, ?_, ?_⟩
  · simp [Covered, Struct.getTotal]
  · simp [Struct.g, Covered, Struct.getTotal]
```

A theorem for `reachable` is a `⦃ ⦄` triple over a `do` block, so that it also shows that the block returns. The block starts with a function that creates the state, and the postcondition says that the property fails. For a function `Struct.new` that creates a `Struct` with every field zero:

```lean
theorem g_breaks_inv_reachable :
    (do let s ← Struct.new; s.g 1#u64) ⦃ s' => ¬ Covered s' ⦄ := by
  simp [Struct.new, Struct.g, Covered, Struct.getTotal]
```

## The Aeneas documentation

`lakefile.toml` of the Lean project gives the path of the `aeneas` package, which ends in `backends/lean`. The documentation is in the directory `documentation/` at the root of that repository. `tactics-reference.md` describes every tactic, `proof-strategies.md` describes loops, large functions and bit vectors in more detail, and `tips-and-tricks.md` lists more pitfalls. Those files are about `⦃ ⦄` triples, and they tell the reader to use `lean-lsp-mcp`, which you do not have.
