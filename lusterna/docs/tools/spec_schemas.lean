/-
Lusterna SPEC SCHEMAS — the attributes FORMALISE uses to DECLARE what each theorem IS.

The three mechanical checks in `spec_checks.lean` hunt for bad shapes, which makes their SILENCE
ambiguous ("clean, or nothing I recognise"). These attributes invert that: the author declares the
schema, and `checkSchemaConformance` verifies the declaration. A conformance failure on a DECLARED
schema is a fact rather than a judgement, which is what lets the harness gate on it.

  @[lusterna_invariant]              -- Inv pre = ok true → target execution → Inv post = ok true
  @[lusterna_hoare]                  -- Pre inputs = ok true → target execution → Post … = ok true
  @[lusterna_freeform "why"]         -- a supporting lemma, deliberately outside every schema

╔══════════════════════════════════════════════════════════════════════════════════════════════╗
║ HARNESS-OWNED, and the attribute set is APPEND-ONLY. Do not edit or delete this file.         ║
║                                                                                              ║
║ `lean._write_lint_tool` rewrites it on every `setup_lake`, while PRIOR campaigns' spec        ║
║ modules are restored immutable from `refs/lusterna/pristine` and reference these attributes   ║
║ by name. So an old spec module is always rebuilt against a NEWLY WRITTEN copy of this file:   ║
║ renaming or removing an attribute breaks every prior campaign, and the pipeline cannot repair ║
║ them, because editing them is exactly what `_restore_prior_specs` forbids.                    ║
║                                                                                              ║
║ Attributes may be ADDED. They may never be renamed or removed.                                ║
╚══════════════════════════════════════════════════════════════════════════════════════════════╝

Deliberately depends on `Lean` ALONE — not Aeneas. This is the file spec modules import, so it must
stay tiny, fast and stable; the churn belongs in `spec_checks.lean`, which imports this one.
-/
import Lean

open Lean

/-- `@[lusterna_freeform "reason"]`. Parametric because a supporting lemma opts OUT of every checked
schema, and an opt-out should have to say why — the reason is reported, so a spec cannot quietly
become all-freeform. -/
syntax (name := lusterna_freeform) "lusterna_freeform " str : attr

namespace Lusterna.Schemas

/-- An INVARIANT-PRESERVATION theorem: `Inv <pre> = ok true`, one declared-target execution
producing `<post>`, concluding `Inv <post> = ok true`, with `Inv` a failure-strict `Result Bool`. -/
initialize invariantAttr : TagAttribute ←
  registerTagAttribute `lusterna_invariant
    "this theorem is an invariant-preservation theorem (see Lusterna `schema_conformance`)"

/-- A FORWARD HOARE TRIPLE: `Pre <root inputs> = ok true`, exactly one declared-target execution
binding fresh outputs, concluding `Post <inputs, outputs> = ok true`, with both `Pre` and `Post`
failure-strict `Result Bool` definitions. -/
initialize hoareAttr : TagAttribute ←
  registerTagAttribute `lusterna_hoare
    "this theorem is a forward Hoare triple (see Lusterna `schema_conformance`)"

/-- A SUPPORTING lemma, declared out of scope. The payload is the justification. -/
initialize freeformAttr : ParametricAttribute String ←
  registerParametricAttribute {
    name := `lusterna_freeform
    descr := "a supporting lemma, deliberately outside every checked schema; the string says why"
    getParam := fun _ stx => do
      match stx with
      | `(attr| lusterna_freeform $s:str) =>
        let r := s.getString
        -- `String.trim` is mid-transition to `String.Slice` in this toolchain and warns on use;
        -- "no non-whitespace character" says the same thing without the deprecation noise, which
        -- would otherwise surface in every crate's build.
        if !r.any (fun c => !c.isWhitespace) then
          throwError "@[lusterna_freeform] needs a non-empty reason for opting out of the schemas"
        return r
      | _ => Elab.throwUnsupportedSyntax
  }

/-- Every schema `decl` declares, as surface names. MORE THAN ONE is a conformance failure the
caller reports — this returns what is there rather than picking a winner, because silently
preferring one would let a theorem declare two and be checked against the laxer. -/
def declaredSchemas (env : Environment) (decl : Name) : Array String := Id.run do
  let mut out : Array String := #[]
  if invariantAttr.hasTag env decl then out := out.push "invariant"
  if hoareAttr.hasTag env decl then out := out.push "hoare"
  if (freeformAttr.getParam? env decl).isSome then out := out.push "freeform"
  return out

/-- The `@[lusterna_freeform]` justification, if any. -/
def freeformReason (env : Environment) (decl : Name) : Option String :=
  freeformAttr.getParam? env decl

end Lusterna.Schemas
