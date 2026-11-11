---
name: aeneas-tactics
description: The tactics of Aeneas for proofs about a translation: `step`, `scalar_tac` and the others, which one fits which goal, loops and recursion, and the usual pitfalls. Use when you write or repair a proof in `Spec/`.
---

# Aeneas proof tactics

This skill is about proofs in the usual Aeneas style, which show that a call returns and that its result satisfies a postcondition. For a theorem with a hypothesis that the call returns, of the form `∃ r, f args = ok r`, see the skill `lusterna:aeneas-partial-correctness`.

The examples are about this translation. They leave out the namespaces.

```lean
-- Rust: fn get_total(&self) -> u64 { self.a + self.b - self.c }
def Struct.get_total (self : Struct) : Result U64 := do
  let i ← self.a + self.b
  i - self.c

-- Rust: fn twice(&self) -> u64 { let t = self.get_total(); t + t }
def Struct.twice (self : Struct) : Result U64 := do
  let t ← self.get_total
  t + t
```

## Seeing the goal

You have no editor. Build one module with `lake build Spec.<Module>`, for example `lake build Spec.Lemmas.Scalar`. An error message shows the goal at the place of the error. To see the goal at another place, put the tactic `trace_state` there and build: the build output shows the goal.

## The standard proof

Unfold the function, then let `step` handle one call at a time. `step` looks for a theorem with the attribute `@[step]` about the function that the goal calls next, applies it, and leaves its hypotheses as goals.

```lean
@[step]
theorem Struct.get_total.total_spec (s : Struct)
    (h1 : s.a.val + s.b.val ≤ U64.max)
    (h2 : s.c.val ≤ s.a.val + s.b.val) :
    s.get_total ⦃ r => r.val = s.a.val + s.b.val - s.c.val ⦄ := by
  unfold Struct.get_total
  -- `self.a + self.b`: names the sum `i` and the fact about it `h_i`
  step as ⟨i, h_i⟩
  -- `i - self.c`
  step as ⟨r, h_r⟩
  scalar_tac
```

The forms of `step`:

- `step`: applies the `@[step]` theorem that matches the next call.
- `step as ⟨x, h1, h2⟩`: also names the result and the parts of its postcondition, in order. Without `as` they get names that you cannot refer to.
- `step with my_theorem`: applies the given theorem. Use it when several `@[step]` theorems match the call, or when none is registered.
- `step*`: repeats `step` and splits on each branch. The proof above also works as `unfold Struct.get_total; step*`.
- `step*?`: like `step*`, and prints the proof script that it found. Use it to get a script that you then repair where it stops.

A theorem with `@[step]` about a function is then used at each call of the function:

```lean
theorem Struct.twice.spec (s : Struct)
    (h1 : s.a.val + s.b.val ≤ U64.max)
    (h2 : s.c.val ≤ s.a.val + s.b.val)
    (h3 : 2 * (s.a.val + s.b.val - s.c.val) ≤ U64.max) :
    s.twice ⦃ r => r.val = 2 * (s.a.val + s.b.val - s.c.val) ⦄ := by
  unfold Struct.twice
  step with Struct.get_total.total_spec as ⟨t, h_t⟩
  step as ⟨r, h_r⟩
  scalar_tac
```

If `step` fails at a call, the function has no `@[step]` theorem, or the hypotheses of its theorem cannot be shown. Search for a theorem about the function before you write one.

## Branches

When the function has an `if` or a `match`, split the goal with `split`, and prove each case. `simp_ifs` simplifies an `if` whose condition follows from the hypotheses.

If the function starts with an `if` or a `match`, split before the first `step`. Otherwise `step` can apply the theorem that you are proving to itself, and Lean then reports a termination error for a proof that looks finished.

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

If the function uses the operator `loop`, apply `loop.spec_decr_nat`. It takes a measure in `Nat`, an invariant and a postcondition. You then show that the body keeps the invariant and decreases the measure when it continues, and gives the postcondition when it is done.

## Large functions and slow proofs

- If a function is long, state a lemma for a part of it and use the lemma in the proof of the whole.
- Give a hard fact as a `have` before the tactic that needs it.
- If a proof takes more than a minute to check, split it into lemmas. Raise `maxHeartbeats` only if that does not help.

## The Aeneas documentation

`lakefile.toml` of the Lean project gives the path of the `aeneas` package, which ends in `backends/lean`. The documentation is in the directory `documentation/` at the root of that repository. `tactics-reference.md` describes every tactic, `proof-strategies.md` describes loops, large functions and bit vectors in more detail, and `tips-and-tricks.md` lists more pitfalls. Those files tell the reader to use `lean-lsp-mcp`, which you do not have.
