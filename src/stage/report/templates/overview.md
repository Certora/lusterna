| # | Property | Kind | Status | Proven theorems |
|---|----------|------|--------|-----------------|
{% for p in properties %}
| {{ p.id }} | [{{ p.title }}](#property-{{ p.id }}) | {% if p.struct_name %}invariant on `{{ p.struct_name }}`{% else %}rule{% endif %} | {{ p.status }} | {{ p.proven_thms }}/{{ p.total_thms }} |
{% endfor %}
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

<a id="proven-with-assumptions"></a>

#### Proven with Assumptions

Properties that have status **Proven\*** have at least one theorem whose proof relies on additional assumptions. A proof with an added assumption only holds where the assumption holds. Each assumption is of one of the kinds below. The table lists them from the least to the most review that they need:

| Kind | Assumes | Backed by | Review |
|------|---------|-----------|--------|
| State invariant | An invariant holds for the state before the call. It is an invariant property or one of the [additional invariants](#additional-invs). An induction step can assume its own invariant for another value of its struct. | The Lean proofs of the invariant. Its status shows whether it is verified. | Check the status of the invariant. |
| Natspec | A condition that the natspec of the property states and the theorem lacks. | The specification. | Check the quote against the natspec. |
| Caller precondition | A condition on the arguments that every caller of the function establishes. | An LLM that read each caller that the assumption lists. | Check that each caller establishes the condition. |
| External | A condition on code outside the translation, which Lean cannot check. | The justification of an LLM. | Check the justification against that code. |
| System | A fact that the runtime of the platform guarantees for a value that it produces. | The claim of an LLM about the runtime. | Check the claim against the documentation of the runtime. |
| Reasonable | A condition that no code establishes. It excludes only values that, by its justification, no execution of the program reaches. | Nothing. The condition does not hold for every input. | Decide for each assumption whether to accept it. |
