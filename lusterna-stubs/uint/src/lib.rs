//! Stub of the `uint` crate, version 0.9.5, for the macro `construct_uint!`,
//! in the Charon build of a target crate.
//!
//! Charon builds the target crate with this crate in place of `uint`.
//! `construct_uint!` defines the type in the calling crate, with the same
//! field `pub [u64; N]` as `uint`. Each of its methods and trait impls calls
//! a function of this crate on the limbs. lusterna makes all items of `uint`
//! opaque, so Charon and Aeneas use only their signatures, and the Lean model
//! gives them their meaning. Only the bodies that rustc runs while it compiles
//! the target crate are implemented: the `const fn`s. These compute the same
//! results as `uint`.
//!
//! The type that `construct_uint!` defines has these items of `uint`:
//!
//! - `MAX`, `zero`, `one`, `max_value`, `is_zero`, `bits`, `low_u64`,
//!   `as_u32`, `as_u64`, `as_usize`, and for two or more limbs `low_u128` and
//!   `as_u128`
//! - `From` of `u8`, `u16`, `u32`, `u64`, `usize`, `i8`, `i16`, `i32`, `i64`,
//!   `isize` and `&Self`, `TryFrom<Self>` for `u64`, and for two or more limbs
//!   `From<u128>`, `From<i128>` and `TryFrom<Self>` for `u128`
//! - `+`, `-`, `/`, `%`, `<<` and `>>` with any `Into<Self>` on the right,
//!   `*` with `Self` or a primitive integer type of at most 64 bits on the
//!   right, and the assignment forms of these operators
//! - `checked_add`, `checked_sub`, `checked_mul`, `checked_div`, `checked_rem`,
//!   `checked_pow`, `overflowing_add`, `overflowing_sub`, `overflowing_mul`,
//!   `saturating_add`, `saturating_sub`, `saturating_mul`, `div_mod`, `pow`,
//!   `exp10` and `integer_sqrt`
//! - `Clone`, `Copy`, `PartialEq`, `Eq`, `PartialOrd`, `Ord`, `Hash`,
//!   `Default`, `Debug` and `Display`
//!
//! It does not have these items of `uint`: `&`, `|`, `^`, `!`, `bit`, `byte`,
//! `low_u32`, `leading_zeros`, `trailing_zeros`, `to_big_endian`,
//! `to_little_endian`, `from_big_endian`, `from_little_endian`, the
//! conversions from and to byte arrays and byte slices, `from_dec_str`,
//! `from_str_radix`, `FromStr`, `From<&str>`, `LowerHex`, `UpperHex`,
//! `abs_diff`, `overflowing_neg`, `checked_neg`, `overflowing_pow`,
//! `TryFrom<Self>` for the other integer types, `AsRef<[u64]>`, and the `quickcheck` and
//! `arbitrary` impls. This crate does not have the other items of `uint`.

#![cfg_attr(not(feature = "std"), no_std)]

use core::cmp::Ordering;
use core::fmt::{Formatter, Result as FmtResult};

#[doc(hidden)]
pub use core as core_;

/// Zero.
pub const fn zero<const N: usize>() -> [u64; N] {
    [0; N]
}

/// One.
pub const fn one<const N: usize>() -> [u64; N] {
    let mut words = [0; N];
    words[0] = 1;
    words
}

/// The largest value, `2^(64 N) - 1`.
pub const fn max_value<const N: usize>() -> [u64; N] {
    [u64::MAX; N]
}

/// The lowest limb.
pub const fn low_u64<const N: usize>(a: [u64; N]) -> u64 {
    a[0]
}

/// The two lowest limbs. `N` is at least 2.
pub const fn low_u128<const N: usize>(a: [u64; N]) -> u128 {
    ((a[1] as u128) << 64) + a[0] as u128
}

/// Whether the value is zero.
pub const fn is_zero<const N: usize>(a: [u64; N]) -> bool {
    let mut i = 0;
    while i < N {
        if a[i] != 0 {
            return false;
        }
        i += 1;
    }
    true
}

pub fn bits<const N: usize>(_a: [u64; N]) -> usize {
    unimplemented!()
}

pub fn from_u64<const N: usize>(_v: u64) -> [u64; N] {
    unimplemented!()
}

