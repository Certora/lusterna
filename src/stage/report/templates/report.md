# Verification report: `{{ crate_name }}`

## Summary

{% include "summary.md" %}

## Induction

{% if formalize %}
{% include "induction.md" %}
{% else %}
Skipped.
{% endif %}

## Properties

{% for p in properties %}
{% include "property.md" %}
{% endfor %}

## Stage details

### Pre-translate

{% if pre_translate %}
{% include "pre_translate.md" %}
{% else %}
Skipped.
{% endif %}

### Translate

{% if translate %}
{% include "translate.md" %}
{% else %}
Skipped.
{% endif %}

### Formalize

{% if formalize %}
{% include "formalize.md" %}
{% else %}
Skipped.
{% endif %}
