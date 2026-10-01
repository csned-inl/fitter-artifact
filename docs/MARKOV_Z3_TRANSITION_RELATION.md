# Composed finite transition relation

`clarity.certification.markov_z3_transition` composes the checked 132-state,
281-edge finite control DAG with all 51 retained event semantics and the 27
proved identity-only block/no-op nodes.

Event-local lowering still produces and tests a complete 46-cell entry/exit
SSA relation. Composition first validates that every retained write has exactly
one update and every non-write has exactly one identity frame. It then removes
those identities and carries unchanged values symbolically. The composed SMT
relation allocates cells only for the 46 boundary inputs, 71 actual writes, and
105 control-flow phi merges. Phi bridges group incoming edges that carry the
same value. Exception and blocked-accept edges explicitly carry the pre-event
state, so a failed update cannot leak an unconstrained post-state into an error
outcome.

The SysML `lastObservedTemperature` attribute is a live binding to the held
thermometer reading, not an independent storage location. The slice therefore
projects every read directly to `semantic:held_temperature` and does not
allocate, write, or bridge a duplicate state cell for the binding target.

True/false, matched/absent, accepted/blocked, sent, call, return, next, and
exception edges remain equated to their exact event predicates. Source floating
division records an explicit nonzero-divisor definedness condition; zero routes
to the execution-error edge instead of inheriting SMT floating division's
infinity behavior.

The sparse relation has 635 declarations and 262 grouped phi bridges and emits
about 0.93 MiB of SMT-LIB, down from 12,821 declarations, 13,207 bridges, and
about 12.46 MiB in the complete-frame composition. Boundary and all nine
first-outcome state interfaces remain explicit. This is still a
transition-kernel component, not yet a proof query or certificate.
