# Staking Reward — Design Document

## Purpose
Compute the staking reward earned on a deposited `principal`, given a reward rate
expressed in **basis points** (1 basis point = 0.01% = 1/10000).

## Implementation
A single pure function `reward(principal: u64, rate_bps: u64) -> u64`.

## Definition
The reward is the principal scaled by the rate:

    reward = principal * rate_bps / 10000

The multiplication is performed first and the division by 10000 is applied **once, at
the very end**, truncating toward zero (integer division). This ordering matters:
scaling before dividing preserves precision, so a principal smaller than 10000 still
earns a proportional, non-zero reward when the rate is large enough.

## Functional requirements
- `reward(principal, rate_bps) = principal * rate_bps / 10000` (division applied last).
- `reward(principal, 0) = 0` (a zero rate yields no reward).
- `reward(0, rate_bps) = 0` (zero principal yields no reward).
- The reward is monotonic non-decreasing in `rate_bps`.
- Full-rate check: `reward(principal, 10000) = principal` (10000 bps = 100%).

## Guaranteed input range
- `principal <= 10^12`
- `rate_bps <= 10000`
Within this range `principal * rate_bps <= 10^16 < 2^63`, so the intermediate product
does not overflow u64.

## Non-requirements
- Compounding, time-weighting, or accrual over multiple periods are out of scope.
- Rounding modes other than truncation are out of scope.
