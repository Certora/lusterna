//! Stub of the `fixed` crate, version 1.23.1, for the type
//! `fixed::types::U68F60`, in the Charon build of a target crate.
//!
//! Charon builds the target crate with this crate in place of `fixed`.
//! lusterna makes all items
//! of `fixed` opaque, so Charon and Aeneas use only their signatures, and
//! the Lean model gives them their meaning. Only the bodies that rustc runs
//! while it compiles the target crate are implemented: the `const fn`s that
//! the target crate uses in constants. These compute the same results as
//! `fixed`.

use core::fmt::{Debug, Display, Formatter, Result as FmtResult};
use core::str::FromStr;

use types::U68F60;

pub mod types {
    //! `U68F60`: an unsigned fixed-point number with 68 integer bits and 60
    //! fractional bits, stored as a `u128` of its bits.

    use core::ops::{Add, AddAssign, Div, DivAssign, Mul, MulAssign, Rem, Sub, SubAssign};

    use super::traits::{FromFixed, ToFixed};

    /// `fixed::types::U68F60`. The value is `bits / 2^60`.
    #[derive(Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
    #[repr(transparent)]
    pub struct U68F60 {
        bits: u128,
    }

    impl U68F60 {
        /// Zero.
        pub const ZERO: U68F60 = U68F60::from_bits(0);
        /// One.
        pub const ONE: U68F60 = U68F60::from_bits(1 << 60);
        /// The smallest positive value, `2^-60`.
        pub const DELTA: U68F60 = U68F60::from_bits(1);
        /// The smallest value, zero.
        pub const MIN: U68F60 = U68F60::from_bits(0);
        /// The largest value, `2^68 - 2^-60`.
        pub const MAX: U68F60 = U68F60::from_bits(u128::MAX);
        /// The number of integer bits.
        pub const INT_NBITS: u32 = 68;
        /// The number of fractional bits.
        pub const FRAC_NBITS: u32 = 60;

        /// The number with the given bits.
        pub const fn from_bits(bits: u128) -> U68F60 {
            U68F60 { bits }
        }

        /// The bits of the number.
        pub const fn to_bits(self) -> u128 {
            self.bits
        }

        /// The number with the value `src`. Panics if it does not fit.
        pub const fn const_from_int(src: u128) -> U68F60 {
            let shifted = src << 60;
            if (shifted >> 60) != src {
                panic!("overflow");
            }
            U68F60::from_bits(shifted)
        }

        /// `self` divided by the integer `rhs`. Panics if `rhs` is zero.
        pub const fn unwrapped_div_int(self, rhs: u128) -> U68F60 {
            U68F60::from_bits(self.bits / rhs)
        }

        pub fn from_num<Src: ToFixed>(_src: Src) -> U68F60 {
            unimplemented!()
        }

        pub fn checked_from_num<Src: ToFixed>(_src: Src) -> Option<U68F60> {
            unimplemented!()
        }

        pub fn to_num<Dst: FromFixed>(self) -> Dst {
            unimplemented!()
        }

        pub fn checked_to_num<Dst: FromFixed>(self) -> Option<Dst> {
            unimplemented!()
        }

        pub fn saturating_to_num<Dst: FromFixed>(self) -> Dst {
            unimplemented!()
        }

        pub fn int(self) -> U68F60 {
            unimplemented!()
        }

        pub fn frac(self) -> U68F60 {
            unimplemented!()
        }

        pub fn is_zero(self) -> bool {
            unimplemented!()
        }

        pub fn floor(self) -> U68F60 {
            unimplemented!()
        }

        pub fn ceil(self) -> U68F60 {
            unimplemented!()
        }

        pub fn round(self) -> U68F60 {
            unimplemented!()
        }

        pub fn checked_add(self, _rhs: U68F60) -> Option<U68F60> {
            unimplemented!()
        }

        pub fn checked_sub(self, _rhs: U68F60) -> Option<U68F60> {
            unimplemented!()
        }

        pub fn checked_mul(self, _rhs: U68F60) -> Option<U68F60> {
            unimplemented!()
        }

