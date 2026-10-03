THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Logical meaning of the symbolic process representation

Status: implemented experimental profile `ot-constraint-logic-0.1`.

## Purpose

The symbolic transition representation must not merely resemble mathematics.
Every accepted post-parse expression is translated into a small, typed logic
with a stated semantic meaning. The structural checker can remain fast and
solver-free, while an independent solver checks both:

1. the semantic soundness of the proof-rule schemas; and
2. concrete applications of those rules to compiled model obligations.

This creates a precise boundary between the source parser, the symbolic
checker, the logical claim, and the solver backend.

## Logic

`src/clarity/certification/constraint_logic.py` defines a solver-independent,
quantifier-free, many-sorted constraint language:

- sorts: `Bool`, `Int`, and `Real`;
- Boolean operations: negation, conjunction, disjunction, implication, and
  equality;
- arithmetic: exact rational literals, negation, addition, subtraction,
  multiplication, and division;
- comparisons: `<`, `<=`, `>`, and `>=`;
- typed conditional terms.

Every term checks its own arity and sort constraints. A `LogicSequent` means

```text
premise_1 AND ... AND premise_n  entails  conclusion
```

and its counterexample is defined exactly as

```text
premise_1 AND ... AND premise_n AND NOT(conclusion).
```

Each sequent has a canonical SHA-256 fingerprint so a solver result can be tied
to the exact logical object checked.

## Translation boundary

`compile_expression` gives accepted symbolic expressions their logical
denotation. It resolves only the model compiler's explicit observation,
executed-action, prior-action, scenario-context, and declared fixed-parameter
bindings. Unknown references or unsupported sorts fail closed.

`compile_structural_discretization_logic` translates every accepted structural
safety obligation into a `LogicSequent`. The resulting premises are:

1. the complete accepted scenario-only source domain;
2. the mapped scenario domain used by the structural proof;
3. explicit equalities for observations fixed by process context; and
4. the shield or identity execution relation.

The conclusion is the mapped source safety predicate. This translation does
not execute the simulator.

## Proof-rule validation

`src/clarity/certification/structural_rule_validation.py` states the semantic
schemas used by the positive structural proof rules. The current inventory
covers:

- implication/counterexample equivalence;
- discharging an implication after independently proving its consequent;
- conjunction introduction;
- equality substitution in an affine predicate;
- contradictory lower and upper bounds, including strict boundaries; and
- unit-multiplier Farkas addition for widths two through four.

For every schema, Z3 receives the schema premises together with the negation of
its conclusion. Only `UNSAT` validates the schema. `SAT`, `UNKNOWN`, timeout,
missing solver, or translation failure is not validation.

The test suite also exercises the checker implementation itself:

- accepted Boolean DNF rewrites must be equivalent in the explicit logic;
- every accepted interval contradiction must be semantically unsatisfiable;
- accepted unit-Farkas applications must be semantically unsatisfiable; and
- a deliberately weakened interval rule must be satisfiable, showing that the
  validation is not vacuous.

## Exact guarantee and trust boundary

This work verifies the post-parse logic we claim and checks that the current
structural-rule implementation agrees with that logic on the tested rule
space. It does not claim that Python, Z3, the SysML parser, or arbitrary source
parsing has been formally verified.

The current trusted boundary is therefore:

- Python execution and exact-rational arithmetic;
- the source parser and source-to-symbolic binding witnesses;
- the small logic-to-Z3 lowering backend; and
- Z3 itself.

Keeping the logical IR independent of Z3 permits a second solver backend or a
proof-assistant semantics to be added without changing the production
structural checker. Agreement between independent backends would reduce this
trusted boundary further.

## Commands

Run the logic and rule-validation suite:

```bash
PYTHONPATH=src python tests/certification/validate_constraint_logic.py -v
```

Generate a structural certificate, validate the proof-rule schemas, and
validate the model-specific logical sequents with Z3:

```bash
PYTHONPATH=src python scripts/certify_discretization.py \
  path/to/model.sysml --validate-z3
```
