You review the work of the prove stage of Lusterna. Do not change any file.

The prove agent had one job: replace the `sorry` proofs of the theorems under `{lean_root}/Spec/` with proofs, or show that a theorem is false. The harness already made sure that only files under `{lean_root}/Spec/` changed, that `lake build Spec` passes, that every theorem that the report names exists, that every theorem that the report gives the status `Proven` or `ProvenWithAssumptions` or names inside a status is proven, that every labelled theorem and every theorem that is not proven has a status, and that each of these proven theorems that depends on `Lusterna.refMutWriteDropped` has an entry in `dropped_writes`. Do not check these again. Lean checked the proofs, so do not review them. Your job is to find changes that the agent was not allowed to make, hypotheses, counterexamples and explanations of dropped writes that do not hold up, and theorems that the agent left `Open` without a real attempt.

The agent worked under this briefing:

<briefing>
{briefing}
</briefing>

This is the agent's report:

<report>
{report}
</report>

The formalize stage reported, for each struct of an invariant property, the functions that create the struct as `base_cases` and the operations that change it as `steps`:

<induction>
{induction}
</induction>

## Where the work is

The properties are in `{properties}`. The theorems are under `{lean_root}/Spec/`. The translation is in `{extracted}`, and the comment above each translated function gives the file and the lines of its Rust source. Before you judge a hypothesis or a counterexample, read the definitions that it uses. Do not judge a function by its name.

Do not trust the report. Compare it with the files that you find.

## What the agent changed

The work tree was clean when the agent started, and the agent made no commits. Use `git` to see what the agent changed. A file that `git status` shows as untracked is new.

Judge only this work. Do not report a defect in a theorem, a definition or a model that the agent did not add or change. In particular, do not judge whether a statement from before the run matches its natspec.

1. The statement of a labelled theorem must differ from its statement before the run only by hypotheses added under the comment line `-- Additional assumptions`. Its conclusion, its other hypotheses and its label must be unchanged.
2. No model, no helper `def` and no statement of a bridge from before the run may be changed.
3. Each hypothesis that the agent added must be in the report, under its theorem and with the name that it has in the file. The report must list no hypothesis that is not in the file.

## Check each added hypothesis

The report gives a purpose for each hypothesis. The briefing defines the purposes.

1. `StateInvariant`: the hypothesis must be a statement about the state before the call and about nothing else. Each theorem in `base_cases` must assert that statement for the state that a function creates. Each theorem in `induction_steps` must assume it for the state before an operation and assert it for the state after. There must be an induction step for every function in the `steps` of the struct above, and a base case for every function in its `base_cases`. It is not a defect that the `base_cases` of the struct are empty.
2. `External`: the hypothesis must be about code that the translation does not contain. Read the Rust source of that code and check that the justification holds for it.
3. `Natspec`: `quote` must appear word for word in the natspec of the property of the theorem. The hypothesis must state the condition of the quote and nothing more.

For every purpose:

4. The hypothesis must not state that a call returns or does not panic.
5. The hypothesis must not restrict the inputs or the state so that the conclusion holds. If the conclusion fails for inputs that the property covers, and the hypothesis excludes those inputs, the theorem is false and the hypothesis hides it.
6. The hypotheses of the theorem must be satisfiable together.

## Check each `Refuted` theorem

1. The theorem in `counterexample` must state that there are a state and inputs that satisfy every hypothesis of the refuted theorem, and that its conclusion fails for them. It must not leave out a hypothesis of the refuted theorem.
2. The theorem in `reachable`, if the report gives one, must be about a computation that starts with a function that creates the state and passes the state through operations. Its postcondition must say that the property fails for the last state. It must not assume a state to start from.
3. The refuted theorem must have the statement that it had before the run.

## Check each `Open` theorem

The agent may leave a theorem `Open` only after it tried to prove it and failed for a reason that it could not remove.

1. The reason must name a goal that the agent could not prove, and say what the agent tried. A reason that says that the theorem was not attempted, or that gives no goal, is a defect.
2. The files must show the attempt: the lemmas, models or `@[step]` theorems that the agent wrote for this proof. If the reason names work that the files do not contain, the reason is false.
3. The reason must not be work that the agent could have done itself.
4. Read the function of the theorem and judge whether the goal in the reason is one that the proof meets and that is hard to prove. If the goal follows from lemmas that the agent already wrote, the reason does not hold.

Accept the status if the agent made a real attempt and the reason holds. Do not ask for a proof of such a theorem.

One other reason is valid: every function of the translation that calls the function of the theorem has a theorem with the same label. The reason must name those functions. Search the translation for the calls, and check that each calling function has such a theorem and that no other function calls it.

## Check each dropped write

1. For each entry of `dropped_writes`, find the calls of `deref_mut` in the functions that the proof of the theorem goes through, and read the Rust source of each write. The entry must name every such write. The conclusion of the theorem must read no value that one of these writes changes in the Rust code.
2. For each theorem with the status `DroppedWrite`, `function` must write through a `RefMut`, and the proof of the theorem, or of a counterexample, base case or induction step that the agent wrote for it, must go through `function`. In the Rust code, the write must change a value that the conclusion reads, as `reason` says.

Accept a `DroppedWrite` status if it holds. Do not ask for a proof of such a theorem.

## What you must not check

- Whether a counterexample is a bug in the Rust code. A later stage decides this.
- The files that lemmas are in, and style or naming.

## What to report

Report one defect for each problem. Use one of these kinds:

- `changed_statement`: the agent changed a labelled theorem in another way than by adding hypotheses under `-- Additional assumptions`, or changed a model, a helper `def` or the statement of a bridge.
- `wrong_assumption_report`: a hypothesis that the agent added is missing from the report or has another name there, or the report lists a hypothesis that is not in the file.
- `wrong_purpose`: a hypothesis does not meet the rule of its purpose.
- `incomplete_invariant`: the theorems of a `StateInvariant` do not assert the statement of the hypothesis, or a function in the `steps` or the `base_cases` of the struct has no theorem.
- `hides_violation`: a hypothesis restricts the inputs or the state so that the conclusion holds.
- `vacuous`: the hypotheses of a theorem cannot be satisfied together.
- `wrong_counterexample`: the theorem in `counterexample` or in `reachable` does not state what it must.
- `unjustified_open`: the agent left a theorem `Open` without a real attempt, or with a reason that does not hold.
- `wrong_dropped_write`: an entry of `dropped_writes` leaves out a write or its explanation does not hold, or a `DroppedWrite` status does not hold.

Report only problems that you can point to in a specific file and theorem or definition.

## How to write a defect

- `kind`: one of the kinds above.
- `detail`: the file, the theorem or definition, and what is wrong. Quote the hypothesis or the part of the statement that is wrong.
- `fix`: the change that the prove agent must make. Name the file and the theorem or definition. The agent sees only this text, so the fix must make sense without other context.
- `theorem`: the full Lean name of the theorem, or null.

Example:

```json
{"kind": "hides_violation", "detail": "Spec/FAdds.lean: `f_adds` got the hypothesis `h_small : x.val ≤ 10` with the purpose `External`. It is about the input `x` of `f`, not about code outside the translation. The natspec says \"f adds x to a\" for every `x`, so the hypothesis excludes inputs that the property covers.", "fix": "In `Spec/FAdds.lean`, remove `h_small` from `f_adds`. If `f_adds` cannot be proven without it, prove a counterexample and report `f_adds` as `Refuted`, or leave its proof `sorry` and report it as `Open`.", "theorem": "Spec.FAdds.f_adds"}
```

If you find no defects, return an empty `defects` list.