        pub fn checked_div(self, _rhs: U68F60) -> Option<U68F60> {
            unimplemented!()
        }

        pub fn saturating_add(self, _rhs: U68F60) -> U68F60 {
            unimplemented!()
        }

        pub fn saturating_sub(self, _rhs: U68F60) -> U68F60 {
            unimplemented!()
        }

        pub fn saturating_mul(self, _rhs: U68F60) -> U68F60 {
            unimplemented!()
        }

        pub fn abs_diff(self, _other: U68F60) -> U68F60 {
            unimplemented!()
        }
    }

    /// `impl Op<Rhs> for Lhs` with an unimplemented body.
    macro_rules! ops {
        ($($Op:ident $op:ident $Lhs:ty, $Rhs:ty;)*) => {$(
            impl $Op<$Rhs> for $Lhs {
                type Output = U68F60;
                fn $op(self, _rhs: $Rhs) -> U68F60 {
                    unimplemented!()
                }
            }
        )*};
    }
    ops! {
        Add add U68F60, U68F60; Add add U68F60, &U68F60; Add add &U68F60, U68F60; Add add &U68F60, &U68F60;
        Sub sub U68F60, U68F60; Sub sub U68F60, &U68F60; Sub sub &U68F60, U68F60; Sub sub &U68F60, &U68F60;
        Mul mul U68F60, U68F60; Mul mul U68F60, &U68F60; Mul mul &U68F60, U68F60; Mul mul &U68F60, &U68F60;
        Div div U68F60, U68F60; Div div U68F60, &U68F60; Div div &U68F60, U68F60; Div div &U68F60, &U68F60;
        Rem rem U68F60, U68F60; Rem rem U68F60, &U68F60; Rem rem &U68F60, U68F60; Rem rem &U68F60, &U68F60;
        Mul mul U68F60, u128; Div div U68F60, u128; Mul mul u128, U68F60;
    }

    /// `impl OpAssign<Rhs> for U68F60` with an unimplemented body.
    macro_rules! assign_ops {
        ($($OpAssign:ident $op_assign:ident $Rhs:ty;)*) => {$(
            impl $OpAssign<$Rhs> for U68F60 {
                fn $op_assign(&mut self, _rhs: $Rhs) {
                    unimplemented!()
                }
            }
        )*};
    }
    assign_ops! {
        AddAssign add_assign U68F60; AddAssign add_assign &U68F60;
        SubAssign sub_assign U68F60; SubAssign sub_assign &U68F60;
        MulAssign mul_assign U68F60; MulAssign mul_assign &U68F60; MulAssign mul_assign u128;
        DivAssign div_assign U68F60; DivAssign div_assign &U68F60; DivAssign div_assign u128;
    }

    /// `impl From<int> for U68F60` with an unimplemented body, for the integer
    /// types whose values all fit.
    macro_rules! from_int {
        ($($Int:ty),*) => {$(
            impl From<$Int> for U68F60 {
                fn from(_src: $Int) -> U68F60 {
                    unimplemented!()
                }
            }
        )*};
    }
    from_int! { bool, u8, u16, u32, u64 }

    impl PartialEq<u64> for U68F60 {
        fn eq(&self, _rhs: &u64) -> bool {
            unimplemented!()
        }
    }

    impl PartialOrd<u64> for U68F60 {
        fn partial_cmp(&self, _rhs: &u64) -> Option<core::cmp::Ordering> {
            unimplemented!()
        }
    }

    impl core::iter::Sum for U68F60 {
        fn sum<I: Iterator<Item = U68F60>>(_iter: I) -> U68F60 {
            unimplemented!()
        }
    }
}

pub mod traits {
    //! `fixed::traits::{ToFixed, FromFixed}`, for conversions between
    //! `U68F60` and the primitive integers.

    use super::types::U68F60;

    /// Conversion to `U68F60`.
    pub trait ToFixed: Copy {
        fn to_fixed(self) -> U68F60;
        fn checked_to_fixed(self) -> Option<U68F60>;
    }

