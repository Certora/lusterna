| # | Property | Kind | Status | Proven theorems |
|---|----------|------|--------|-----------------|
{% for p in properties %}
| {{ p.id }} | [{{ p.title }}](#property-{{ p.id }}) | {% if p.struct_name %}invariant on `{{ p.struct_name }}`{% else %}rule{% endif %} | {{ p.status }} | {{ p.proven_thms }}/{{ p.total_thms }} |
{% endfor %}

A property has the first of these statuses that one of its theorems has:

- **Refuted**: a theorem is false as stated. Its section explains the counterexample, which usually points to a bug in the code or to a property that the spec states wrongly.
- **Dropped write**: a proof holds only because the translation drops a write through a `RefMut` that the conclusion depends on. The theorem is not proven for the Rust code.
- **Open**: a theorem is neither proven nor refuted.
- **Proven with assumptions**: a theorem is proven only with hypotheses that the prove stage added. Its section lists them.
- **Proven**: every theorem is proven as stated.

Note that an invariant can name exceptions that do not preserve it by design. The theorems of these operations have the status **Exception** and do not count toward the status of the property. A property that was not formalized has the status **Skipped**. If the prove stage did not run, every formalized property has the status **Not run**.
