//! A simple ERC-20 token implementation in pure Rust.
//!
//! Mirrors the ERC-20 interface: `total_supply`, `balance_of`, `transfer`,
//! `approve`, `allowance`, and `transfer_from`. Addresses are plain `u64`
//! identifiers and balances are `u128`. All operations use checked
//! arithmetic and return an error instead of panicking.

use std::collections::BTreeMap;

pub type Address = u64;
pub type Amount = u128;

#[derive(Debug, PartialEq, Eq)]
pub enum Erc20Error {
    InsufficientBalance,
    InsufficientAllowance,
    SupplyOverflow,
}

#[derive(Debug, Default)]
pub struct Erc20 {
    total_supply: Amount,
    balances: BTreeMap<Address, Amount>,
    allowances: BTreeMap<(Address, Address), Amount>,
}

impl Erc20 {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn total_supply(&self) -> Amount {
        self.total_supply
    }

    pub fn balance_of(&self, owner: Address) -> Amount {
        self.balances.get(&owner).copied().unwrap_or(0)
    }

    pub fn allowance(&self, owner: Address, spender: Address) -> Amount {
        self.allowances.get(&(owner, spender)).copied().unwrap_or(0)
    }

    pub fn mint(&mut self, to: Address, amount: Amount) -> Result<(), Erc20Error> {
        let new_supply = self
            .total_supply
            .checked_add(amount)
            .ok_or(Erc20Error::SupplyOverflow)?;
        // Balance cannot overflow if total supply does not.
        *self.balances.entry(to).or_insert(0) += amount;
        self.total_supply = new_supply;
        Ok(())
    }

    pub fn burn(&mut self, from: Address, amount: Amount) -> Result<(), Erc20Error> {
        let balance = self.balance_of(from);
        if balance < amount {
            return Err(Erc20Error::InsufficientBalance);
        }
        self.balances.insert(from, balance - amount);
        self.total_supply -= amount;
        Ok(())
    }

    pub fn transfer(
        &mut self,
        from: Address,
        to: Address,
        amount: Amount,
    ) -> Result<(), Erc20Error> {
        let from_balance = self.balance_of(from);
        let to_balance = self.balance_of(to);
        if from_balance < amount {
            return Err(Erc20Error::InsufficientBalance);
        }
        self.balances.insert(from, from_balance - amount);
        self.balances.insert(to, to_balance + amount);
        Ok(())
    }

    pub fn approve(&mut self, owner: Address, spender: Address, amount: Amount) {
        self.allowances.insert((owner, spender), amount);
    }

    pub fn transfer_from(
        &mut self,
        spender: Address,
        from: Address,
        to: Address,
        amount: Amount,
    ) -> Result<(), Erc20Error> {
        let allowed = self.allowance(from, spender);
        if allowed < amount {
            return Err(Erc20Error::InsufficientAllowance);
        }
        self.transfer(from, to, amount)?;
        self.allowances.insert((from, spender), allowed - amount);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALICE: Address = 1;
    const BOB: Address = 2;
    const CAROL: Address = 3;

    #[test]
    fn mint_and_balance() {
        let mut token = Erc20::new();
        token.mint(ALICE, 1000).unwrap();
        assert_eq!(token.balance_of(ALICE), 1000);
        assert_eq!(token.total_supply(), 1000);
    }

    #[test]
    fn transfer_moves_funds() {
        let mut token = Erc20::new();
        token.mint(ALICE, 1000).unwrap();
        token.transfer(ALICE, BOB, 400).unwrap();
        assert_eq!(token.balance_of(ALICE), 600);
        assert_eq!(token.balance_of(BOB), 400);
        assert_eq!(token.total_supply(), 1000);
    }

    #[test]
    fn transfer_insufficient_balance_fails() {
        let mut token = Erc20::new();
        token.mint(ALICE, 100).unwrap();
        assert_eq!(
            token.transfer(ALICE, BOB, 101),
            Err(Erc20Error::InsufficientBalance)
        );
        assert_eq!(token.balance_of(ALICE), 100);
    }

    #[test]
    fn transfer_from_respects_allowance() {
        let mut token = Erc20::new();
        token.mint(ALICE, 1000).unwrap();
        token.approve(ALICE, BOB, 300);
        token.transfer_from(BOB, ALICE, CAROL, 200).unwrap();
        assert_eq!(token.balance_of(CAROL), 200);
        assert_eq!(token.allowance(ALICE, BOB), 100);
        assert_eq!(
            token.transfer_from(BOB, ALICE, CAROL, 200),
            Err(Erc20Error::InsufficientAllowance)
        );
    }

    #[test]
    fn burn_reduces_supply() {
        let mut token = Erc20::new();
        token.mint(ALICE, 500).unwrap();
        token.burn(ALICE, 200).unwrap();
        assert_eq!(token.balance_of(ALICE), 300);
        assert_eq!(token.total_supply(), 300);
        assert_eq!(token.burn(ALICE, 400), Err(Erc20Error::InsufficientBalance));
    }

    #[test]
    fn mint_overflow_fails() {
        let mut token = Erc20::new();
        token.mint(ALICE, Amount::MAX).unwrap();
        assert_eq!(token.mint(BOB, 1), Err(Erc20Error::SupplyOverflow));
    }
}
