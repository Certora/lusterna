pub fn checked_total(a: u64, b: u64) -> Option<u64> {
    a.checked_add(b)
}

pub fn log_bucket(x: u64) -> u32 {
    if x == 0 {
        return 0;
    }
    x.ilog2() + 1
}
