<a id="property-{{ p.id }}"></a>

### P{{ p.id }}: {{ p.title }}

> {{ p.natspec | replace("\n", "\n> ") }}

| Kind | Formalization | Status |
|------|---------------|--------|
| {% if p.struct_name %}Invariant on `{{ p.struct_name }}`{% else %}Rule over `{{ p.fns | join("`, `") }}`{% endif %} | {% if p.file %}`{{ p.file }}`{% else %}None{% endif %} | **{{ p.status }}** |

{% if p.skip_reason %}

#### Not formalized

Formalize did not state this property as Lean theorems, so it is not verified. Reason: {{ p.skip_reason.kind }}. {{ p.skip_reason.details }}

{% elif p.file %}

{% with theorems = p.theorems, file = p.file %}
{% include "theorems.md" %}
{% endwith %}
{% else %}

#### Theorems

Skipped: this run has no output of the formalize stage.

{% endif %}
