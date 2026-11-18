| Date | Crate Name | Commit | Lusterna Commit | Aeneas Version |
|------|------------|--------|-----------------|----------------|
| {{ date }} | {{ crate_name }} | `{{ commit[:7] }}` | N/A | `{{ aeneas_rev[:7] }}` |

This report summarizes the formal verification that Lusterna did for *{{ crate_name }}*.

{% set formalized = properties | selectattr("file") | list %}
Lusterna tried to verify **{{ properties | length }}** properties, of which **{{ formalized | length }}** were formalized in Lean. The formalized properties have the following verdicts:

| Proven | Open | Unprovable | Refuted |
| -------- | ------ | ------------ | --------- |
| {{ formalized | selectattr("status", "in", ["Proven", "Proven\\*"]) | list | length }} | {{ formalized | selectattr("status", "eq", "Open") | list | length }} | {{ formalized | selectattr("status", "eq", "Unprovable") | list | length }} | {{ formalized | selectattr("status", "eq", "Refuted") | list | length }} |

Each property is expressed in one or more Lean theorems. A property has the first of these statuses that one of its theorems has:

- **Refuted**: a theorem is false as stated. Its section explains the counterexample, which usually points to a bug in the code or to a property that the spec states wrongly.
- **Unprovable**: a theorem cannot be proven due to limitations of the Aeneas framework.
- **Open**: the LLM was neither able to finish the proof nor to find a counterexample.
- **Proven**: the property is formally verified, possibly relying on additional assumptions that are either verified themselves or justified with an explanation that an LLM checked individually.

<a id="scope"></a>

### Scope

Lusterna translated the Rust code that is reachable from the items that match these paths:

{% for e in entrypoints %}

- `{{ e }}`
{% endfor %}
{% if (translate and translate.rust_changes) or (pre_translate and pre_translate.changes) %}

#### Code Adjustments

{% if translate and translate.rust_changes %}

Lusterna edited these files to work around limitations of Charon and Aeneas:

{% for file in translate.rust_changes | map(attribute="file") | unique %}

- `{{ file }}`
{% endfor %}

By default, each edit is checked by an equivalence test. [Translate Stage Details](#details-translate) describes each edit, why it was needed, and its equivalence test, if it has one.
{% endif %}
{% if pre_translate and pre_translate.changes %}

Lusterna also changed the build so that the crate builds on the host. See [Pre-Translate Stage Details](#details-pre-translate).
{% endif %}
{% endif %}

<a id="trust-assumptions"></a>

### Trust Assumptions

Every claim of this report holds only under these assumptions:

1. **Formalization**: the LLM faithfully formalized the informal specification of each property into [Lean theorems](#details) that capture the intended property.
2. **Induction**: the LLM identified every function that creates or changes the struct of an invariant as a [base case or an induction step](#induction).
3. **Added assumptions**: each [assumption](#proven-with-assumptions) that the LLM added to a proof holds where the proof uses it. For assumptions that are not proven, the LLM's given justification is trusted.
4. **Interior mutability**: where a proof goes through a write through a `RefMut` that the translation does not model, the argument of the LLM that the conclusion of the theorem does not depend on that write is correct.
5. **Lean definitions**: the LLM faithfully modelled each item that Aeneas could not translate as a [Lean definition](#lean-definitions) that computes the same as the item.
6. **Rewrites**: the code that the LLM [rewrote](#changes-to-the-rust-code) to work around Charon and Aeneas computes the same as the original. Equivalence tests check most rewrites on random inputs.
7. **Exceptions**: the functions that the informal specification names as exceptions to an invariant are meant to break it.
8. **Host build**: the [changes](#details-pre-translate) that the LLM made so that the crate builds on the host do not change what the crate computes on its target.
9. **Toolchain**: Charon and Aeneas translate the Rust code correctly, Lean checks the proofs correctly, and the Lean libraries that the translation uses model the Rust standard library and the dependencies of the crate correctly.

> [!WARNING]
> Assumptions 1 to 8 rest on work that an LLM did and only an LLM reviewed. No proof checks them. Inspect them before you rely on the claims of this report.
