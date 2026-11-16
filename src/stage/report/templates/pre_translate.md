{% for c in pre_translate.changes %}
- `{{ c.file }}`: {{ c.change }} Reason: {{ c.reason }}
{% else %}
The crate builds on the host, the stage made no changes.
{% endfor %}
