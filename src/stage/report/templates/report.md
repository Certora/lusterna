# Verification report: `{{ crate_name }}`

This report shows which properties of `{{ crate_name }}` Lusterna verified. Lusterna translates the Rust code to Lean, states each property of the spec as Lean theorems about the translation, and proves them. A theorem counts as proven only if Lean accepts its proof and the proof does not use `sorry`.

The proofs are about the Lean translation, not the Rust code itself. The section [Stage details](#stage-details) lists every change that Lusterna made to the crate or to the translation, so you can check that the translation still matches the crate.

## Summary

{% include "summary.md" %}

## Properties

Each section gives the property as the spec states it, the Lean theorems that formalize it, and the result for each theorem.
Read the reason of every theorem that is not proven, and check every added assumption: a proof with an added assumption only holds where the assumption holds. In addition, invariants may be incomplete if induction steps are missing (refer to [Induction](#induction)).

{% for p in properties %}
{% include "property.md" %}
{% endfor %}
{% if additional_invariants %}

## Additional invariants

The prove stage added these invariants, because proofs of the properties assume them. A proof that assumes an invariant holds only if the invariant holds, so each one is proven by induction like an invariant property.

{% for i in additional_invariants %}
{% include "invariant.md" %}
{% endfor %}
{% endif %}

## Induction

{% if formalize %}
{% include "induction.md" %}
{% else %}
Skipped: this run has no output of the formalize stage.
{% endif %}

## Stage details

### Pre-translate

Pre-translate runs only if the crate does not build on the host. An agent then changes the crate so that it builds, without changing what it computes on its target, and a separate agent checks the changes.

{% if pre_translate %}
{% include "pre_translate.md" %}
{% else %}
Skipped: this run has no output of the pre-translate stage.
{% endif %}

### Translate

Charon and Aeneas translate the code that the entrypoints reach to Lean. Where they fail on the Rust code, an agent replaces the code with an equivalent version, and a separate agent checks the replacement.

{% if translate %}
{% include "translate.md" %}
{% else %}
Skipped: this run has no output of the translate stage.
{% endif %}

### Formalize

Formalize states each property as Lean theorems without proofs. A separate agent checks that the theorems state what the property says.

{% if formalize %}
{% include "formalize.md" %}
{% else %}
Skipped: this run has no output of the formalize stage.
{% endif %}
