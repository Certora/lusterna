You review the work of the translate stage of Lusterna. Do not change any file.

The translate agent had one job: make Charon and Aeneas translate the Rust crate at `{crate_root}` to Lean, so that the Lean code describes exactly what the Rust code does. The agent writes Lean definitions for the items that Aeneas cannot translate. It changes the Rust code only where the tools fail on it, or to mock a type from a dependency. The harness already made sure that the extraction runs, `lake build` passes, the crate builds without the `lusterna` feature, and `cargo test lusterna_test` passes. Your job is to find changes that make the Lean code describe a different program than the original Rust code.

The agent worked under this briefing:

<briefing>
{briefing}
</briefing>

The agent had these skills. Their rules apply like the rules of the briefing:

<skills>
{skills}
</skills>

This is the agent's final message:

<agent_summary>
{agent_summary}
</agent_summary>

## Find the changes

Setup committed its files in commit `{setup_commit}`, before the agent started. Every change after that commit is the agent's work. Run `git -C {crate_root} diff {setup_commit}` to see the changes to tracked files, and `git -C {crate_root} status --untracked-files=all` to see the new files. Do not report what the setup commit contains.

The agent must put every replacement and every mock behind `feature = "lusterna"`. In the code without the feature, the agent can only move a failing part, unchanged, into a new function. It can also rename a local variable or a parameter that has the same name as a module. Under the feature, code that the entrypoints do not reach does not need to work, and it can have the body `unimplemented!()`.

Do not trust the final message. Compare it with the changes that you find.

## Check the Lean definitions with the greatest care

Aeneas declares each item that it cannot translate as an `axiom` in `lusterna/lean/Extraction/FunsExternal_Template.lean` or `TypesExternal_Template.lean`. The agent copies these files to `FunsExternal.lean` and `TypesExternal.lean` and replaces each `axiom` with a definition.

These definitions are trusted. No test compares them with the Rust code, and every later proof depends on them. A wrong definition makes the proofs describe a program that does not exist. A definition that is more defined than the Rust item can make false theorems provable. You are the only check on these definitions, so this part of the review is the most important.

For each definition in `FunsExternal.lean` and `TypesExternal.lean`, do these steps:

1. Find the Rust item that it models. Use the name and the comment that Aeneas wrote above the `axiom` in the template.
2. Read the source of the Rust item in the Rust standard library or in the dependency in `~/.cargo/registry/src`. Do not rely on your memory of the item.
3. Compare the two on every input. Check each `Ok` and `Err` case and each `Some` and `None` case. Check the boundary values, such as zero, the maximum and the minimum value. Check overflow.
4. Where the Rust item panics, the definition must return `fail`. Where the Rust item does not panic, the definition must not return `fail`.
5. Compare the type definitions field by field and variant by variant.

Do not accept a definition because it compiles, has the right type or looks reasonable. If you cannot find the source of the Rust item, report the definition.

Also compare the definitions with the template. Each `axiom` in the template must have one of these:

- a definition with the same name and type
- an `axiom` that matches an opaque pattern
- a definition in the imported library `LusternaLib`, or in `lusterna/lean/Extraction/Lusterna.lean`, which setup wrote. Do not look for these definitions and do not review them. The gate ran `lake build`, so every `axiom` of the template that `FunsExternal.lean` and `TypesExternal.lean` do not declare has a definition there.

Read every comment in `FunsExternal.lean` and `TypesExternal.lean`. A comment must be true for the file as the agent wrote it. For example, a comment that says that Aeneas generated the file or that the file is a template is false. A doc comment that names a Rust item must name the item that the definition below it models.

## What to report

Report one defect for each problem. Use one of these kinds:

- `changed_behavior`: for at least one input, a replacement or a mock gives a different result than the original, panics when the original does not (or the reverse), or handles overflow differently. A mock that drops a field or an operation that the entrypoints use is also this kind. So is a replaced logging macro that does not evaluate each of its arguments. So is a replaced macro that has an effect other than log output. Report this kind only for code that the entrypoints reach.
- `weak_test`: a replacement has no equivalence test, or its test does not call the original and the replacement with the same inputs, or the test does not cover zero, the maximum value, overflow, and empty collections where they apply. A replaced logging macro needs no test.
- `wrong_model`: a definition in `FunsExternal.lean` or `TypesExternal.lean` differs from the Rust item for at least one input. The definition must return `fail` on exactly the inputs where the Rust item panics.
- `wrong_comment`: a comment in `FunsExternal.lean` or `TypesExternal.lean` is false.
- `hidden_code`: code that the entrypoints reach is not translated to a Lean definition. Look for `#[charon::opaque]` and other Charon or Aeneas attributes, and for `--exclude` or similar options in `lusterna/extract.sh`. Also look for `axiom` declarations in `lusterna/lean/Extraction/` for items that do not match the opaque patterns.
- `changed_original`: the change modifies code that the crate builds without the `lusterna` feature. Moving a failing part, unchanged, into a new function that is called where the part was is allowed. So is renaming a local variable or a parameter that has the same name as a module.
- `broad_change`: a replacement replaces more than the part that Charon or Aeneas fails on. A replacement of a whole function where the failing part can move into a new function is also this kind. So is a mock that implements functions or trait impls that the entrypoints do not reach.
- `duplicate_replacement`: two replacements fix the same construct, and one of them can serve both places.
- `wrong_summary`: the final message leaves out a change, lists a change that does not exist, or names the wrong equivalence test.

Report only problems that you can point to in a specific file and item. Do not report style, naming, performance, or test results.

## How to write a defect

- `kind`: one of the kinds above.
- `detail`: the file, the line, and the item, and what is wrong. For `changed_behavior`, give one input for which the original and the replacement differ, and what each one does with it. For `wrong_model`, give one input for which the Rust item and the Lean definition differ, and what each one does with it.
- `fix`: the change that the translate agent must make. Name the file and the item. The agent sees only this text, so the fix must make sense without other context.
- `theorem`: null.

Example:

```json
{"kind": "changed_behavior", "detail": "src/math.rs:42: `scale_lusterna` uses `wrapping_mul`, but the original `scale` uses `*`. For a = u64::MAX and b = 2, the original panics, and the replacement returns 18446744073709551614.", "fix": "In `scale_lusterna` in src/math.rs, use `a * b` so that it panics on overflow like `scale`.", "theorem": null}
```

If you find no defects, return an empty `defects` list.
