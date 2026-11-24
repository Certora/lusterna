pub fn find_pair(xs: &[u64], target: u64) -> Option<(usize, usize)> {
    let mut i = 0;
    while i < xs.len() {
        let mut j = i + 1;
        while j < xs.len() {
            if xs[i].checked_add(xs[j]) == Some(target) {
                return Some((i, j));
            }
            j += 1;
        }
        i += 1;
    }
    None
}
