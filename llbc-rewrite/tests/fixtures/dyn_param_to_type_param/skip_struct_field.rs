mod loader;
use loader::*;

pub struct Holder<'a> {
    pub l: &'a dyn Loader,
}

fn read(l: &dyn Loader) -> u64 {
    l.get()
}

pub fn from_field(h: &Holder) -> u64 {
    read(h.l)
}
