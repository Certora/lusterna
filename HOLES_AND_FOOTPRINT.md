# Holes, footprint, and soundness

Lusterna's central promise is narrow and precise: *if it reports a property as verified,
that property really follows from the real Rust code.* This document explains the one
place that promise is most easily broken — **untranslatable code** — and the three
mechanisms that keep it honest:

1. **Hole detection** — noticing what Aeneas could not translate.
2. **The footprint** — a cheap, static estimate of whether a hole can affect a stated
   property.
3. **The axiom check** — the authoritative, kernel-level verdict on whether a *proved*
   property actually depends on a hole.

The short version: the footprint is a fast approximation; `#print axioms` is the truth.

---

## 1. Why holes exist

The trust chain is:

```
original Rust  ──[Charon + Aeneas: trusted]──▶  Lean 4  ──[proofs]──▶  verified property
```

The Rust source is **immutable** — Lusterna never rewrites it, because a "helpful" edit
could silently make the model diverge from the real implementation. Aeneas runs on the
code exactly as written.

But not every Rust construct has a Lean model (I/O, some iterator-adaptor chains, inline
assembly, …). When Aeneas meets one, it does **not** crash and does **not** invent a body.
It **degrades gracefully**: it keeps the function's *signature* and replaces the *body*
with a bare `sorry`. We call such a function a **hole**.

That design choice is what makes everything downstream possible:

- The def still exists, so **callers still type-check** and the **call graph stays
  intact** — which is exactly what lets us reason about reachability later.
- A hole is an honest marker ("not translated here"), never a fabricated fact.

Example. Given this crate:

```rust
pub fn double(x: u32) -> u32 { x * 2 }              // pure — translatable
pub fn read_and_double() -> u32 {                   // does I/O — NOT translatable
    let mut s = String::new();
    std::io::stdin().read_line(&mut s).unwrap();
    double(s.trim().parse().unwrap())
}
pub fn quad(x: u32) -> u32 { double(double(x)) }    // pure — translatable
```

Aeneas emits something like:

```lean
/- [demo::double]: -/
def demo.double (x : U32) : Result U32 :=
  x * 2#u32

/- [demo::read_and_double]: -/
def demo.read_and_double : Result U32 :=
  sorry                       -- ← the hole: signature kept, body untranslated

/- [demo::quad]: -/
def demo.quad (x : U32) : Result U32 :=
  do
    let y ← demo.double x
    demo.double y
```

---

## 2. Detecting holes

Hole detection is textual and lives in `lean._detect_holes`, called by `run_aeneas`
right after Aeneas writes its files. For each generated Lean file it:

- finds every top-level `def NAME` (`^def\s+([\w.]+)` — Aeneas uses fully-qualified
  dotted names like `demo.read_and_double`);
- takes that def's **block** — from its header to the next `^def`/`^end`;
- flags the def as a hole if the block contains a line that is *only* `sorry`
  (`^\s*sorry\s*$`).

Run against the file above, the logic picks out exactly one hole:

```python
import re

def detect_holes(lean: str) -> list[str]:
    holes = []
    for m in re.finditer(r"(?m)^def\s+([\w.]+)", lean):           # every `def NAME`
        start = m.end()
        nxt = re.search(r"(?m)^(def|end)\b", lean[start:])         # block = up to next def/end
        block = lean[start : start + (nxt.start() if nxt else len(lean))]
        if re.search(r"(?m)^\s*sorry\s*$", block):                 # a line that is ONLY `sorry`
            holes.append(m.group(1))
    return holes

# detect_holes(<the Lean above>)  ->  ['demo.read_and_double']
```

`double` has a real body; `quad` has a real body that *calls* `double`; only
`read_and_double`'s whole body is a lone `sorry`.

Two distinctions worth internalizing:

- **Translation holes vs. proof stubs.** `_detect_holes` only ever scans the *Aeneas
  translation files*, never the spec. A theorem written `:= by sorry` in `Spec.lean` is
  **not** a hole — it is an unproved obligation. The two `sorry`s look alike but live in
  different files and mean different things: *code we could not translate* vs. *a proof we
  have not done yet*. (The detector also requires `sorry` alone on a line, so an inline
  `:= by sorry` would not match regardless.)
- **A hole is not a failure.** `run_aeneas` returns `success: True` even with holes. A
  hole only becomes *relevant* through the footprint.

---

## 3. Does a hole matter? — the footprint (static approximation)

A crate can contain a hole that has nothing to do with the property we are proving. So the
question is never "are there holes?" but "**does a hole lie under this specific
property?**"

Lusterna's first answer is the **footprint**: the set of translation defs a stated
property transitively references. If a hole is in that set, the property might rest on it;
if not, the hole is irrelevant to that property.

```python
from lusterna import tools

# roots  = translation defs the spec text mentions by name
# closure = those defs plus everything they transitively reference
roots     = tools.referenced_defs(spec_text, translation_text)
footprint = tools.call_closure(translation_text, roots)
holes_in_footprint = [h for h in holes if h in footprint]
```

