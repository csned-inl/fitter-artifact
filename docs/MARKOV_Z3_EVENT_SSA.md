# Event-local scalar SSA lowering

`clarity.certification.markov_z3_event` assigns distinct entry and exit SMT
symbols to every retained Boolean, presence, integer, Float32, and Float64
storage. For each supported event it emits an exact update for every scalar
write and an equality frame for every non-written scalar storage.

The first checked subset contains 16 sliced events: assignments, Boolean
branches, the configured-`dt` copy, and the binary64 engine-clock advance.
Assignments reuse the exact IEEE-754 expression compiler, including RNE at
every arithmetic operation. Branch guards are retained as typed Boolean terms
for later attachment to true/false control edges.

Enum-mode transitions, transport operations, source-constraint solving,
property accumulation, shield/action application, decisions, and outcomes are
rejected by this API. They require profile-specific constructors or compound
semantics and cannot be approximated by unconstrained scalar writes. This
module is therefore a checked component of the future composed transition
relation, not a complete transition relation or certificate.
