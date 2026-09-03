import Aeneas
import Probe
import Probe.LusternaSchemas

/-! Fixture for `schema_conformance`'s FAILURE-STRICTNESS (`claimFailSafe?` → `certifiedStrict`).

Two halves. The predicate DEFINITIONS below are the real subject: each is either inside the
failure-strict fragment (and must certify) or outside it (and must be reported). The theorems at the
bottom are thin `@[lusterna]` wrappers whose only job is to present a checked property whose claim
rests on the predicate, so the strictness check fires on it, plus a handful that miss the shape.

The empirical anchor for the whole check is `sum_bals` over `overflowing`: it genuinely returns
`fail integerOverflow`, so `InvSum overflowing = fail …` (never `ok true`) while
`InvSumOpen overflowing = ok true`. That pair is the defect this check exists to find, and
`#eval`-ing the two is the fastest way to convince yourself the fragment means what it claims. -/

open Aeneas Aeneas.Std Aeneas.Std.Result

namespace Probe.Inv

structure Acct where
  bal : U64
deriving Repr

structure St where
  accts : List Acct
  total : U64

def measure (s : St) : Result U64 := ok s.total

/-- Fallible for real: the sum overflows `U64` on `overflowing` below. -/
def sum_bals : List Acct → Result U64
  | [] => ok 0#u64
  | a :: r => do
    let s ← sum_bals r
    let t ← s + a.bal
    ok t

