mod loader;
use loader::*;

fn read(l: &dyn Loader) -> u64 {
    l.get()
}

pub fn pick(c: bool, a: &Acc, b: &Other) -> u64 {
    let l: &dyn Loader = if c { a } else { b };
    read(l)
}
