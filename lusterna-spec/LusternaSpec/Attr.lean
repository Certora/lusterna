import Lean

open Lean

/-- `@[lusterna id "title"]`: the theorem belongs to the property with this id and title. -/
syntax (name := lusterna) "lusterna " num str : attr

namespace Lusterna.Spec

initialize lusternaAttr : ParametricAttribute (Nat × String) ←
  registerParametricAttribute {
    name := `lusterna
    descr := "the id and title of the property that the theorem belongs to"
    getParam := fun _ stx => do
      match stx with
      | `(attr| lusterna $id:num $title:str) =>
        let title := title.getString
        if !title.any (fun c => !c.isWhitespace) then
          throwError "@[lusterna] needs a non-empty title"
        return (id.getNat, title)
      | _ => Elab.throwUnsupportedSyntax
  }

end Lusterna.Spec
