
---

<a id="invariant-{{ i.name }}"></a>

### Additional: `{{ i.name }}`

> {{ i.natspec | replace("\n", "\n> ") }}

| Kind | Status |
|------|--------|
| Invariant on `{{ i.struct_name }}` | **{{ i.status }}** |

{% with theorems = i.theorems, file = none %}
{% include "theorems.md" %}
{% endwith %}
