{% if translate.rust_changes %}
#### Changes to the Rust code

The Rust code that Charon or Aeneas failed on, what the agent replaced it with, and the test that checks that the replacement computes the same as the original.

{% for c in translate.rust_changes %}
- `{{ c.file }}`, `{{ c.item }}`: {{ c.replaced }} Charon or Aeneas failed on the original: {{ c.failure }}{% if c.test %} Equivalence test: `{{ c.test }}`.{% endif %}

{% endfor %}

{% endif %}
{% if translate.extract_changes %}
#### Changes to the extraction

The changes to the options that `lusterna/extract-args.sh` passes to Charon and Aeneas.

{% for c in translate.extract_changes %}
- {{ c.change }} Reason: {{ c.reason }}
{% endfor %}

{% endif %}
{% if translate.lean_definitions %}
#### Lean definitions

Aeneas translates some Rust items to axioms, Lean declarations without a definition. The agent gave these a Lean definition of what the Rust item computes, so the proofs can use it.

| Lean name | Rust item |
|-----------|-----------|
{% for d in translate.lean_definitions %}
| `{{ d.name }}` | `{{ d.rust_item }}` |
{% endfor %}

{% endif %}
{% if translate.kept_axioms %}
#### Axioms

These items stay axioms because they match an opaque pattern. Lean has no definition for them, so the proofs hold for any implementation of them.

| Lean name | Opaque pattern |
|-----------|----------------|
{% for a in translate.kept_axioms %}
| `{{ a.name }}` | `{{ a.opaque_pattern }}` |
{% endfor %}

{% endif %}
{% if translate.unmatched_opaque_patterns %}
#### Unused opaque patterns

These opaque patterns match no item, so they have no effect: `{{ translate.unmatched_opaque_patterns | join("`, `") }}`

{% endif %}
{% if translate.notes %}
#### Notes

{% for n in translate.notes %}
- {{ n }}
{% endfor %}

{% endif %}
