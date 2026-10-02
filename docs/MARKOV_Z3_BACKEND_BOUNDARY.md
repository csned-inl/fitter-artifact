THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Phase-4 Z3 backend boundary

`clarity.certification.markov_z3` is the first solver-owning component of the
finite-history proof. Its current scope is intentionally narrow:

- canonicalize and SHA-256 bind assertion-only SMT-LIB queries;
- own solver timeouts and reject query-embedded solver controls;
- classify `SAT`, `UNSAT`, `UNKNOWN`, timeout, and errors without conflation;
- retain SAT models for later concrete-candidate replay;
- map Boolean, integer, Float32, and Float64 native sorts exactly;
- reject generic runtime containers and enums without checked constructors;
- rerun only exact-hash SAT/UNSAT evidence with the recorded solver identity.

The solver identity includes the fixed
`simplify > propagate-values > solve-eqs > smt` tactic pipeline. The first
three exact Z3 preprocessing passes remove definitional SSA and state-bridge
equalities before the final search. This changes neither the asserted formula
nor the SAT/UNSAT meaning, and Z3's tactic solver retains model converters so
SAT models remain expressed in the original query vocabulary. Recording the
pipeline in the identity makes replay reject a silently changed solver
configuration.

The direct-observation fixture must be `UNSAT`; the hidden-state fixture must
be `SAT`. These fixtures validate the solver boundary but do not establish the
thermostat theorem.

Production `ObligationManifest` lowering still raises
`UnsupportedLoweringError`. The native operation table, sparse
controller-visible shield/reward/outcome interface, and exact buffer
layout/reset-padding/one-step-shift view are now checked. Linear predecessor
windows now attach exact continuing transitions and lag correspondence under
the conservative `I = true` initial-state domain. This is not exact reset
reachability. The production boundary remains closed until the source-reset
anchor, paired-run, reachability/invariant, progress-query, and
visible-difference lowering are also checked.
Consequently, the workstation gate may report `z3_fixture_status: passed` while
it must still report `certificate_claimed: false` and a not-run production
proof status.
