import LusternaSpec
import LusternaSpecTest.Fixture

/-!
# LusternaSpecTest

Regression tests for LusternaSpec. The file type-checks iff each `#guard_msgs` sees the expected
output.
-/

/--
info: {"axioms":[],"function":null,"id":null,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.two","title":null}
{"axioms":["sorryAx"],"function":"LusternaSpecTest.f","id":5,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_increments_fail","title":"f increments if it returns"}
{"axioms":["sorryAx"],"function":"LusternaSpecTest.f","id":1,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_increments","title":"f increments"}
{"axioms":["sorryAx"],"function":null,"id":2,"issues":["notATriple"],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_eq","title":"f is f"}
{"axioms":["sorryAx"],"function":null,"id":null,"issues":[],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_eq_by_lemma","title":null}
{"axioms":["sorryAx"],"function":"LusternaSpecTest.f","id":4,"issues":[{"canFail":{"reason":"LusternaSpecTest.f r"}}],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_fixpoint","title":"f is a fixpoint"}
{"axioms":["sorryAx"],"function":"LusternaSpecTest.f","id":3,"issues":["ignoresResult","vacuous"],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_returns","title":"f returns"}
-/
#guard_msgs in
#eval Lusterna.Spec.check #[`LusternaSpecTest.Fixture] (axioms := true)

/-- error: @[lusterna] needs a non-empty title -/
#guard_msgs in
@[lusterna 1 " "]
theorem untitled : True := trivial