    /// Conversion from `U68F60`.
    pub trait FromFixed: Copy {
        fn from_fixed(src: U68F60) -> Self;
        fn checked_from_fixed(src: U68F60) -> Option<Self>;
        fn saturating_from_fixed(src: U68F60) -> Self;
    }

    /// `ToFixed` and `FromFixed` with unimplemented bodies.
    macro_rules! conversions {
        ($($T:ty),*) => {$(
            impl ToFixed for $T {
                fn to_fixed(self) -> U68F60 {
                    unimplemented!()
                }
                fn checked_to_fixed(self) -> Option<U68F60> {
                    unimplemented!()
                }
            }
            impl FromFixed for $T {
                fn from_fixed(_src: U68F60) -> $T {
                    unimplemented!()
                }
                fn checked_from_fixed(_src: U68F60) -> Option<$T> {
                    unimplemented!()
                }
                fn saturating_from_fixed(_src: U68F60) -> $T {
                    unimplemented!()
                }
            }
        )*};
    }
    conversions! { U68F60, u8, u16, u32, u64, u128, usize, i8, i16, i32, i64, i128, isize }
}

/// The traits of `fixed::prelude`.
pub mod prelude {
    pub use super::traits::{FromFixed, ToFixed};
}

/// `fixed::ParseFixedError`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ParseFixedError;

impl Display for ParseFixedError {
    fn fmt(&self, _f: &mut Formatter<'_>) -> FmtResult {
        unimplemented!()
    }
}

impl std::error::Error for ParseFixedError {}

impl U68F60 {
    pub fn from_str(_s: &str) -> Result<U68F60, ParseFixedError> {
        unimplemented!()
    }
}

impl FromStr for U68F60 {
    type Err = ParseFixedError;

    fn from_str(_s: &str) -> Result<U68F60, ParseFixedError> {
        unimplemented!()
    }
}

impl Display for U68F60 {
    fn fmt(&self, _f: &mut Formatter) -> FmtResult {
        unimplemented!()
    }
}

impl Debug for U68F60 {
    fn fmt(&self, _f: &mut Formatter) -> FmtResult {
        unimplemented!()
    }
}

#[cfg(test)]
mod tests {
    //! Compares the implemented `const fn`s with the ones of the same name in
    //! `fixed` 1.23.1.

    use std::panic::catch_unwind;

    use fixed_real::types::U68F60 as Real;
    use proptest::prelude::*;

    use super::types::U68F60;

    #[test]
    fn constants() {
        assert_eq!(U68F60::ZERO.to_bits(), Real::ZERO.to_bits());
        assert_eq!(U68F60::ONE.to_bits(), Real::ONE.to_bits());
        assert_eq!(U68F60::DELTA.to_bits(), Real::DELTA.to_bits());
        assert_eq!(U68F60::MIN.to_bits(), Real::MIN.to_bits());
        assert_eq!(U68F60::MAX.to_bits(), Real::MAX.to_bits());
        assert_eq!(U68F60::INT_NBITS, Real::INT_NBITS);
        assert_eq!(U68F60::FRAC_NBITS, Real::FRAC_NBITS);
    }

    proptest! {
        #[test]
        fn from_bits_to_bits(bits in any::<u128>()) {
            prop_assert_eq!(U68F60::from_bits(bits).to_bits(), Real::from_bits(bits).to_bits());
        }

        #[test]
        fn const_from_int(n in prop_oneof![any::<u128>(), 0..(1u128 << 68), (1u128 << 68)..]) {
            let ours = catch_unwind(|| U68F60::const_from_int(n).to_bits()).ok();
            let real = catch_unwind(|| Real::const_from_int(n).to_bits()).ok();
            prop_assert_eq!(ours, real);
        }

        #[test]
        fn unwrapped_div_int(bits in any::<u128>(), n in prop_oneof![Just(0u128), any::<u128>(), 1..10_000u128]) {
            let ours = catch_unwind(|| U68F60::from_bits(bits).unwrapped_div_int(n).to_bits()).ok();
            let real = catch_unwind(|| Real::from_bits(bits).unwrapped_div_int(n).to_bits()).ok();
            prop_assert_eq!(ours, real);
        }
    }
}
