# Functional coverage

Functional coverage measures how much of a declared behavior space a test campaign has
actually exercised. It is not code coverage: code coverage asks which lines ran, functional
coverage asks which *behaviors* were observed.

A coverage model is declared, not inferred. The engineer writes down the interesting
behaviors as coverpoints and bins; the tool reports which bins were hit.

Coverage closure is the process of driving coverage toward 100% of the achievable bins.
Bins that cannot be reached are excluded from the denominator (UVM calls these ignore bins),
so that 100% means "everything reachable was reached" rather than "we gave up".

Coverage is evidence of thoroughness, never a proof of correctness. A campaign with 100%
coverage and no failing checks has demonstrated that the declared space was exercised and
nothing checked went wrong. It has not proven the absence of bugs outside the model.
