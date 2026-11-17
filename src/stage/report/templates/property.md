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

{% with theorems = p.theorems %}
{% include "theorems.md" %}
{% endwith %}
{% else %}

#### Theorems

Skipped: this run has no output of the formalize stage.

{% endif %}
