# ERC-20 — Design Document

## Purpose
A minimal, dependency-free ERC-20 token implementation in pure Rust, mirroring
the standard interface: `total_supply`, `balance_of`, `transfer`, `approve`,
`allowance`, and `transfer_from`, plus supply-changing `mint` and `burn`.

## Implementation
A single struct `Erc20` in `src/lib.rs` holding:
- `total_supply: u128`
- `balances: BTreeMap<Address, Amount>` — addresses are plain `u64` identifiers
- `allowances: BTreeMap<(Address, Address), Amount>` — keyed by (owner, spender)

There is no notion of `msg.sender`; the caller passes `from`/`spender`
explicitly. All fallible operations return `Result<(), Erc20Error>` with
variants `InsufficientBalance`, `InsufficientAllowance`, `SupplyOverflow` —
no panics.

## Functional requirements
- `balance_of` / `allowance` return 0 for unknown addresses/pairs.
- `mint(to, n)` increases `to`'s balance and the total supply by `n`; fails
  with `SupplyOverflow` if the total supply would exceed `u128::MAX`.
  Individual balances therefore cannot overflow.
- `burn(from, n)` decreases balance and supply; fails with
  `InsufficientBalance` if `balance_of(from) < n`.
- `transfer(from, to, n)` moves `n` from `from` to `to`; fails with
  `InsufficientBalance` if underfunded. Self-transfers are allowed and are
  no-ops on the balance.
- `approve(owner, spender, n)` sets (not adds to) the allowance.
- `transfer_from(spender, from, to, n)` requires
  `allowance(from, spender) >= n` and `balance_of(from) >= n`; on success it
  performs the transfer and decreases the allowance by `n`. On failure,
  state is unchanged.

## Invariants
- The sum of all balances equals `total_supply` at all times.
- No operation panics; errors are surfaced via `Erc20Error`.
- A failed operation leaves the state unchanged.

## Non-requirements
- No events/logging, no decimals/name/symbol metadata.
- No access control on `mint`/`burn` (any caller may invoke them).
- No infinite-allowance (`u128::MAX` is decremented like any other value).
- Performance beyond `BTreeMap`'s O(log n) lookups is out of scope.
