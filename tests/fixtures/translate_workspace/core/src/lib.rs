pub fn max(a: u32, b: u32) -> u32 {
    if a > b {
        a
    } else {
        b
    }
}

pub fn clamp(x: u32, lo: u32, hi: u32) -> u32 {
    if x < lo {
        lo
    } else {
        min(x, hi)
    }
}

pub fn min(a: u32, b: u32) -> u32 {
    if a < b {
        a
    } else {
        b
    }
}
