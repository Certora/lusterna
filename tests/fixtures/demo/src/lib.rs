pub mod math;
pub mod pairs;
pub mod unused;
pub mod vault;
pub mod wide;

const _: () = assert!(core::mem::align_of::<u128>() == 8);
