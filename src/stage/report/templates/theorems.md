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
<summary>Benign interior mutability</summary>

The translation does not model writes through a `RefMut` (interior mutability).

Each theorem below is about a Rust function that makes such writes, itself or through a function that it calls. A proof of such a theorem holds for the Rust code only if its conclusion does not depend on these writes; otherwise the theorem is **Unprovable**. Each entry explains why the conclusion does not depend on them:

{% for t in dropped %}
- `{{ t.name }}`: {{ t.dropped_writes }}
{% endfor %}

Benign here means only that the conclusion of the theorem does not depend on the writes. The values that the writes change can still lack an induction step; see [Induction](#induction).

</details>
{% endif %}
