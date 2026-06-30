# Fibonacci — Design Document

## Purpose
Compute the n-th Fibonacci number defined by the standard recurrence:
  F(0) = 0, F(1) = 1, F(n) = F(n-1) + F(n-2) for n >= 2.

## Implementation
A single recursive function `fib_recursive(n: u32) -> u64` using Rust pattern matching.
No memoisation; time complexity O(2^n), space complexity O(n) (call stack).

## Functional requirements
- F(0) = 0
- F(1) = 1
- For n >= 2: F(n) = F(n-1) + F(n-2)
- The function must not panic for n in [0, 50] (u64 is sufficient up to F(93)).

## Non-requirements
- Performance (exponential recursion is acceptable for this reference implementation).
- Iterative or memoised variants are out of scope.
