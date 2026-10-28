mod loader;
use loader::*;

pub struct Holder<'a> {
    pub l: &'a dyn Loader,
}

pub fn store(l: &dyn Loader) -> u64 {
    let h = Holder { l };
    h.l.get()
}
