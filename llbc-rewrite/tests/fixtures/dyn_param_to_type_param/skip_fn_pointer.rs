mod loader;
use loader::*;

fn read(l: &dyn Loader) -> u64 {
    l.get()
}

pub fn through_pointer(a: &Acc) -> u64 {
    let f: fn(&dyn Loader) -> u64 = read;
    f(a)
}
