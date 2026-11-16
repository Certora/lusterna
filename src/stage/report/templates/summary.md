| # | Property | Kind | Status | Proven theorems |
|---|----------|------|--------|-----------------|
{% for p in properties %}
| {{ p.id }} | [{{ p.title }}](#property-{{ p.id }}) | {% if p.struct_name %}invariant on `{{ p.struct_name }}`{% else %}rule{% endif %} | {{ p.status }} | {{ p.proven_thms }}/{{ p.total_thms }} |
{% endfor %}

A property has the first of these statuses that one of its theorems has:

- **Refuted**: a theorem is false as stated. Its section gives the counterexample.
- **Dropped write**: a proof holds only because the translation drops a write through a `RefMut` that the conclusion depends on.
- **Open**: a theorem is neither proven nor refuted.
- **Proven with assumptions**: a theorem is proven only with added hypotheses. Its section lists them.
- **Proven**: every theorem is proven as stated.

A theorem of an induction step that the natspec names as an exception to the invariant has the status **Exception** and does not count. A property that was not formalized has the status **Skipped**. If the prove stage was skipped, every formalized property has the status **Not run**.
