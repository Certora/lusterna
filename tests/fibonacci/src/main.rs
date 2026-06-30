/// Fibonacci algorithm implementations in Rust

/// Simple recursive implementation
/// Time complexity: O(2^n) - exponential
/// Space complexity: O(n) - call stack depth
fn fib_recursive(n: u32) -> u64 {
    match n {
        0 => 0,
        1 => 1,
        _ => fib_recursive(n - 1) + fib_recursive(n - 2),
    }
}

fn main() {
    let test_values = vec![5, 10, 20];

    for n in test_values {
        println!("  Recursive:   {}: {}", n, fib_recursive(n));
    }
}
