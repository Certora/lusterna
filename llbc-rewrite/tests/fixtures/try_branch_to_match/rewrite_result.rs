pub struct E;
pub struct F;

impl From<E> for F {
    fn from(_: E) -> F {
        F
    }
}

pub fn get(x: u32) -> Result<u32, E> {
    if x > 0 { Ok(x) } else { Err(E) }
}

pub fn same_error(x: u32) -> Result<u32, E> {
    let v = get(x)?;
    Ok(v + 1)
}

pub fn converted_error(x: u32) -> Result<u32, F> {
    let v = get(x)?;
    Ok(v)
}
