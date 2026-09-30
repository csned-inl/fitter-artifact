# Event-local scalar SSA lowering

`clarity.certification.markov_z3_event` assigns distinct entry and exit SMT
symbols to every retained Boolean, presence, integer, Float32, and Float64
storage. For each supported event it emits an exact update for every scalar
write and an equality frame for every non-written scalar storage.

The checked subset now contains 40 of 51 sliced event nodes: assignments,
Boolean branches, the configured-`dt` copy, the binary64 engine-clock advance,
decision completion latching/testing, executed-action decoding, exact machine
mode writes/tests, calls, returns, outcome identity, the converged four-copy
source-flow projection, and distinct thermometer send/accept payload copies.
Assignments reuse the exact IEEE-754 expression compiler, including RNE at
every arithmetic operation. Branch guards are retained as typed Boolean terms
for later attachment to true/false control edges.

Profile enum state uses distinct SMT datatypes. `ExecutedAction` has four
constructors matching action IDs 0--3, and `HeaterBehaviorState` has the exact
`reset` and `on` constructors. Other enum types remain opaque until their
constructors are fixed. Machine source tests expose a typed saved-entry-mode
local; later composition must bind it at `enter_machine` rather than silently
substituting mutable state.

The remaining eleven nodes—two machine-entry snapshot bindings, four trigger
matches, four controller command sends, and ordered property accumulation—are rejected
by this API. They require compound control-local semantics and
cannot be approximated by unconstrained writes. This module is therefore a
checked component of the future composed transition relation, not a complete
transition relation or certificate.
