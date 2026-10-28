use std::cell::{RefCell, RefMut};

pub struct E;

pub fn borrow(c: &RefCell<u32>) -> Result<RefMut<'_, u32>, E> {
    Ok(c.borrow_mut())
}

pub fn bump(c: &RefCell<u32>) -> Result<(), E> {
    let mut r = borrow(c)?;
    *r += 1;
    Ok(())
}
