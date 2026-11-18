#### Theorems

<details>
<summary>List of Theorems</summary>

{% if file %}
The theorems in `{{ file }}` state this property.

{% endif %}
| Theorem | Status |
|---------|--------|
{% for t in theorems %}
| `{{ t.name }}` | {{ t.status }} |
{% endfor %}

</details>
{% set exceptions = theorems | selectattr("status", "eq", "Exception") | list %}
{% if exceptions %}

#### Exceptions

{% for t in exceptions %}
- `{{ t.name }}`: {{ t.reason }}
{% endfor %}
{% endif %}
{% set refuted = theorems | selectattr("status", "eq", "Refuted") | list %}
{% if refuted %}

#### Counterexamples
{% for t in refuted %}

##### `{{ t.name }}`

> {{ t.reason | replace("\n", "\n> ") }}
{% endfor %}
{% endif %}
{% set open = theorems | selectattr("status", "eq", "Open") | list %}
{% if open %}

#### Open

{% for t in open %}
- `{{ t.name }}`: {{ t.reason }}
{% endfor %}
{% endif %}
{% set assumed = theorems | selectattr("assumptions") | list %}
{% if assumed %}

#### Added assumptions

{% for t in assumed %}
- `{{ t.name }}`
{% for a in t.assumptions %}
  - `{{ a.name }}`: {{ a.purpose }} ({{ a.justification }})
{% endfor %}
{% endfor %}
{% endif %}

#### Unprovable

{% for t in theorems | selectattr("status", "eq", "Unprovable") %}
- `{{ t.name }}`: The write is in `{{ t.dropped_write_function }}`. {{ t.reason }}
{% else %}
None.
{% endfor %}
{% set dropped = theorems | selectattr("dropped_writes") | list %}
{% if dropped %}

<details>
<summary>Benign Dropped Writes</summary>

The translation drops writes through a `RefMut`. The functions of these theorems make such writes, and each entry explains why the conclusion of the theorem does not depend on them.

{% for t in dropped %}
- `{{ t.name }}`: {{ t.dropped_writes }}
{% endfor %}

</details>
{% endif %}
