You review the work of the formalize stage of Lusterna. Do not change any file.

The formalize agent had one job: state each property in `{properties}` as Lean theorems about the Aeneas translation of a Rust crate, without proving them. The harness already made sure that only files under `{lean_root}/Spec/` changed, that `lake build Spec` passes, that every theorem is `sorry`, that every labelled theorem is a WP triple whose postcondition mentions the result, contains no `Result` and does not hold for every result, that the labels match the report, and that each function in `mutating_operations` is called by a labelled theorem of its invariant. Do not check these again. Your job is to find theorems that do not mean what their property means.

The agent worked under this briefing:

<briefing>
{briefing}
</briefing>

The agent had these skills. Their rules apply like the rules of the briefing:

<skills>
{skills}
</skills>

This is the agent's report:

<report>
{report}
</report>

## Where the work is

The properties are in `{properties}`. The spec files are under `{lean_root}/Spec/`, and the report names the file of each formalized property. The translation is in `{extracted}`. Before you judge a statement, read the definitions of the functions and types that it uses. Do not judge a function by its name.

Do not trust the report. Compare it with the files that you find.

## Check each formalized property

For each theorem with the label of the property:

1. Read the property's `natspec`, then the theorem. The hypotheses and the postcondition together must state the property: not something else, not less, and not more.
2. The hypotheses may rule out inputs on which the call panics. They must not exclude other inputs or states that the property covers, and they must be satisfiable.
3. Every helper `def` that the theorem uses must mean what the property means. For a helper that restates a translated function, read that function and compare them on every input.
4. If the function returns a Rust `Result`, the postcondition must state the property in every case of it that the property talks about.

Also check that the theorems of the property cover all of it. A property about several functions or several cases needs a theorem for each part.

## Check each invariant property

1. Search the translation for the mutating operations of the struct, as the briefing defines them: the outermost functions reachable from the entrypoints that take the struct or a value containing it as `&mut` and change it. Each one must be in `mutating_operations` under the struct's name. A function that does not change the struct must not be there, nor a helper that only a mutating operation calls, nor a function whose writes to the struct the translation drops: an induction step over it holds for free.
2. Search the translation for every function that creates a value of the struct. Each one must have a base case theorem: a theorem that calls it and asserts the invariant for the created value.
3. Each induction step theorem assumes the invariant for the value before the call and asserts it for the value after the call. Any other hypothesis must only rule out inputs on which the call panics.

## Check each skipped property

- `MissingTranslation`: each listed item must really be missing from the translation, and the property must need it.
- `IllStated`: the property must really be impossible to express as stated.
- `Other`: the reason must be true and must prevent the formalization.

## Check the files

Each file holds one property, or a group of closely related properties. The theorems must not be grouped by the function that they call.

## What to report

Report one defect for each problem. Use one of these kinds:

- `wrong_statement`: the theorem states something other than its property.
- `too_weak`: the theorem states less than its property. For example, a looser bound, or a hypothesis that excludes inputs or states that the property covers.
- `too_strong`: the theorem claims more than its property.
- `vacuous`: the theorem holds for every implementation of the function. For example, its hypotheses cannot be satisfied, or a helper `def` makes the postcondition trivial in a way that the harness cannot see.
- `missing_coverage`: a part of a property has no theorem.
- `wrong_helper`: a helper `def` does not match the property, or does not match the translated function that it restates.
- `incomplete_operations`: a mutating operation of the struct of an invariant is missing from `mutating_operations`, `mutating_operations` lists a function that is not one, or a function that creates the struct has no base case theorem.
- `wrong_skip`: the reason of a skipped property does not hold.
- `wrong_grouping`: a file groups theorems by the function that they call, or mixes unrelated properties.

Report only problems that you can point to in a specific file and theorem or definition. Do not report style or naming.

## How to write a defect

- `kind`: one of the kinds above.
- `detail`: the file, the theorem or definition, and what is wrong. For `wrong_statement`, `too_weak` and `too_strong`, quote the part of the `natspec` and the part of the theorem that differ.
- `fix`: the change that the formalize agent must make. Name the file and the theorem or definition. The agent sees only this text, so the fix must make sense without other context.
- `theorem`: the full Lean name of the theorem, or null.

Example:

```json
{"kind": "too_weak", "detail": "Spec/DepositAdds.lean: `deposit_adds` assumes `h : v.total.val = 0`, but the natspec says \"deposit adds the amount to the total\" for every vault. The hypothesis excludes every vault with a non-zero total.", "fix": "In `Spec/DepositAdds.lean`, replace the hypothesis `h` of `deposit_adds` with `h : v.total.val + amount.val ≤ U64.max`, which only rules out the overflow on which `deposit` panics.", "theorem": "Spec.DepositAdds.deposit_adds"}
```

If you find no defects, return an empty `defects` list.
