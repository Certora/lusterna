You review the work of the prove stage of Lusterna. Do not change any file.

The prove agent had one job: replace the `sorry` proofs of the theorems under `{lean_root}/Spec/` with proofs, or report why a theorem is false. Your job is to find changes that the agent was not allowed to make, hypotheses, added invariants, added inductions, counterexamples, exceptions and explanations of dropped writes that do not hold up, and theorems that the agent left `Open` or `Refuted` without a real attempt.

The agent worked under this briefing:

<briefing>
{briefing}
</briefing>

The agent had these skills for invariants. Their rules apply like the rules of the briefing:

<invariants>
{invariants}
</invariants>

This is the agent's submission:

<submission>
{submission}
</submission>

## Where the work is

The properties are in `{properties}`. The theorems are under `{lean_root}/Spec/`. The translation is in `{extracted}`, and the comment above each translated function gives the file and the lines of its Rust source. Before you judge a hypothesis or a counterexample, read the definitions that it uses. Do not judge a function by its name.

The theorem names and the statuses of the submission passed the checks of the harness. Every other statement of the submission is a claim of the agent: check it against the files and the Rust source before you rely on it.

Each section below that checks entries of the submission starts with the list of these entries. Check every entry of a list, from the first to the last, whether the agent changed its file or not. A section is done only when you checked each entry of its list.

## What the agent changed

The work tree was clean when the agent started, and the agent made no commits. So `git diff HEAD` shows the text from before the run, and `git status` shows a new file as untracked. Use `git` for the rules of this section only.

Do not report a defect in the text of a statement, a definition or a model from before the run.

1. The statement of a labelled theorem must differ from its statement before the run only by hypotheses added under the comment line `-- Additional assumptions`. Its conclusion, its other hypotheses and its label must be unchanged.
2. No model, no helper `def` and no statement of a bridge from before the run may be changed.
3. Each hypothesis that the agent added must be in the submission, under its theorem and with the name that it has in the file. The submission must list no hypothesis that is not in the file.
4. Each Lean file that the agent created must have a module doc comment `/-! ... -/` after its imports.

## Check each added hypothesis

The theorems with the status `ProvenWithAssumptions`:

{theorems_with_assumptions}

The submission gives a purpose for each hypothesis. The briefing defines the purposes.

1. `StateInvariant`: the hypothesis must be a statement about the state before the call and about nothing else. It must state the invariant that the assumption names: for `Property`, the invariant of that property in `{properties}`; for `Additional`, the `def` of that entry of `additional_invariants`.
2. `External`: the hypothesis must be about code that the translation does not contain. Read the Rust source of that code and check that the justification holds for it.
3. `Natspec`: `quote` must appear word for word in the natspec of the property of the theorem. The hypothesis must state the condition of the quote and nothing more.
4. `CallerPrecondition`: search the Rust source of the crate for every call of the function of the theorem. `callers` must list each function that calls it, and no other. For each caller, read its Rust code: the condition must hold for the arguments that it passes, on every path to the call, for the reason that its entry gives. The hypothesis may only be about the arguments of the function, including the state that the caller passes. The function must not be one for which the briefing says that this purpose does not apply.

For every purpose:

1. The hypothesis must be about the inputs and the state before the call, not about the result of the call. Only an `External` hypothesis may be about the result of a function that the translation does not contain.
2. The hypothesis must not state that a call returns or does not panic.
3. The hypothesis must not restrict the inputs or the state so that the conclusion holds, except a `CallerPrecondition` that holds as above. If the conclusion fails for inputs that the property covers, and the hypothesis excludes those inputs, the theorem is false and the hypothesis hides it.
4. The hypotheses of the theorem must be satisfiable together.

## Check each added invariant

The entries of `additional_invariants`:

{added_invariants}

Each entry of `additional_invariants` is an invariant that the agent added, under the full Lean name of its `def`.

1. The `def` must state the `natspec` of the entry: not something else, not less, and not more. The `title` must name what the `def` states.
2. No property in `{properties}` and no other entry may state the invariant or one that implies it, unless that one has a theorem with the status `Open`, `Refuted` or `Exception`.
3. The invariant and its theorems must be in `file`, a file of their own, in the shape that the skill `lusterna:invariants` gives, except for what the briefing allows.
4. `theorems` must name exactly the base cases and the induction steps of the invariant.
5. There must be a base case for every function in the `base_cases` and an induction step for every function in the `steps` of the induction of `struct`: its entry in the `induction` of the briefing, or in `additional_inductions`. It is not a defect that the `base_cases` are empty.

## Check each added induction

The entries of `additional_inductions`:

{added_inductions}

Each entry of `additional_inductions` is the induction of a struct that the `induction` of the briefing does not have. Its lists must be the ones that "The lists of an induction" in the skill `lusterna:induction` gives.

## Check each `Refuted` theorem

The theorems with the status `Refuted`:

{refuted_theorems}

`Refuted` ends the work on a theorem without a proof, so an agent that does not want to do a hard proof can use it as a way out. Be skeptical of a `reason` that is vague or that avoids the hard part of the function.

1. The values in `reason` must satisfy every hypothesis of the theorem.
2. Follow the Rust source of the function with these values. The branches and the values in `reason` must match the code, and the conclusion must fail for the result.
3. `reason` must name the invariants and the conditions of the callers that the agent checked, and say why they do not rule out the counterexample.
4. `reason` must name the goal at which the proof attempt stopped, and the files must show the lemmas or `@[step]` theorems that the agent wrote for the function of this theorem or for a function that it calls, if the attempt needed any.
5. The refuted theorem must have the statement that it had before the run.

