mod loader;
use loader::*;

pub trait Reader {
    fn read(&self, l: &dyn Loader) -> u64;
}

impl Reader for Acc {
    fn read(&self, l: &dyn Loader) -> u64 {
        l.get()
    }
}