def overflowing : St :=
  { accts := [⟨18446744073709551615#u64⟩, ⟨18446744073709551615#u64⟩], total := 0#u64 }

def transfer (amt : U64) (s : St) : Result (Unit × St) := ok ((), { s with total := amt })
def notATarget (s : St) : Result (Unit × St) := ok ((), s)

/-! ── CERTIFIED STRICT: every one of these must stay clean ────────────────────────────────── -/

/-- The canonical form: bind every measurement, decide on pure data. -/
def InvFlat (s : St) : Result Bool := do
  let t ← measure s
  let b ← sum_bals s.accts
  ok (t == b)

/-- `massert` is not a special case — it is a call whose arguments are Result-free. -/
def InvAssert (s : St) : Result Bool := do
  let t ← measure s
  massert (t.val < 100)
  ok (t.val == 0)

/-- THE CASE THAT DECIDES WHETHER THIS CHECK IS USEFUL. A real invariant folds over accounts, and
structural recursion compiles to `fun l => List.brecOn l InvFold._f` — unreadable. It certifies
only because the walk reads `getEqnsFor?` instead of `dv.value`. -/
def InvFold : List Acct → Result Bool
  | [] => ok true
  | a :: r => do
    let b ← InvFold r
    ok (b && a.bal.val == 0)

def InvFoldTop (s : St) : Result Bool := InvFold s.accts

/-- Well-founded recursion, which compiles differently from structural and must also certify. -/
def InvWf (l : List Acct) (acc : Nat) : Result Bool :=
  match l with
  | [] => ok (acc == 0)
  | a :: rest => do
    let b ← measure ⟨[], a.bal⟩
    InvWf rest (acc + b.val)
termination_by l.length

def InvWfTop (s : St) : Result Bool := InvWf s.accts 0

/-- Branching on PURE data. Both branches stay in the monad and are each walked. -/
def InvIte (s : St) : Result Bool := do
  let t ← measure s
  if s.accts.isEmpty then ok (t.val == 0) else ok (t.val > 0)

/-- `dite`, whose branches are lambdas rather than plain terms. -/
def InvDite (s : St) : Result Bool :=
  if _h : s.accts.isEmpty then do let t ← measure s; ok (t.val == 0)
  else do let t ← measure s; ok (t.val > 0)

/-- Matching a PURE scrutinee. Contrast `InvMatchRes`, whose scrutinee is a `Result`. -/
def InvMatchPure (s : St) : Result Bool :=
  match s.accts with
  | [] => ok true
  | _ => do let t ← measure s; ok (t.val > 0)

/-- `Result.ofOption` never RECEIVES a `Result`, so it cannot launder one — it can only produce
`fail`, which is the strict direction. A blocklist naming Aeneas's `Result` helpers would have
rejected this; the positional rule accepts it, correctly. -/
def InvOfOption (s : St) : Result Bool := do
  let a ← Result.ofOption s.accts.head? Error.panic
  ok (a.bal.val == 0)

/-- An extra non-state argument, which the shape rule requires to AGREE across the theorem. -/
def InvWithCap (cap : U64) (s : St) : Result Bool := do
  let t ← measure s
  ok (t.val ≤ cap.val)

/-- The empirical anchor. `InvSum overflowing = fail integerOverflow`, never `ok true`. -/
def InvSum (s : St) : Result Bool := do
  let b ← sum_bals s.accts
  ok (b == s.total)

/-- THE EMPIRICAL ANCHOR, pinned at BUILD time rather than asserted in a comment. `sum_bals`
genuinely fails on `overflowing`, and these two lines are the whole point of the check: the
certified-strict invariant is NOT `ok true` there, while the fail-open one is. Editing `sum_bals` or
`overflowing` in a way that breaks the premise now breaks the build. -/
example : sum_bals overflowing.accts = .fail .integerOverflow := by rfl
example : InvSum overflowing = .fail .integerOverflow := by rfl

/-! Aeneas-GENERATED-CODE shapes. The demo crate in `tests/demo-metavault-private-lusterna` cannot
build against the current toolchain (its committed Lean predates the `core.convert.From.from_` →
`from` rename), so the properties that matter about generated code are reproduced here instead:
a `Std.Array`-typed field folded over `.val` (real generated state is
`Array state.VaultToken 10#usize`), and a DEEP chain of one-line `Result` defs, which is what
`certifiedStrict` actually walks on a real crate — every generated def is project-local, hence
inspected rather than trusted. -/

structure ArrSt where
  toks  : Std.Array Acct 4#usize
  total : U64

/-- A 10-hop chain of generated-style wrappers under the measurement. Exercises the node budget
across a closure the size a real crate produces. -/
def g0 (a : Acct) : Result U64 := ok a.bal
def g1 (a : Acct) : Result U64 := g0 a
def g2 (a : Acct) : Result U64 := g1 a
def g3 (a : Acct) : Result U64 := g2 a
def g4 (a : Acct) : Result U64 := g3 a
def g5 (a : Acct) : Result U64 := g4 a
def g6 (a : Acct) : Result U64 := g5 a
def g7 (a : Acct) : Result U64 := g6 a
def g8 (a : Acct) : Result U64 := g7 a
def g9 (a : Acct) : Result U64 := g8 a

def sumArr : List Acct → Result U64
  | [] => ok 0#u64
  | a :: r => do
    let v ← g9 a
    let s ← sumArr r
    let t ← s + v
    ok t

/-- CLEAN: a fold over an Aeneas `Array`'s `.val`, through a 10-deep generated-style chain. -/
def InvArr (s : ArrSt) : Result Bool := do
  let n ← sumArr s.toks.val
  ok (n == s.total)

/-- CLEAN: an Aeneas `partial_fixpoint` loop, which is how a generated loop can arrive. It is a
recursive `Result` def like any other, so the equation-lemma path reads it. -/
def loopSum (l : List Acct) (acc : U64) : Result U64 :=
  match l with
  | [] => ok acc
  | a :: r => do
    let acc' ← acc + a.bal
    loopSum r acc'
partial_fixpoint

def InvLoop (s : St) : Result Bool := do
  let n ← loopSum s.accts 0#u64
  ok (n == s.total)

def transferArr (amt : U64) (s : ArrSt) : Result (Unit × ArrSt) := ok ((), { s with total := amt })

/-! ── FAIL-OPEN: every one of these must be reported ──────────────────────────────────────── -/

/-- Reported because `sum_bals s.accts` is a `Result` sitting in an ARGUMENT, not because `ok?` is
named. `InvSumOpen overflowing = ok true` — the invariant holds for free on a state whose total
cannot even be computed, which is the entire defect. -/
def InvSumOpen (s : St) : Result Bool :=
  ok (! ok? (sum_bals s.accts))

/-- A Boolean encoding of fail-open behaviour: no implication anywhere, so only the
failure-strictness fragment finds it. Elaborates to
`InvMatchRes.match_1 motive (measure s) …`, so the scrutinee is an argument. -/
def InvMatchRes (s : St) : Result Bool :=
  match measure s with
  | ok t => ok (t.val == 0)
  | fail _ => ok true
  | div => ok true

/-- Same defect behind an `ite` condition rather than a match. -/
def InvIteOkQ (s : St) : Result Bool :=
  if ok? (measure s) then ok true else ok true

/-- A plain (non-monadic) `let` binding a `Result` is not a bind, and does not launder the match. -/
def InvLetRes (s : St) : Result Bool :=
  let r := measure s
  match r with
  | ok t => ok (t.val == 0)
  | fail _ => ok true
  | div => ok true

/-- TRANSITIVITY, four wrappers deep over a fail-open leaf. Each layer looks innocent on its own;
only following project-local `Result`-returning callees to exhaustion finds it. -/
def W4 (s : St) : Result Bool := InvMatchRes s
def W3 (s : St) : Result Bool := W4 s
def W2 (s : St) : Result Bool := W3 s
def InvDeep (s : St) : Result Bool := W2 s

/-- Fail-open with an extra argument, so the reported case is not only the one-argument shape. -/
def InvWithCapBad (cap : U64) (s : St) : Result Bool :=
  ok (! ok? (measure s) || cap.val == 0)

/-- The other half of the anchor: the fail-open form reports "invariant holds" on exactly the state
whose total cannot be computed. This is the defect, executable. -/
example : InvSumOpen overflowing = .ok true := by rfl

/-! ── NOT CERTIFIED, and not evidence of a defect either ──────────────────────────────────── -/

/-- `opaque` has no body. Reported rather than skipped: an uninspectable invariant must never read
as certified. -/
opaque InvOpaque (s : St) : Result Bool

/-- `partial def` compiles to an opaque constant, so it lands in the same place. -/
partial def InvPartial (s : St) : Result Bool := InvPartial s

/-- The idiomatic monadic fold. Almost certainly strict — `Result` has no `MonadExcept` instance,
so `foldlM` has nothing to catch a failure WITH — but the walk cannot see inside the combinator, so
it is reported at the lower `not_certified` severity rather than waved through or called fail-open.
The prescribed fix is `InvFold` above: explicit recursion, which certifies. -/
def InvFoldM (s : St) : Result Bool := do
  let n ← s.accts.foldlM (fun (acc : U64) (a : Acct) => do let t ← acc + a.bal; ok t) 0#u64
  ok (n == s.total)

/-- Prop-valued, so OUT OF SCOPE here — `assumed_postcondition` owns this form. Silent, not skipped. -/
def InvProp (s : St) : Prop := ∃ t, measure s = ok t ∧ t.val = 0

end Probe.Inv

namespace Probe.Inv.Spec
open Probe.Inv

/-! These are `@[lusterna]` CHECKED properties. The `invariant_not_strict` check they used to drive
was FOLDED INTO `schema_conformance`: `certifiedStrict` is byte-for-byte unchanged and is now reached
through `claimFailSafe?`, which vets every precondition and the conclusion. A preservation stated on a
fail-open predicate draws `predicate_not_failure_strict` (on BOTH the pre-state hypothesis and the
conclusion, since each is a claim resting on that predicate — 2 findings); on a strict predicate it
draws nothing. This is the parity corpus for the strictness fragment. -/

/-! ── CLEAN: the predicate certifies, so the checked property conforms ─────────────────────── -/

@[lusterna] theorem i1_flat (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvFlat s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

@[lusterna] theorem i2_assert (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvAssert s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvAssert s' = ok true := by sorry

@[lusterna] theorem i3_fold (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvFoldTop s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvFoldTop s' = ok true := by sorry

@[lusterna] theorem i4_wf (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvWfTop s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvWfTop s' = ok true := by sorry

@[lusterna] theorem i5_ite (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvIte s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvIte s' = ok true := by sorry

@[lusterna] theorem i6_dite (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvDite s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvDite s' = ok true := by sorry

@[lusterna] theorem i7_match_pure (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvMatchPure s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvMatchPure s' = ok true := by sorry

@[lusterna] theorem i8_of_option (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvOfOption s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvOfOption s' = ok true := by sorry

@[lusterna] theorem i9_cap_agrees (cap amt : U64) (s s' : St) (y : Unit)
    (hinv : InvWithCap cap s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvWithCap cap s' = ok true := by sorry

@[lusterna] theorem i10_sum (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvSum s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvSum s' = ok true := by sorry

@[lusterna] theorem i27_array_deep_chain (amt : U64) (s s' : ArrSt) (y : Unit)
    (hinv : InvArr s = ok true) (hexec : transferArr amt s = ok (y, s')) :
    InvArr s' = ok true := by sorry

@[lusterna] theorem i28_partial_fixpoint_loop (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvLoop s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvLoop s' = ok true := by sorry

/-- CLEAN, plain-Prop: the readable form the whole redesign is for — a raw equation about the output,
no `Result Bool` wrapper, no measurement, so `claimFailSafe?` accepts it as a pure proposition. -/
@[lusterna] theorem i20_plain_prop (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) : s'.total = amt := by sorry

/-! ── FLAGGED `predicate_not_failure_strict` (fail-open) — pre-state AND conclusion each ───── -/

@[lusterna] theorem i11_sum_open (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvSumOpen s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvSumOpen s' = ok true := by sorry

@[lusterna] theorem i12_match_res (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvMatchRes s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvMatchRes s' = ok true := by sorry

@[lusterna] theorem i13_ite_okq (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvIteOkQ s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvIteOkQ s' = ok true := by sorry

@[lusterna] theorem i14_let_res (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvLetRes s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvLetRes s' = ok true := by sorry

@[lusterna] theorem i15_deep (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvDeep s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvDeep s' = ok true := by sorry

@[lusterna] theorem i16_cap_bad (cap amt : U64) (s s' : St) (y : Unit)
    (hinv : InvWithCapBad cap s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvWithCapBad cap s' = ok true := by sorry

/-! ── FLAGGED `predicate_not_failure_strict` (not certified: no inspectable body / combinator) ─ -/

@[lusterna] theorem i17_opaque (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvOpaque s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvOpaque s' = ok true := by sorry

@[lusterna] theorem i18_partial (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvPartial s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvPartial s' = ok true := by sorry

@[lusterna] theorem i19_foldm (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvFoldM s = ok true) (hexec : transfer amt s = ok (y, s')) :
    InvFoldM s' = ok true := by sorry

/-! ── FLAGGED `claim_not_failsafe`: a measurement, but NOT the accepted `P args = ok true` form ─

The checked shape accepts a claim two ways: a pure proposition, or `P args = ok true` for a
project-local failure-strict `P`. A claim that NAMES a measurement any other way — an inline
existential, or an inline `do`-block whose head is library `Bind.bind` — is neither, and is reported
with the fix: state it as a plain proposition over the produced values, or as a `Result Bool`
predicate. (Both fire twice: the pre-state hypothesis and the conclusion.) -/

@[lusterna] theorem i21_existential_measurement (amt : U64) (s s' : St) (y : Unit)
    (hinv : InvProp s) (hexec : transfer amt s = ok (y, s')) : InvProp s' := by sorry

@[lusterna] theorem i22_inline_measurement (amt : U64) (s s' : St) (y : Unit)
    (hinv : (do let t ← measure s; ok (t.val == 0)) = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    (do let t ← measure s'; ok (t.val == 0)) = ok true := by sorry

/-! ── FLAGGED `execution_not_unique`: the execution is not a DECLARED target (a wrong `--subjects`),
so the checked property has NO execution and must not read as clean. -/

@[lusterna] theorem i25_not_a_target (s s' : St) (y : Unit)
    (hinv : InvSumOpen s = ok true) (hexec : notATarget s = ok (y, s')) :
    InvSumOpen s' = ok true := by sorry

end Probe.Inv.Spec
