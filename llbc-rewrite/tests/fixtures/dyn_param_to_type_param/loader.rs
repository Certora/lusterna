pub trait Loader {
    fn get(&self) -> u64;
    fn put(&mut self, v: u64);
}

pub struct Acc {
    pub v: u64,
}

impl Loader for Acc {
    fn get(&self) -> u64 {
        self.v
    }
    fn put(&mut self, v: u64) {
        self.v = v
    }
}

pub struct Other;

impl Loader for Other {
    fn get(&self) -> u64 {
        0
    }
    fn put(&mut self, _: u64) {}
}
