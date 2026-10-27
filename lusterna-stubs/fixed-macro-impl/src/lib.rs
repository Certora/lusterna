//! Replacement for `fixed-macro-impl` 1.2.0. `fixed!` does not expand in the
//! Charon build.

use proc_macro::TokenStream;

/// Expands to a compile error.
#[proc_macro]
pub fn fixed(_input: TokenStream) -> TokenStream {
    r#"compile_error!("lusterna: `fixed!` is not available in the Charon build. Replace each use under `#[cfg(feature = \"lusterna\")]` with `U68F60::from_bits(<bits>)`")"#
        .parse()
        .unwrap()
}
