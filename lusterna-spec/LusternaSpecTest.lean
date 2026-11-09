import LusternaSpec
import LusternaSpecTest.Fixture

/-!
# LusternaSpecTest

Regression tests for LusternaSpec. The file type-checks iff each `#guard_msgs` sees the expected
output.
-/

/--
info: {"function":null,"id":null,"isSorry":false,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.two","title":null}
{"function":"LusternaSpecTest.f","id":1,"isSorry":true,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_increments","title":"f increments"}
{"function":null,"id":2,"isSorry":true,"issues":["notATriple"],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_eq","title":"f is f"}
{"function":"LusternaSpecTest.f","id":4,"isSorry":true,"issues":[{"canFail":{"reason":"LusternaSpecTest.f r"}}],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_fixpoint","title":"f is a fixpoint"}
{"function":"LusternaSpecTest.f","id":3,"isSorry":true,"issues":["ignoresResult","vacuous"],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_returns","title":"f returns"}
-/
#guard_msgs in
#eval Lusterna.Spec.check #[`LusternaSpecTest.Fixture]

/-- error: @[lusterna] needs a non-empty title -/
#guard_msgs in
@[lusterna 1 " "]
theorem untitled : True := trivial
