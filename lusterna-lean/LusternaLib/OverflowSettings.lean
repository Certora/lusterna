module

/-!
# Overflow settings

The models of crates whose behavior on overflow depends on the profile
settings of the build take these settings from an instance of
`OverflowSettings`. `Extraction/Lusterna.lean` of an extracted crate
declares it.
-/

@[expose] public section

namespace Lusterna

/-- The overflow settings that the release profile of the target crate gives
to the replaced crates. -/
class OverflowSettings where
  /-- `overflow-checks` -/
  overflowChecks : Bool
  /-- `debug-assertions` -/
  debugAssertions : Bool

end Lusterna
