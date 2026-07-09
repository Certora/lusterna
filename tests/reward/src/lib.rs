//! Staking reward computation.

pub fn reward(principal: u64, rate_bps: u64) -> u64 {
    principal / 10000 * rate_bps
}
