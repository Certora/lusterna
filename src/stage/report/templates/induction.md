{% for s in inductions %}
{% if loop.first %}
An invariant is proven by induction over the functions that create or change its struct. A base case is a function that creates a value of the struct: its theorem shows that the value satisfies the invariant. An induction step is an operation that changes a value that it takes as `&mut`: its theorem shows that the operation preserves the invariant for that value. Lusterna tries to find these functions in the Rust source. A function that changes the struct and is not listed under Base cases or Induction steps is not covered by the invariant.

{% endif %}

### `{{ s.name }}`

#### Base cases

{% for f in s.base_cases %}

- `{{ f }}`
{% else %}
None. No theorem shows that a new value of the struct satisfies the invariants.
{% endfor %}

#### Induction steps

{% for f in s.steps %}

- `{{ f }}`
{% else %}
None. No theorem shows that a change to the struct preserves the invariants.
{% endfor %}

{% if s.unmodelled_steps %}

#### Unmodelled interior mutability

The translation does not model writes through a `RefMut` (interior mutability) and instead drops them.
No theorem can make a claim about the post-state of a struct value that was mutated through such a write. Therefore, **such induction steps are omitted**.

Note that a function can write through both a `RefMut` and a `&mut` borrow to a given struct. In that case, the function is kept as an induction step, but any claims only hold for the writes through the `&mut` value.

The following functions are affected by the interior mutability problem. If they do not have another `&mut` access to the struct, they are excluded from induction:

| Function | Reason |
|----------|--------|
{% for u in s.unmodelled_steps %}
| `{{ u.function }}` | {{ u.write }} |
{% endfor %}

{% endif %}

#### Helpers

A helper changes the struct, is not exported by the crate, and is only called inside other functions that change the struct. The invariants only have to hold between operations, so a helper has no induction step itself: the induction steps of its callers cover its change.

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
