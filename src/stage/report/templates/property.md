<a id="property-{{ p.id }}"></a>

### P{{ p.id }}: {{ p.title }}

> {{ p.natspec | replace("\n", "\n> ") }}

{% if p.struct_name %}
This property is an invariant on `{{ p.struct_name }}`. Its theorems are the base cases and induction steps of `{{ p.struct_name }}` in [Induction](#induction).
{% else %}
This property is a rule over `{{ p.fns | join("`, `") }}`.
{% endif %}

Status: **{{ p.status }}**

{% if p.skip_reason %}

#### Not formalized

Formalize did not state this property as Lean theorems, so it is not verified. Reason: {{ p.skip_reason.kind }}. {{ p.skip_reason.details }}

{% elif p.file %}

#### Theorems

The theorems in `{{ p.file }}` state this property.

| Theorem | Status |
|---------|--------|
{% for t in p.theorems %}
| `{{ t.name }}` | {{ t.status }} |
{% endfor %}

{% set unproven = p.theorems | selectattr("reason") | list %}
{% if unproven %}

#### Not proven

For each theorem that is not proven, the reason that the prove stage gives. For a refuted theorem, the reason explains the counterexample.

{% for t in unproven %}

##### `{{ t.name }}`: {{ t.status }}

{% if t.dropped_write_function %}
The write is in `{{ t.dropped_write_function }}`.

{% endif %}
{{ t.reason }}

{% endfor %}
{% endif %}
{% set assumed = p.theorems | selectattr("assumptions") | list %}
{% if assumed %}

#### Added assumptions

These theorems are proven only with hypotheses that the prove stage added. Each hypothesis is listed with its purpose, why it was added and what justifies it. A proof only holds where its hypotheses hold.

{% for t in assumed %}

##### `{{ t.name }}`

{% for a in t.assumptions %}

- `{{ a.name }}` ({{ a.purpose }}): {{ a.reason }} {{ a.justification }}
{% endfor %}

{% endfor %}
{% endif %}
{% set dropped = p.theorems | selectattr("dropped_writes") | list %}
{% if dropped %}

#### Dropped writes

The proofs of these theorems go through writes through a `RefMut` that the translation drops. The prove stage explains why the conclusion of each theorem does not depend on them.

{% for t in dropped %}

##### `{{ t.name }}`

{{ t.dropped_writes }}

{% endfor %}
{% endif %}
{% else %}

#### Theorems

Skipped: this run has no output of the formalize stage.

{% endif %}
