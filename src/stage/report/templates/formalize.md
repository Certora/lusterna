- New theorems: {% if formalize.new_theorems %}`{{ formalize.new_theorems | join("`, `") }}`{% else %}none{% endif %}

- Updated theorems: {% if formalize.updated_theorems %}`{{ formalize.updated_theorems | join("`, `") }}`{% else %}none{% endif %}

- Removed theorems: {% if formalize.removed_theorems %}`{{ formalize.removed_theorems | join("`, `") }}`{% else %}none{% endif %}

