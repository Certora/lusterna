
---

<a id="invariant-{{ i.name }}"></a>

#### Additional: {{ i.title }}

> {{ i.natspec | replace("\n", "\n> ") }}

| Kind | Definition | Formalization | Status |
|------|------------|---------------|--------|
| Invariant on `{{ i.struct_name }}` | `{{ i.name }}` | `{{ i.file }}` | **{{ i.status }}** |

{% with theorems = i.theorems, file = i.file %}
{% include "theorems.md" %}
{% endwith %}
