#![feature(try_trait_v2)]
use std::convert::Infallible;
use std::ops::{ControlFlow, Try};

pub struct E;

pub fn returned(r: Result<u32, E>) -> ControlFlow<Result<Infallible, E>, u32> {
    r.branch()
}

pub fn consume(_: ControlFlow<Result<Infallible, E>, u32>) {}

pub fn passed(r: Result<u32, E>) {
    consume(r.branch())
}
