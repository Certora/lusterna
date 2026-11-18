| # | Property | Kind | Status | Proven theorems |
|---|----------|------|--------|-----------------|
{% for p in properties %}
| {{ p.id }} | [{{ p.title }}](#property-{{ p.id }}) | {% if p.struct_name %}invariant on `{{ p.struct_name }}`{% else %}rule{% endif %} | {{ p.status }} | {{ p.proven_thms }}/{{ p.total_thms }} |
{% endfor %}

Properties that have status **Proven\*** have at least one theorem whose proof relies on additional assumptions. Each of those assumptions is of one of the following kinds:

- **State invariant**: an invariant holds for the state before the call. The invariant is an invariant property or one of the [additional invariants](#additional-invs), and its own status shows whether it is verified.
- **External**: a condition on code outside the translation, which Lean cannot check. The assumption gives its justification.
- **Natspec**: the natspec of the property states the condition, and the theorem lacks it.
- **Caller precondition**: a condition on the arguments that every caller of the function establishes.

A proof with an added assumption only holds where the assumption holds.
{% if additional_invariants %}

<a id="additional-invs"></a>

#### Additional Invariants

Some of the proofs of the properties above rely on additional assumptions on the reachable state. Lusterna formalized and tried to verify the following additional invariants:

| Invariant | Kind | Status | Proven theorems |
|-----------|------|--------|-----------------|
{% for i in additional_invariants %}
| [{{ i.title }}](#invariant-{{ i.name }}) | invariant on `{{ i.struct_name }}` | {{ i.status }} | {{ i.proven_thms }}/{{ i.total_thms }} |
{% endfor %}
{% endif %}
