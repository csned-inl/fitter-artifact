THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Thermostat scalar-expression lowering

`clarity.certification.markov_z3_expr` compiles the checked thermostat scalar
AST subset into deterministic SMT-LIB 2 terms. It is intentionally below the
whole-transition layer.

The compiler:

- resolves every source storage reference to one retained typed `StorageId`;
- follows checked live-expression aliases rather than stale graph candidates;
- emits Boolean and integer operations with native sorts;
- emits binary64 arithmetic and comparisons with explicit IEEE-754 operators;
- uses round-to-nearest, ties-to-even (`RNE`) for arithmetic;
- materializes Real source literals from their exact binary64 bit patterns;
- promotes only checked integer literals into floating expressions;
- rejects unresolved references, enum/container terms, implicit symbolic
  coercions, unsupported operators, and non-AST values.

The gate compiles all 19 scalar roots retained from assignment, branch,
decision-input, and requirement expressions. On the workstation it also asks
the pinned Z3 build to parse and solve a generated property fixture.

This stage does not yet encode event frames, control selectors, histories, or
the two-execution counterexample relation, and therefore cannot discharge a
production manifest obligation.
