{% for s in inductions %}
{% if loop.first %}
An invariant is proven by induction over the functions that create or change its struct. A base case is a function that creates a value of the struct: its theorem shows that the value satisfies the invariant. An induction step is an operation that changes a value: its theorem shows that the operation preserves the invariant. Lusterna tries to find these functions in the Rust source. A function that changes the struct and is not listed here is not covered by the invariant.

{% endif %}

### `{{ s.name }}`

#### Base cases

{% for f in s.base_cases %}

- `{{ f }}`
{% else %}
None. This means that either the relevant functions were not translated (not reached from given entry point) or they could not be modelled (see below).
{% endfor %}

#### Induction steps

{% for f in s.steps %}

- `{{ f }}`
{% else %}
None. This means that either the relevant functions were not translated (not reached from given entry point) or they could not be modelled (see below).
{% endfor %}

{% if s.unmodelled_steps %}

#### Not modelled

Functions that change a struct's value by writing through a `RefMut` (i.e. use the internal mutability pattern) currently cannot be modelled. All statements about such writes are either unprovable or invalid and Lusterna therefore does not include them in inductions.

This applies to the following functions:

| Function | Reason |
|----------|--------|
{% for u in s.unmodelled_steps %}
| `{{ u.function }}` | {{ u.write }} |
{% endfor %}

{% endif %}

#### Helpers

A helper changes the struct, is not exported by the crate, and is only called inside functions that were already identified as induction steps. The invariants only have to hold between operations, so a helper has no induction step itself: the operations that call it cover its change.

{% if s.helpers %}

| Name | Callers |
|------|---------|
{% for h in s.helpers %}
| `{{ h.function }}` | `{{ h.callers | join("`,`") }}` |
{% endfor %}
{% else %}
None.
{% endif %}

{% else %}
No property is an invariant.
{% endfor %}
