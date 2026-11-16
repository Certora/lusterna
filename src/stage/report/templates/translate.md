{% if translate.rust_changes %}
Changes to the Rust code:

{% for c in translate.rust_changes %}
- `{{ c.file }}`, `{{ c.item }}`: {{ c.replaced }} Charon or Aeneas failed on the original: {{ c.failure }}{% if c.test %} Equivalence test: `{{ c.test }}`.{% endif %}

{% endfor %}

{% endif %}
{% if translate.extract_changes %}
Changes to `lusterna/extract-args.sh`:

{% for c in translate.extract_changes %}
- {{ c.change }} Reason: {{ c.reason }}
{% endfor %}

{% endif %}
{% if translate.lean_definitions %}
Lean definitions that replace an axiom:

| Lean name | Rust item |
|-----------|-----------|
{% for d in translate.lean_definitions %}
| `{{ d.name }}` | `{{ d.rust_item }}` |
{% endfor %}

{% endif %}
{% if translate.kept_axioms %}
Axioms that stay:

| Lean name | Opaque pattern |
|-----------|----------------|
{% for a in translate.kept_axioms %}
| `{{ a.name }}` | `{{ a.opaque_pattern }}` |
{% endfor %}

{% endif %}
{% if translate.unmatched_opaque_patterns %}
Opaque patterns that match nothing: `{{ translate.unmatched_opaque_patterns | join("`, `") }}`

{% endif %}
{% if translate.notes %}
Notes:

{% for n in translate.notes %}
- {{ n }}
{% endfor %}

{% endif %}
