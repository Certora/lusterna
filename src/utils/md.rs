use std::fmt::Display;

/// Returns `text` as inline code in Markdown.
pub fn code(text: impl Display) -> String {
    format!("`{text}`")
}

/// Returns `items` as a Markdown bullet list.
pub fn bullet_list<T: Display>(items: impl IntoIterator<Item = T>) -> String {
    let mut list = String::new();
    for item in items {
        if !list.is_empty() {
            list.push('\n');
        }
        list.push_str(&format!("- {item}"));
    }
    list
}

/// Returns `items` as a Markdown bullet list of inline code.
pub fn code_list<T: Display>(items: impl IntoIterator<Item = T>) -> String {
    bullet_list(items.into_iter().map(code))
}

/// Returns `items` as a Markdown numbered list.
pub fn numbered_list<T: Display>(items: impl IntoIterator<Item = T>) -> String {
    let mut list = String::new();
    for (index, item) in items.into_iter().enumerate() {
        if !list.is_empty() {
            list.push('\n');
        }
        list.push_str(&format!("{}. {item}", index + 1));
    }
    list
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bullet_list_puts_each_item_on_a_line() {
        assert_eq!(bullet_list(["a", "b"]), "- a\n- b");
    }

    #[test]
    fn code_list_puts_each_item_in_inline_code() {
        assert_eq!(code_list(["a", "b"]), "- `a`\n- `b`");
    }

    #[test]
    fn numbered_list_counts_from_one() {
        assert_eq!(numbered_list(["a", "b"]), "1. a\n2. b");
    }

    #[test]
    fn list_of_no_items_is_empty() {
        assert_eq!(bullet_list(Vec::<String>::new()), "");
        assert_eq!(numbered_list(Vec::<String>::new()), "");
    }
}
