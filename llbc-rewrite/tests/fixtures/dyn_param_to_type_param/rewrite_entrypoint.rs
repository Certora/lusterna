mod loader;
use loader::*;

pub fn bump(l: &mut dyn Loader) {
    let v = l.get();
    l.put(v + 1);
}
