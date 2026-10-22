import Aeneas

open Aeneas Aeneas.Std Aeneas.Std.Result

namespace Probe

def f (x : Nat) : Result Nat := ok (x + 1)
def g (x : Nat) : Result Nat := ok (x + 2)

end Probe

