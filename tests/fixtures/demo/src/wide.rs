use uint::construct_uint;

construct_uint! {
    pub struct U256(4);
}

pub fn mul_div(a: u64, b: u64, c: u64) -> u64 {
    (U256::from(a) * U256::from(b) / U256::from(c)).as_u64()
}
