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

The direct-observation fixture must be `UNSAT`; the hidden-state fixture must
be `SAT`. These fixtures validate the solver boundary but do not establish the
thermostat theorem.

Production `ObligationManifest` lowering still raises
`UnsupportedLoweringError`. That boundary remains closed until the native
operation semantic table and exact shield/reward/outcome equations are checked.
Consequently, the workstation gate may report `z3_fixture_status: passed` while
it must still report `certificate_claimed: false` and a not-run production
proof status.
