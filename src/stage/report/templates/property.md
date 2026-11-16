<a id="property-{{ p.id }}"></a>

### P{{ p.id }}: {{ p.title }}

> {{ p.natspec | replace("\n", "\n> ") }}

**Kind:** {% if p.struct_name %}invariant on `{{ p.struct_name }}`{% else %}rule over `{{ p.fns | join("`, `") }}`{% endif %}

**Status:** {{ p.status }}

{% if p.skip_reason %}
**Not formalized:** {{ p.skip_reason.kind }}. {{ p.skip_reason.details }}

{% elif p.file %}
**Formalization** (`{{ p.file }}`):

| Theorem | Status |
|---------|--------|
{% for t in p.theorems %}
| `{{ t.name }}` | {{ t.status }} |
{% endfor %}

{% for t in p.theorems if t.reason %}

#### `{{ t.name }}`: {{ t.status }}

{% if t.dropped_write_function %}
The write is in `{{ t.dropped_write_function }}`.

{% endif %}
{{ t.reason }}

{% endfor %}
{% for t in p.theorems if t.assumptions %}

#### `{{ t.name }}`: added assumptions

{% for a in t.assumptions %}

- `{{ a.name }}` ({{ a.purpose }}): {{ a.reason }} {{ a.justification }}
{% endfor %}

{% endfor %}
{% for t in p.theorems if t.dropped_writes %}
#### `{{ t.name }}`: dropped writes

{{ t.dropped_writes }}

{% endfor %}
{% else %}
**Formalization:** Skipped.

{% endif %}
