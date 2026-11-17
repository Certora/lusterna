<a id="invariant-{{ i.name }}"></a>

### `{{ i.name }}`

> {{ i.natspec | replace("\n", "\n> ") }}

This invariant is on `{{ i.struct_name }}`. Its theorems are the base cases and induction steps of `{{ i.struct_name }}` in [Induction](#induction).

Status: **{{ i.status }}**

#### Theorems

{% with theorems = i.theorems %}
{% include "theorems.md" %}
{% endwith %}