This is computed in `lean.footprint` after FORMALISE and again after PROVE.

**It is only an approximation, and only conservative in one direction:**

- **Over-approximation (harmless).** A statement may *mention* a hole without its proof
  ever depending on the hole's value — e.g. `theorem t : demo.read_and_double = demo.read_and_double := rfl`.
  The footprint flags it anyway. Pessimistic, but it can only *understate* how much is
  verified, never overstate it.
- **Under-approximation (the real danger).** A proof can reach a hole through a path with
  no textual def-name token to see — a `simp` lemma set, a typeclass instance, notation,
  an unfolding. The name scan sees no reference, concludes "no hole in footprint," and is
  **wrong**. Textual matching cannot close this gap.

So the footprint answers "does the *written text* reach a hole" — a useful proxy, but a
proxy. It is valuable precisely because it works *before anything is proved* (it can warn
that a property is even reachable-to-a-hole while every theorem is still `sorry`), but it
is not the final word.

---

## 4. Knowing for sure — the axiom check (authoritative)

Lean already tracks the real answer, and it is the same build oracle we already trust. A
hole is `def foo := sorry`, and `sorry` elaborates to the axiom `sorryAx`. Every
declaration carries its transitive axiom dependencies, surfaced by:

```lean
#print axioms my_theorem
```

with exactly two shapes of answer:

```
'my_theorem' depends on axioms: [propext, Classical.choice, Quot.sound]   -- CLEAN
'my_theorem' depends on axioms: [propext, sorryAx]                        -- TAINTED
```

If `sorryAx` is **absent**, the kernel guarantees that *nothing* — no untranslated hole,
no elaboration-hidden dependency, no leftover proof `sorry` — feeds that proof. If it is
**present**, the proof rests on a `sorry` somewhere. This follows the *actual proof term*
through simp sets, instances, and every definition, so it is immune to the footprint's
textual blind spot.

Better still, it collapses both failure modes into one criterion:

> **A theorem is genuinely established ⟺ `#print axioms` reports no `sorryAx`.**

That single check subsumes *both* "the proof is not itself `:= by sorry`" *and* "the proof
does not lean on a translation hole," because both routes introduce `sorryAx`.

Lusterna runs this in `lean.check_axioms` after PROVE. It writes a throwaway checker that
imports the *already-built* `Spec.olean` and runs `#print axioms` against it, then
elaborates just that checker with `lake env lean` and parses the messages. Crucially it
does **not** re-elaborate the spec from source: doing so re-resolves imports outside
lake's build graph and re-runs every proof, and a single failure there auto-`sorry`s every
declaration and taints the whole batch — a checker artefact, not a real finding.

```python
verdict = {}                                   # short-name -> is it clean?
for line in lean_output.splitlines():
    m = re.search(r"'([\w.]+)' (?:depends on axioms|does not depend)", line)
    if m:
        verdict[m.group(1).split(".")[-1]] = "sorryAx" not in line

clean   = [n for n in names if verdict.get(n.split(".")[-1]) is True]
tainted = [n for n in names if verdict.get(n.split(".")[-1]) is not True]  # False or unresolved
```

Unresolved names fall to `tainted` — the conservative direction.

---

## 5. How the pipeline uses both

| Mechanism | When | What it answers | Guarantee |
|---|---|---|---|
| `_detect_holes` | after TRANSLATE | Which translation defs are untranslated? | exact (textual, over-reports if anything) |
| `_footprint` | after FORMALISE, after PROVE | Does a hole *textually reach* a stated property? | approximate — safe only against over-claiming coverage |
| `check_axioms` | after PROVE | Does a *proved* property depend on `sorryAx`? | **authoritative** — kernel-level, no blind spot |

The footprint is the cheap early signal; the axiom check is the reported verdict for
anything proved. A property is only called *verified* when it is proved **and**
`#print axioms` shows no `sorryAx`.

There is one thing the axiom check does **not** stop on its own: a tool that dodges holes
by simply never stating a theorem that would touch one, then declaring a clean axiom set.
That is caught elsewhere — by the design-doc → abstract-spec → RECONCILE coverage path,
which flags an implementation spec that fails to cover an obligation the design document
requires. Soundness (nothing false is claimed) is the axiom check's job; completeness
(nothing required is skipped) is reconciliation's.

---

## 6. Try it yourself

The footprint idea is a runnable worked example — no test infrastructure, just a
`__main__` block in `lean.py`:

```sh
python -m lusterna.lean
```

```
clean   (property touches compute → helper)
  roots=['foo.compute'] footprint=['foo.compute', 'foo.helper'] → SOUND

tainted (property reaches other → untranslatable)
  roots=['foo.other'] footprint=['foo.other', 'foo.untranslatable'] → NOT SOUND — holes ['foo.untranslatable']
```

The clean property never reaches the crate's hole, so it stays sound; the tainted one
reaches `foo.untranslatable` and is correctly flagged. Swap in your own translation text
and theorem statements to see the reachability play out.
