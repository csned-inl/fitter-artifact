# Finite control-relation lowering

`clarity.certification.markov_z3_control` lowers the checked 132-state,
281-edge first-outcome DAG into deterministic SMT-LIB Boolean flow equations.
The entry is active, each active non-outcome state selects exactly one outgoing
edge, each selected edge requires its source, each non-entry state is active
exactly when one incoming edge is selected, and exactly one of the three
first-outcome control configurations is reached. The three outcome node kinds
appear under nine distinct finite return-stack configurations.

Symbols use SHA-256 identities derived from the complete control configuration
and edge triple, including the finite return stack. The compiler independently
rejects duplicate edges, back edges, unreachable states, pre-outcome dead ends,
and outcome states with successors.

This is a structural path encoding, not yet the complete thermostat transition
relation. Edge predicates remain symbolic until checked operation guards,
exceptions, and state updates are attached. SAT proves the skeleton is
consistent; UNSAT fixtures prove structural progress and first-outcome
exclusivity. Neither result is a Markov certificate.
