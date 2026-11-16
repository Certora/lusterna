{% for c in pre_translate.changes %}
{% if loop.first %}
The changes to the crate:

{% endif %}
- `{{ c.file }}`: {{ c.change }} Reason: {{ c.reason }}
{% else %}
The crate builds on the host, so the stage made no changes.
{% endfor %}