## Check each `Exception` theorem

The theorems with the status `Exception`:

{exception_theorems}

An `Exception` that the formalize stage gave in the briefing needs no check. For any other, the natspec must name the function of the theorem as an exception to the invariant, and the reason must be readable from the natspec, not from the code.

## Check each `Open` theorem

The theorems with the status `Open`:

{open_theorems}

The agent may leave a theorem `Open` only after it tried to prove it and failed for a reason that it could not remove.

1. The reason must name a goal that the agent could not prove, and say what the agent tried. A reason that says that the theorem was not attempted, or that gives no goal, is a defect.
2. The files must show the attempt: the lemmas, models or `@[step]` theorems that the agent wrote for the function of this theorem or for a function that it calls. If the reason names work that the files do not contain, the reason is false.
3. A reason that names a model, a lemma or a `@[step]` theorem that the proof needs and that the agent did not write does not hold: the briefing tells the agent to write it.
4. Read the function of the theorem and judge whether the goal in the reason is one that the proof meets. If the goal follows from lemmas that the agent already wrote, the reason does not hold.

Report `unjustified_open` for a theorem that fails one of these rules. Do not ask for a proof of a theorem that passes them.

## Check each dropped write

The entries of `dropped_writes`:

{dropped_writes}

The theorems with the status `DroppedWrite`:

{dropped_write_theorems}

1. For each entry of `dropped_writes`, find the calls of `deref_mut` in the functions that the proof of the theorem goes through, and read the Rust source of each write. The entry must name every such write. The conclusion of the theorem must read no value that one of these writes changes in the Rust code.
2. For each theorem with the status `DroppedWrite`, `function` must write through a `RefMut`, and the proof of the theorem, or of a base case or induction step that the agent wrote for it, must go through `function`. In the Rust code, the write must change a value that the conclusion reads, as `reason` says.

Accept a `DroppedWrite` status if it holds. Do not ask for a proof of such a theorem.

## What you must not check

- The conditions of "When you are done" in the briefing. The harness checked them.
- Whether a proof is correct. Lean checked the proofs. Read the lemmas that the agent wrote only for what they state.
- Whether a counterexample is a bug in the Rust code, or whether the program can reach it. A later stage decides this.
- The files that lemmas are in, and style or naming.

## What to report

Report one defect for each problem. Use one of these kinds:

- `changed_statement`: the agent changed a labelled theorem in another way than by adding hypotheses under `-- Additional assumptions`, or changed a model, a helper `def` or the statement of a bridge.
- `wrong_assumption_submission`: a hypothesis that the agent added is missing from the submission or has another name there, or the submission lists a hypothesis that is not in the file.
- `wrong_purpose`: a hypothesis does not meet the rule of its purpose.
- `wrong_invariant`: the `def` of an added invariant does not state its `natspec`, or a property or another added invariant states it or one that implies it, and all of its theorems are proven.
- `incomplete_invariant`: the theorems of an added invariant are not in a file of their own in the shape of the skill, `theorems` does not name exactly its base cases and induction steps, or a function in the `base_cases` or the `steps` of its induction has no theorem.
- `incomplete_induction`: a function is missing from the list of an entry of `additional_inductions` that the skill `lusterna:induction` gives it, a list names a function that does not belong there, or an entry of `helpers` or `unmodelled_steps` does not hold.
- `hides_violation`: a hypothesis is about the result of the call, or restricts the inputs or the state so that the conclusion holds.
- `vacuous`: the hypotheses of a theorem cannot be satisfied together.
- `wrong_counterexample`: the `reason` of a `Refuted` theorem does not satisfy the hypotheses, does not match the code, leaves out the invariants and caller conditions that the agent checked, or shows no attempt to prove the theorem.
- `wrong_exception`: an `Exception` that the formalize stage did not give does not follow from the natspec.
- `unjustified_open`: the agent left a theorem `Open` without a real attempt, or with a reason that does not hold.
- `wrong_dropped_write`: an entry of `dropped_writes` leaves out a write or its explanation does not hold, or a `DroppedWrite` status does not hold.
- `missing_header`: a Lean file that the agent created has no module doc comment.

Report only problems that you can point to in a specific file and theorem or definition, or for `missing_header` in a specific file. If you cannot decide whether an entry of the submission holds, report it with the kind of its check. Say in `detail` what you could not decide, and in `fix` what the entry must state so that it can be checked.

## How to write a defect

- `kind`: one of the kinds above.
- `detail`: the file, the theorem or definition, and what is wrong. Quote the hypothesis or the part of the statement that is wrong.
- `fix`: the change that the prove agent must make. Name the file and the theorem or definition. The agent sees only this text, so the fix must make sense without other context.
- `theorem`: the full Lean name of the theorem, or null.

Example:

```json
{"kind": "hides_violation", "detail": "Spec/FAdds.lean: `f_adds` got the hypothesis `h_small : x.val ≤ 10` with the purpose `External`. It is about the input `x` of `f`, not about code outside the translation. The natspec says \"f adds x to a\" for every `x`, so the hypothesis excludes inputs that the property covers.", "fix": "In `Spec/FAdds.lean`, remove `h_small` from `f_adds`. If `f_adds` cannot be proven without it, leave its proof `sorry` and report it as `Refuted` with a counterexample in `reason`, or as `Open`.", "theorem": "Spec.FAdds.f_adds"}
```

If you find no defects, return an empty `defects` list.
