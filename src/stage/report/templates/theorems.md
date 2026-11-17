| Theorem | Status |
|---------|--------|
{% for t in theorems %}
| `{{ t.name }}` | {{ t.status }} |
{% endfor %}

{% set unproven = theorems | selectattr("reason") | list %}
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
{% set assumed = theorems | selectattr("assumptions") | list %}
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
{% set dropped = theorems | selectattr("dropped_writes") | list %}
{% if dropped %}

#### Dropped writes

The proofs of these theorems go through writes through a `RefMut` that the translation drops. The prove stage explains why the conclusion of each theorem does not depend on them.

{% for t in dropped %}

##### `{{ t.name }}`

{{ t.dropped_writes }}

{% endfor %}
{% endif %}
