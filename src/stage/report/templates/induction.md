{% for s in inductions %}
### `{{ s.name }}`

| Function | Case |{% for id in s.invariants %} [P{{ id }}](#property-{{ id }}) |{% endfor %}

|----------|------|{% for id in s.invariants %}----|{% endfor %}

{% for row in s.base_cases %}
| `{{ row.function }}` | base case |{% for cell in row.cells %} {{ cell or "-" }} |{% endfor %}

{% endfor %}
{% for row in s.steps %}
| `{{ row.function }}` | step |{% for cell in row.cells %} {{ cell or "-" }} |{% endfor %}

{% endfor %}
{% if s.unmodelled_steps %}

Operations that the translation does not model, with the write that it drops:

{% for u in s.unmodelled_steps %}
- `{{ u.function }}`: {{ u.write }}
{% endfor %}
{% endif %}
{% if s.helpers %}

Helpers, covered by the operations that call them:

{% for h in s.helpers %}
- `{{ h.function }}`, called by `{{ h.callers | join("`, `") }}`
{% endfor %}
{% endif %}

{% else %}
No property is an invariant.
{% endfor %}
