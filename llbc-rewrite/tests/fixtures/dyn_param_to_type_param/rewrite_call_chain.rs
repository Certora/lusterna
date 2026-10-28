mod loader;
use loader::*;

fn helper(l: &dyn Loader, x: u64) -> u64 {
    l.get() + x
}

pub fn entry(l: &dyn Loader, m: &mut dyn Loader, x: u64) -> u64 {
    let y = helper(l, x);
    m.put(y);
    y
}

pub fn caller(a: &Acc, b: &mut Other) -> u64 {
    entry(a, b, 1)
}
