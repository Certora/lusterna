module

/-!
# Overflow settings

The models of crates whose behavior on overflow depends on the profile
settings of the package take these settings as a `Config`.
-/

@[expose] public section

namespace Lusterna

/-- The overflow settings that the profile of a build gives to a package. -/
structure Config where
  /-- `overflow-checks` -/
  overflowChecks : Bool
  /-- `debug-assertions` -/
  debugAssertions : Bool

end Lusterna
