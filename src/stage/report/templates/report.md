# Lusterna Verification Report

## Contents

- [Summary](#summary)
  - [Scope](#scope)
  - [Trust Assumptions](#trust-assumptions)
- [Properties](#properties)
  - [Overview](#overview)
  - [Details](#details)
- [Induction](#induction)
- [Stage details](#stage-details)
  - [Pre-translate](#details-pre-translate)
  - [Translate](#details-translate)
  - [Formalize](#details-formalize)

<a id="summary"></a>

## Summary

{% include "summary.md" %}

<a id="properties"></a>

## Properties

<a id="overview"></a>

### Overview

{% include "overview.md" %}

<a id="details"></a>

### Details

Each section gives the property as the spec states it, the Lean theorems that formalize it, and the result of each theorem.

Invariants are proven by induction over the base cases and induction steps that Lusterna infers. An invariant can be **incomplete** if an induction step is missing, or if a function changes its struct through interior mutability.
Refer to [Induction](#induction) for more details on the established inductions principles and their limitations.

An invariant can name exceptions that break it by design. In that case, the respective induction step theorems have the status **Exception** and do not count toward the status of the property.

A property that was not formalized has the status **Skipped**. If the prove stage did not finish, every formalized property has the status **Not run**.

{% for p in properties %}
{% include "property.md" %}
{% endfor %}
{% for i in additional_invariants %}
{% include "invariant.md" %}
{% endfor %}

<a id="induction"></a>

## Induction

{% if formalize %}
{% include "induction.md" %}
{% else %}
Skipped: this run has no output of the formalize stage.
{% endif %}

<a id="stage-details"></a>

## Stage details

<a id="details-pre-translate"></a>

### Pre-translate

Pre-translate runs only if the crate does not build on the host. An agent then changes the crate so that it builds, without changing what it computes on its target, and a separate agent checks the changes.

{% if pre_translate %}
{% include "pre_translate.md" %}
{% else %}
Skipped: this run has no output of the pre-translate stage.
{% endif %}

<a id="details-translate"></a>

### Translate

Charon and Aeneas translate the code that the entrypoints reach to Lean. Where they fail on the Rust code, an agent replaces the code with an equivalent version, and a separate agent checks the replacement.

{% if translate %}
{% include "translate.md" %}
{% else %}
Skipped: this run has no output of the translate stage.
{% endif %}

<a id="details-formalize"></a>

### Formalize

Formalize states each property as Lean theorems without proofs. A separate agent checks that the theorems state what the property says.

{% if formalize %}
{% include "formalize.md" %}
{% else %}
Skipped: this run has no output of the formalize stage.
{% endif %}
