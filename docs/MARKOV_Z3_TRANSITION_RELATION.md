# Composed finite transition relation

`clarity.certification.markov_z3_transition` composes the checked 132-state,
281-edge finite control DAG with all 51 retained event semantics and the 27
proved identity-only block/no-op nodes. Each selected edge transports all 47
typed state terms from its source exit to its target entry.

Event updates and frames are guarded by the active control state and successful
evaluation. True/false, matched/absent, accepted/blocked, sent, call, return,
next, and exception edges are equated to their exact event predicates. Source
floating division records an explicit nonzero-divisor definedness condition;
zero routes to the execution-error edge instead of inheriting SMT floating
division's infinity behavior.

The composed relation has 12,821 declarations and 13,207 guarded state bridges
before boundary, shield, buffer, and visible-outcome equations are added. It is
therefore a transition-kernel component, not yet a proof query or certificate.