pub fn from_u128<const N: usize>(_v: u128) -> [u64; N] {
    unimplemented!()
}

pub fn from_i64<const N: usize>(_v: i64) -> [u64; N] {
    unimplemented!()
}

pub fn from_i128<const N: usize>(_v: i128) -> [u64; N] {
    unimplemented!()
}

pub fn as_u32<const N: usize>(_a: [u64; N]) -> u32 {
    unimplemented!()
}

pub fn as_u64<const N: usize>(_a: [u64; N]) -> u64 {
    unimplemented!()
}

pub fn as_usize<const N: usize>(_a: [u64; N]) -> usize {
    unimplemented!()
}

pub fn as_u128<const N: usize>(_a: [u64; N]) -> u128 {
    unimplemented!()
}

pub fn try_into_u64<const N: usize>(_a: [u64; N]) -> Result<u64, &'static str> {
    unimplemented!()
}

pub fn try_into_u128<const N: usize>(_a: [u64; N]) -> Result<u128, &'static str> {
    unimplemented!()
}

pub fn eq<const N: usize>(_a: [u64; N], _b: [u64; N]) -> bool {
    unimplemented!()
}

pub fn cmp<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Ordering {
    unimplemented!()
}

pub fn add<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn sub<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn mul<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn mul_u64<const N: usize>(_a: [u64; N], _b: u64) -> [u64; N] {
    unimplemented!()
}

pub fn div<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn rem<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn div_mod<const N: usize>(_a: [u64; N], _b: [u64; N]) -> ([u64; N], [u64; N]) {
    unimplemented!()
}

