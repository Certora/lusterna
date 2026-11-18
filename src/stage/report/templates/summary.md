This report summarizes the formal verification that Lusterna did for *{{ crate_name }}*.

{% set formalized = properties | selectattr("file") | list %}
Lusterna tried to verify **{{ properties | length }}** properties, of which **{{ formalized | length }}** were formalized in Lean. The formalized properties have the following verdicts:

| Proven | Open | Unprovable | Refuted |
|--------|------|------------|---------|
| {{ formalized | selectattr("status", "in", ["Proven", "Proven\\*"]) | list | length }} | {{ formalized | selectattr("status", "eq", "Open") | list | length }} | {{ formalized | selectattr("status", "eq", "Unprovable") | list | length }} | {{ formalized | selectattr("status", "eq", "Refuted") | list | length }} |

Each property is expressed in one or more Lean theorems. A property has the first of these statuses that one of its theorems has:

- **Refuted**: a theorem is false as stated. Its section explains the counterexample, which usually points to a bug in the code or to a property that the spec states wrongly.
- **Unprovable**: a theorem cannot be proven due to limitations of the Aeneas framework.
- **Open**: the LLM was neither able to finish the proof nor to find a counterexample.
- **Proven**: the property is formally verified, possibly relying on additional assumptions that are either verified themselves or justified with an explanation that an LLM checked individually.

### Scope

Lusterna translated the Rust code that is reachable from the items that match these paths:

{% for e in entrypoints %}
- `{{ e }}`
{% endfor %}
{% if (translate and translate.rust_changes) or (pre_translate and pre_translate.changes) %}

#### Code Adjustments
{% if translate and translate.rust_changes %}

Lusterna edited these files to work around limitations of Charon and Aeneas:

{% for file in translate.rust_changes | map(attribute="file") | unique %}
- `{{ file }}`
{% endfor %}

By default, each edit is checked by an equivalence test. [Translate Stage Details](#details-translate) describes each edit and why it was needed.
{% endif %}
{% if pre_translate and pre_translate.changes %}

Lusterna also changed the build so that the crate builds on the host. See [Pre-Translate Stage Details](#details-pre-translate).
{% endif %}
{% endif %}
