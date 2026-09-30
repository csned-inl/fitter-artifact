# Event-local scalar SSA lowering

`clarity.certification.markov_z3_event` assigns distinct entry and exit SMT
symbols to every retained Boolean, presence, integer, Float32, and Float64
storage. For each supported event it emits an exact update for every scalar
write and an equality frame for every non-written scalar storage.

The checked subset now contains all 51 sliced event nodes: assignments,
Boolean branches, the configured-`dt` copy, the binary64 engine-clock advance,
decision completion latching/testing, executed-action decoding, exact machine
mode writes/tests, calls, returns, outcome identity, the converged four-copy
source-flow projection, and distinct thermometer send/accept payload copies.
Machine-call locals and controller command queues are projected exactly to two
saved-entry-mode cells and four concrete command-presence bits. Same-type sends
are idempotent, different command types coexist, trigger matching tests the
exact type bit, and a fired transition consumes only its matched type.
Assignments reuse the exact IEEE-754 expression compiler, including RNE at
every arithmetic operation. Branch guards are retained as typed Boolean terms
for later attachment to true/false control edges.

Profile enum state uses distinct SMT datatypes. `ExecutedAction` has four
constructors matching action IDs 0--3, and `HeaterBehaviorState` has the exact
`reset` and `on` constructors. Property status has true/false constructors and
property error has none/evaluation-error constructors. Machine source tests
read typed saved-entry-mode cells written explicitly by `enter_machine`.

Requirement updates preserve earlier false results and error state. The three
checked thermostat expressions are total under the typed profile, so they add
no new evaluation error. Later composition must initialize each interval to
true/no-error at the drained decision boundary.

Complete event-local coverage is still only a component of the future composed
transition relation. Control edges must be tied to guards, event exit state to
successor entry state, boundary inputs to shield/buffer values, and outcomes to
the visible signature before any certificate can be produced.