pub fn shl<const N: usize>(_a: [u64; N], _shift: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn shr<const N: usize>(_a: [u64; N], _shift: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn overflowing_add<const N: usize>(_a: [u64; N], _b: [u64; N]) -> ([u64; N], bool) {
    unimplemented!()
}

pub fn overflowing_sub<const N: usize>(_a: [u64; N], _b: [u64; N]) -> ([u64; N], bool) {
    unimplemented!()
}

pub fn overflowing_mul<const N: usize>(_a: [u64; N], _b: [u64; N]) -> ([u64; N], bool) {
    unimplemented!()
}

pub fn checked_add<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn checked_sub<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn checked_mul<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn checked_div<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn checked_rem<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn checked_pow<const N: usize>(_a: [u64; N], _b: [u64; N]) -> Option<[u64; N]> {
    unimplemented!()
}

pub fn saturating_add<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn saturating_sub<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn saturating_mul<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn pow<const N: usize>(_a: [u64; N], _b: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn exp10<const N: usize>(_n: usize) -> [u64; N] {
    unimplemented!()
}

pub fn integer_sqrt<const N: usize>(_a: [u64; N]) -> [u64; N] {
    unimplemented!()
}

pub fn fmt<const N: usize>(_a: [u64; N], _f: &mut Formatter) -> FmtResult {
    unimplemented!()
}

/// `construct_uint!` of `uint`, with the items that the crate doc lists.
#[macro_export]
macro_rules! construct_uint {
    ( $(#[$attr:meta])* $visibility:vis struct $name:ident (1); ) => {
        $crate::construct_uint! { @construct $(#[$attr])* $visibility struct $name (1); }
    };

    ( $(#[$attr:meta])* $visibility:vis struct $name:ident ( $n_words:tt ); ) => {
        $crate::construct_uint! { @construct $(#[$attr])* $visibility struct $name ($n_words); }

        impl $name {
            pub const fn low_u128(&self) -> u128 {
                $crate::low_u128::<$n_words>(self.0)
            }

            pub fn as_u128(&self) -> u128 {
                $crate::as_u128::<$n_words>(self.0)
            }
        }

        impl $crate::core_::convert::From<u128> for $name {
            fn from(value: u128) -> $name {
                $name($crate::from_u128::<$n_words>(value))
            }
        }

        impl $crate::core_::convert::From<i128> for $name {
            fn from(value: i128) -> $name {
                $name($crate::from_i128::<$n_words>(value))
            }
        }

        impl $crate::core_::convert::TryFrom<$name> for u128 {
            type Error = &'static str;

            fn try_from(u: $name) -> $crate::core_::result::Result<u128, &'static str> {
                $crate::try_into_u128::<$n_words>(u.0)
            }
        }
    };

    ( @construct $(#[$attr:meta])* $visibility:vis struct $name:ident ( $n_words:tt ); ) => {
        #[repr(C)]
        $(#[$attr])*
        #[derive(Copy, Clone, Eq, Hash)]
        $visibility struct $name (pub [u64; $n_words]);

        impl $name {
            pub const MAX: $name = $name($crate::max_value::<$n_words>());

            pub const fn zero() -> Self {
                $name($crate::zero::<$n_words>())
            }

            pub const fn one() -> Self {
                $name($crate::one::<$n_words>())
            }

            pub const fn max_value() -> Self {
                $name($crate::max_value::<$n_words>())
            }

            pub const fn is_zero(&self) -> bool {
                $crate::is_zero::<$n_words>(self.0)
            }

            pub const fn low_u64(&self) -> u64 {
                $crate::low_u64::<$n_words>(self.0)
            }

            pub fn bits(&self) -> usize {
                $crate::bits::<$n_words>(self.0)
            }

            pub fn as_u32(&self) -> u32 {
                $crate::as_u32::<$n_words>(self.0)
            }

            pub fn as_u64(&self) -> u64 {
                $crate::as_u64::<$n_words>(self.0)
            }

            pub fn as_usize(&self) -> usize {
                $crate::as_usize::<$n_words>(self.0)
            }

            pub fn overflowing_add(self, other: $name) -> ($name, bool) {
                let (v, overflow) = $crate::overflowing_add::<$n_words>(self.0, other.0);
                ($name(v), overflow)
            }

            pub fn overflowing_sub(self, other: $name) -> ($name, bool) {
                let (v, overflow) = $crate::overflowing_sub::<$n_words>(self.0, other.0);
                ($name(v), overflow)
            }

            pub fn overflowing_mul(self, other: $name) -> ($name, bool) {
                let (v, overflow) = $crate::overflowing_mul::<$n_words>(self.0, other.0);
                ($name(v), overflow)
            }

            pub fn checked_add(self, other: $name) -> Option<$name> {
                match $crate::checked_add::<$n_words>(self.0, other.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn checked_sub(self, other: $name) -> Option<$name> {
                match $crate::checked_sub::<$n_words>(self.0, other.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn checked_mul(self, other: $name) -> Option<$name> {
                match $crate::checked_mul::<$n_words>(self.0, other.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn checked_div(self, other: $name) -> Option<$name> {
                match $crate::checked_div::<$n_words>(self.0, other.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn checked_rem(self, other: $name) -> Option<$name> {
                match $crate::checked_rem::<$n_words>(self.0, other.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn checked_pow(self, expon: $name) -> Option<$name> {
                match $crate::checked_pow::<$n_words>(self.0, expon.0) {
                    Some(v) => Some($name(v)),
                    None => None,
                }
            }

            pub fn saturating_add(self, other: $name) -> $name {
                $name($crate::saturating_add::<$n_words>(self.0, other.0))
            }

            pub fn saturating_sub(self, other: $name) -> $name {
                $name($crate::saturating_sub::<$n_words>(self.0, other.0))
            }

            pub fn saturating_mul(self, other: $name) -> $name {
                $name($crate::saturating_mul::<$n_words>(self.0, other.0))
            }

            pub fn div_mod(self, other: $name) -> ($name, $name) {
                let (q, r) = $crate::div_mod::<$n_words>(self.0, other.0);
                ($name(q), $name(r))
            }

            pub fn pow(self, expon: $name) -> $name {
                $name($crate::pow::<$n_words>(self.0, expon.0))
            }

            pub fn exp10(n: usize) -> $name {
                $name($crate::exp10::<$n_words>(n))
            }

            pub fn integer_sqrt(&self) -> $name {
                $name($crate::integer_sqrt::<$n_words>(self.0))
            }
        }

        impl $crate::core_::cmp::PartialEq for $name {
            fn eq(&self, other: &$name) -> bool {
                $crate::eq::<$n_words>(self.0, other.0)
            }
        }

        impl $crate::core_::cmp::Ord for $name {
            fn cmp(&self, other: &$name) -> $crate::core_::cmp::Ordering {
                $crate::cmp::<$n_words>(self.0, other.0)
            }
        }

        impl $crate::core_::cmp::PartialOrd for $name {
            fn partial_cmp(&self, other: &$name) -> Option<$crate::core_::cmp::Ordering> {
                Some($crate::cmp::<$n_words>(self.0, other.0))
            }
        }

        impl $crate::core_::default::Default for $name {
            fn default() -> Self {
                $name($crate::zero::<$n_words>())
            }
        }

        impl $crate::core_::fmt::Debug for $name {
            fn fmt(&self, f: &mut $crate::core_::fmt::Formatter) -> $crate::core_::fmt::Result {
                $crate::fmt::<$n_words>(self.0, f)
            }
        }

        impl $crate::core_::fmt::Display for $name {
            fn fmt(&self, f: &mut $crate::core_::fmt::Formatter) -> $crate::core_::fmt::Result {
                $crate::fmt::<$n_words>(self.0, f)
            }
        }

        impl<'a> $crate::core_::convert::From<&'a $name> for $name {
            fn from(x: &'a $name) -> $name {
                *x
            }
        }

        impl $crate::core_::convert::From<u64> for $name {
            fn from(value: u64) -> $name {
                $name($crate::from_u64::<$n_words>(value))
            }
        }

        impl $crate::core_::convert::From<i64> for $name {
            fn from(value: i64) -> $name {
                $name($crate::from_i64::<$n_words>(value))
            }
        }

        $crate::impl_from_unsigned!($name, $n_words, u8);
        $crate::impl_from_unsigned!($name, $n_words, u16);
        $crate::impl_from_unsigned!($name, $n_words, u32);
        $crate::impl_from_unsigned!($name, $n_words, usize);
        $crate::impl_from_signed!($name, $n_words, i8);
        $crate::impl_from_signed!($name, $n_words, i16);
        $crate::impl_from_signed!($name, $n_words, i32);
        $crate::impl_from_signed!($name, $n_words, isize);

        impl $crate::core_::convert::TryFrom<$name> for u64 {
            type Error = &'static str;

            fn try_from(u: $name) -> $crate::core_::result::Result<u64, &'static str> {
                $crate::try_into_u64::<$n_words>(u.0)
            }
        }

        $crate::impl_binop!($name, $n_words, Add, add, AddAssign, add_assign, add);
        $crate::impl_binop!($name, $n_words, Sub, sub, SubAssign, sub_assign, sub);
        $crate::impl_binop!($name, $n_words, Div, div, DivAssign, div_assign, div);
        $crate::impl_binop!($name, $n_words, Rem, rem, RemAssign, rem_assign, rem);
        $crate::impl_binop!($name, $n_words, Shl, shl, ShlAssign, shl_assign, shl);
        $crate::impl_binop!($name, $n_words, Shr, shr, ShrAssign, shr_assign, shr);

        impl $crate::core_::ops::Mul<$name> for $name {
            type Output = $name;

            fn mul(self, other: $name) -> $name {
                $name($crate::mul::<$n_words>(self.0, other.0))
            }
        }

        impl<'a> $crate::core_::ops::Mul<&'a $name> for $name {
            type Output = $name;

            fn mul(self, other: &'a $name) -> $name {
                $name($crate::mul::<$n_words>(self.0, other.0))
            }
        }

        impl<'a> $crate::core_::ops::Mul<&'a $name> for &'a $name {
            type Output = $name;

            fn mul(self, other: &'a $name) -> $name {
                $name($crate::mul::<$n_words>(self.0, other.0))
            }
        }

        impl<'a> $crate::core_::ops::Mul<$name> for &'a $name {
            type Output = $name;

            fn mul(self, other: $name) -> $name {
                $name($crate::mul::<$n_words>(self.0, other.0))
            }
        }

        impl $crate::core_::ops::MulAssign<$name> for $name {
            fn mul_assign(&mut self, other: $name) {
                *self = $name($crate::mul::<$n_words>(self.0, other.0))
            }
        }

        $crate::impl_mul_primitive!($name, $n_words, u8);
        $crate::impl_mul_primitive!($name, $n_words, u16);
        $crate::impl_mul_primitive!($name, $n_words, u32);
        $crate::impl_mul_primitive!($name, $n_words, u64);
        $crate::impl_mul_primitive!($name, $n_words, usize);
        $crate::impl_mul_primitive!($name, $n_words, i8);
        $crate::impl_mul_primitive!($name, $n_words, i16);
        $crate::impl_mul_primitive!($name, $n_words, i32);
        $crate::impl_mul_primitive!($name, $n_words, i64);
        $crate::impl_mul_primitive!($name, $n_words, isize);
    };
}

/// `From<$from>` for an unsigned integer type `$from` of at most 64 bits.
#[macro_export]
#[doc(hidden)]
macro_rules! impl_from_unsigned {
    ($name:ident, $n_words:tt, $from:ty) => {
        impl $crate::core_::convert::From<$from> for $name {
            fn from(value: $from) -> $name {
                $name($crate::from_u64::<$n_words>(value as u64))
            }
        }
    };
}

/// `From<$from>` for a signed integer type `$from` of at most 64 bits.
#[macro_export]
#[doc(hidden)]
macro_rules! impl_from_signed {
    ($name:ident, $n_words:tt, $from:ty) => {
        impl $crate::core_::convert::From<$from> for $name {
            fn from(value: $from) -> $name {
                $name($crate::from_i64::<$n_words>(value as i64))
            }
        }
    };
}

/// The operator `$Op` with any `Into<$name>` on the right, for `$name` and
/// `&$name`, and its assignment form `$OpAssign`. Each one calls `$f`.
#[macro_export]
#[doc(hidden)]
macro_rules! impl_binop {
    ($name:ident, $n_words:tt, $Op:ident, $op:ident, $OpAssign:ident, $op_assign:ident, $f:ident) => {
        impl<T> $crate::core_::ops::$Op<T> for $name
        where
            T: Into<$name>,
        {
            type Output = $name;

            fn $op(self, other: T) -> $name {
                let other: $name = other.into();
                $name($crate::$f::<$n_words>(self.0, other.0))
            }
        }

        impl<'a, T> $crate::core_::ops::$Op<T> for &'a $name
        where
            T: Into<$name>,
        {
            type Output = $name;

            fn $op(self, other: T) -> $name {
                let other: $name = other.into();
                $name($crate::$f::<$n_words>(self.0, other.0))
            }
        }

        impl<T> $crate::core_::ops::$OpAssign<T> for $name
        where
            T: Into<$name>,
        {
            fn $op_assign(&mut self, other: T) {
                let other: $name = other.into();
                *self = $name($crate::$f::<$n_words>(self.0, other.0))
            }
        }
    };
}

/// `*` with the integer type `$other` on the right, for `$name` and `&$name`,
/// and `*=`. As in `uint`, the right side is cast to `u64`, so a negative
/// value becomes a large one.
#[macro_export]
#[doc(hidden)]
macro_rules! impl_mul_primitive {
    ($name:ident, $n_words:tt, $other:ty) => {
        impl $crate::core_::ops::Mul<$other> for $name {
            type Output = $name;

            fn mul(self, other: $other) -> $name {
                $name($crate::mul_u64::<$n_words>(self.0, other as u64))
            }
        }

        impl<'a> $crate::core_::ops::Mul<&'a $other> for $name {
            type Output = $name;

            fn mul(self, other: &'a $other) -> $name {
                $name($crate::mul_u64::<$n_words>(self.0, *other as u64))
            }
        }

        impl<'a> $crate::core_::ops::Mul<&'a $other> for &'a $name {
            type Output = $name;

            fn mul(self, other: &'a $other) -> $name {
                $name($crate::mul_u64::<$n_words>(self.0, *other as u64))
            }
        }

        impl<'a> $crate::core_::ops::Mul<$other> for &'a $name {
            type Output = $name;

            fn mul(self, other: $other) -> $name {
                $name($crate::mul_u64::<$n_words>(self.0, other as u64))
            }
        }

        impl $crate::core_::ops::MulAssign<$other> for $name {
            fn mul_assign(&mut self, other: $other) {
                *self = $name($crate::mul_u64::<$n_words>(self.0, other as u64))
            }
        }
    };
}

/// Code with untyped integer literals compiles with the stub's
/// `construct_uint!`. rustc gives such a literal the type `i32`.
#[cfg(test)]
#[allow(dead_code)]
mod literal_tests {
    crate::construct_uint! {
        pub struct Stub(4);
    }

    fn literals(a: Stub) -> Stub {
        Stub::from(0) + 1 - a * 10 / 3 % 2 << 1 >> 1
    }
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    uint_real::construct_uint! {
        pub struct Real(4);
    }

    #[test]
    fn constants_match_uint() {
        assert_eq!(super::zero::<4>(), Real::zero().0);
        assert_eq!(super::one::<4>(), Real::one().0);
        assert_eq!(super::max_value::<4>(), Real::max_value().0);
        assert_eq!(super::max_value::<4>(), Real::MAX.0);
    }

    proptest! {
        #[test]
        fn low_u64_matches_uint(a: [u64; 4]) {
            prop_assert_eq!(super::low_u64::<4>(a), Real(a).low_u64());
        }

        #[test]
        fn low_u128_matches_uint(a: [u64; 4]) {
            prop_assert_eq!(super::low_u128::<4>(a), Real(a).low_u128());
        }

        #[test]
        fn is_zero_matches_uint(a: [u64; 4], zero_limbs in 0usize..=4) {
            let mut a = a;
            for limb in a.iter_mut().take(zero_limbs) {
                *limb = 0;
            }
            prop_assert_eq!(super::is_zero::<4>(a), Real(a).is_zero());
        }
    }
}

/// Checks that `uint` computes what the Lean model `LusternaLib.Uint` says,
/// with exact results computed in 512 bits.
#[cfg(test)]
mod model_tests {
    use proptest::prelude::*;
    use std::panic::{catch_unwind, UnwindSafe};

    uint_real::construct_uint! {
        pub struct Real(4);
    }

    uint_real::construct_uint! {
        pub struct Wide(8);
    }

    /// The value of `a`.
    fn wide(a: Real) -> Wide {
        let mut w = [0; 8];
        w[..4].copy_from_slice(&a.0);
        Wide(w)
    }

    /// `2^256`.
    fn modulus() -> Wide {
        Wide::one() << 256
    }

    /// `m mod 2^256`.
    fn wrap(m: Wide) -> Real {
        Real(m.0[..4].try_into().unwrap())
    }

    /// The result of `f`, or `None` if it panics.
    fn run<T>(f: impl FnOnce() -> T + UnwindSafe) -> Option<T> {
        catch_unwind(f).ok()
    }

    /// `Some(wrap(m))` if `m` fits, and `None` otherwise.
    fn fits(m: Wide) -> Option<Real> {
        (m < modulus()).then(|| wrap(m))
    }

    /// A number with a random count of nonzero low limbs, so that small and
    /// large values are both common.
    fn num() -> impl Strategy<Value = Real> {
        (0usize..=4, any::<[u64; 4]>()).prop_map(|(limbs, mut a)| {
            for limb in a.iter_mut().skip(limbs) {
                *limb = 0;
            }
            Real(a)
        })
    }

    #[test]
    fn exp10_panics_iff_the_power_does_not_fit() {
        for n in 0..100usize {
            let exact = Wide::from(10u64).checked_pow(Wide::from(n));
            assert_eq!(run(move || Real::exp10(n)), exact.and_then(fits), "n = {n}");
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(2000))]

        #[test]
        fn add_sub_mul(a in num(), b in num(), c: u64, d: i32) {
            let (wa, wb) = (wide(a), wide(b));
            prop_assert_eq!(run(move || a + b), fits(wa + wb));
            prop_assert_eq!(run(move || a - b), (wb <= wa).then(|| wrap(wa - wb)));
            prop_assert_eq!(run(move || a * b), fits(wa * wb));
            prop_assert_eq!(run(move || a * c), fits(wa * Wide::from(c)));
            prop_assert_eq!(run(move || a * d), fits(wa * Wide::from(d as u64)));
            prop_assert_eq!(run(move || Real::from(d)), (d >= 0).then(|| Real::from(d as u64)));
        }

        #[test]
        fn checked_overflowing_saturating(a in num(), b in num()) {
            let (wa, wb) = (wide(a), wide(b));
            let max = modulus() - 1;
            prop_assert_eq!(a.checked_add(b), fits(wa + wb));
            prop_assert_eq!(a.checked_sub(b), (wb <= wa).then(|| wrap(wa - wb)));
            prop_assert_eq!(a.checked_mul(b), fits(wa * wb));
            prop_assert_eq!(a.overflowing_add(b), (wrap(wa + wb), wa + wb >= modulus()));
            prop_assert_eq!(a.overflowing_sub(b), (wrap(wa + modulus() - wb), wa < wb));
            prop_assert_eq!(a.overflowing_mul(b), (wrap(wa * wb), wa * wb >= modulus()));
            prop_assert_eq!(a.saturating_add(b), wrap((wa + wb).min(max)));
            prop_assert_eq!(a.saturating_sub(b), wrap(wa.saturating_sub(wb)));
            prop_assert_eq!(a.saturating_mul(b), wrap((wa * wb).min(max)));
        }

        #[test]
        fn div_rem(a in num(), b in num()) {
            let (wa, wb) = (wide(a), wide(b));
            let zero = b.is_zero();
            let q = (!zero).then(|| wrap(wa / wb));
            let r = (!zero).then(|| wrap(wa % wb));
            prop_assert_eq!(run(move || a / b), q);
            prop_assert_eq!(run(move || a % b), r);
            prop_assert_eq!(run(move || a.div_mod(b)), q.zip(r));
            prop_assert_eq!(a.checked_div(b), q);
            prop_assert_eq!(a.checked_rem(b), r);
        }

        #[test]
        fn pow(a in num(), small: u8, b in num()) {
            for e in [Real::from(small % 40), b] {
                let exact = wide(a).checked_pow(wide(e)).and_then(fits);
                prop_assert_eq!(run(move || a.pow(e)), exact);
                prop_assert_eq!(a.checked_pow(e), exact);
            }
        }

        #[test]
        fn shifts(a in num(), s in 0u64..300, big in num()) {
            let wa = wide(a);
            let shl = if s < 256 { wrap(wa << s as usize) } else { Real::zero() };
            let shr = if s < 256 { wrap(wa >> s as usize) } else { Real::zero() };
            prop_assert_eq!(run(move || a << s), Some(shl));
            prop_assert_eq!(run(move || a >> s), Some(shr));
            let fits_usize = wide(big) <= Wide::from(usize::MAX);
            prop_assert_eq!(run(move || a << big).is_some(), fits_usize);
            prop_assert_eq!(run(move || a >> big).is_some(), fits_usize);
        }

        #[test]
        fn conversions(a in num(), v: i64, w: i128) {
            let wa = wide(a);
            let below = |bits: usize| wa < (Wide::one() << bits);
            prop_assert_eq!(run(move || a.as_u32()), below(32).then(|| a.low_u64() as u32));
            prop_assert_eq!(run(move || a.as_u64()), below(64).then(|| a.low_u64()));
            prop_assert_eq!(run(move || a.as_u128()), below(128).then(|| a.low_u128()));
            prop_assert_eq!(
                run(move || a.as_usize()),
                (wa <= Wide::from(usize::MAX)).then(|| a.low_u64() as usize)
            );
            prop_assert_eq!(u64::try_from(a).ok(), below(64).then(|| a.low_u64()));
            prop_assert_eq!(u128::try_from(a).ok(), below(128).then(|| a.low_u128()));
            prop_assert_eq!(
                u64::try_from(a).err(),
                (!below(64)).then_some("integer overflow when casting to u64")
            );
            prop_assert_eq!(
                u128::try_from(a).err(),
                (!below(128)).then_some("integer overflow when casting to u128")
            );
            prop_assert_eq!(run(move || Real::from(v)), (v >= 0).then(|| Real::from(v as u64)));
            prop_assert_eq!(run(move || Real::from(w)), (w >= 0).then(|| Real::from(w as u128)));
        }

        #[test]
        fn bits_sqrt_cmp(a in num(), b in num()) {
            let (wa, wb) = (wide(a), wide(b));
            let bits = a.bits();
            prop_assert!(wa < (Wide::one() << bits));
            prop_assert!(bits == 0 || wa >= (Wide::one() << (bits - 1)));
            let r = wide(a.integer_sqrt());
            prop_assert!(r * r <= wa && wa < (r + 1) * (r + 1));
            prop_assert_eq!(a.cmp(&b), wa.cmp(&wb));
            prop_assert_eq!(a == b, wa == wb);
        }
    }
}
