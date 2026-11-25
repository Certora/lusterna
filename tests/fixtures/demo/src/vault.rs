pub struct Vault {
    total: u64,
    shares: u64,
}

impl Vault {
    pub fn new() -> Self {
        Vault {
            total: 0,
            shares: 0,
        }
    }

    pub fn shares(&self) -> u64 {
        self.shares
    }

    pub fn deposit(&mut self, amount: u64) -> bool {
        match (self.total.checked_add(amount), self.shares.checked_add(amount)) {
            (Some(total), Some(shares)) => {
                self.set(total, shares);
                true
            }
            _ => false,
        }
    }

    pub fn withdraw(&mut self, shares: u64) -> bool {
        if shares > self.shares {
            return false;
        }
        self.set(self.total - shares, self.shares - shares);
        true
    }

    pub fn reset(&mut self) {
        self.total = 0;
        self.shares = 1;
    }

    fn set(&mut self, total: u64, shares: u64) {
        self.total = total;
        self.shares = shares;
    }
}
