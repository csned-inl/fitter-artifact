# Discretization Safety Certification

## Claim

For every parsed SysML `#Prohibition` and `#Obligation`, the stage proves that
the complete modeled physical process cannot enter the unsafe condition at a
controller update or at any time from zero through the following fixed `dt`
interval. The executed action is constrained by the controller contract and
held through the interval according to the extracted actuator and physical
equations.

The Markov process certificate is an input to this claim. It proves that the
selected observation and action history reconstructs enough state for the
sampled transition. Information history is not converted into elapsed physical
time. Physical timing comes from the single fixed `dt` passed through the
pipeline.

## Outcomes

Every checker attempt returns one of three outcomes.

| outcome | meaning |
|---|---|
| `CERTIFIED` | the checker produced proof evidence accepted by the independent verifier |
| `VIOLATION` | the checker produced a complete counterexample accepted by exact replay |
| `DEFERRED` | applicability, proof, or replay was not established |

Unsupported expressions, missing equations, malformed output, numerical
failure, solver failure, and timeout all produce `DEFERRED`. If every method
defers, the property and model are `NOT_CERTIFIED`.

## Source Reduction

The reducer reparses the SysML model and performs these steps for each safety
property.

1. Expand same cycle definitions in the original property.
2. map sampled controller values to the current physical quantities through
   explicit sensor equations.
3. Extract every `#ContinuousRate` transition used by the property.
4. Replace the transition time step with an interval variable ranging from
   zero through the exact fixed `dt`.
5. Substitute the held controller action through actuator equations.
6. Check that each physical trajectory at `dt` equals the extracted sampled
   next value.
7. Construct the sampled point and physical interval unsafe formulas.
8. Build the complete sampled transition and initial condition relation used
   by reachability checks.

The certificate records the full equation inventory and identifies the exact
dependency closure used by the reduction. A physical equation cannot be
omitted merely because the controller does not observe that state directly.

## Lazy Constraint Construction

The unsafe formula remains factored instead of being expanded into every
Boolean and conditional combination. Each checker requests only the branch or
conjunctive subset it needs. The record retains the root expression, its hash,
the selected constraints, and the proof that a selected subset is sufficient.

This matters most for the cruise controller. Its interacting throttle, brake,
speed, gap, and quadratic drag conditions create many syntactically distinct
branches when expanded eagerly, even though most branches share the same
physical constraints. Lazy construction prevents that incidental branch count
from becoming the solver workload.

Reachability uses a shared context keyed by the complete domain, initial
conditions, transition relation, action variables, and numeric types. The
reachable region and safety queries are computed once and reused across
properties. Canonical expression hashes merge identical constraints and proof
obligations.

## Checker Progression

The fixed progression is:

```text
linear
convex
reachability_linear
reachability_convex
exact_symbolic
smt_fallback
relational_invariant
smt_reachability
```

The linear checker accepts only exact linear reductions. Its certificate gives
nonnegative rational weights whose weighted sum is a contradiction. The
independent checker recomputes that contradiction exactly.

The convex checker accepts only its recorded supported quadratic form. Its
dual certificate is converted to exact rational data and the verifier
recomputes the global bound. A numerical optimizer result alone is never a
certificate.

The exact symbolic and local SMT stages address logical structure that was not
discharged by the first two methods. Solver selected subsets are sent back
through the ordinary linear or convex certificate generators when possible.

Reachability separates local feasibility from reachable behavior. It begins
with the exact SysML initial values, advances the complete sampled transition,
merges canonical regions, checks finite prefixes, and attempts inductive
exclusion. The final SMT method receives the same source-linked transition and
unsafe formula. A satisfying result is accepted only after exact trace replay.
An unsatisfying result is accepted only with supported proof evidence.

## Certificate Boundary

Generation, storage, and checking are separate modules. The checker does not
call the analysis generator. Its verifier-owned reconstruction reparses the
hashed SysML file and independently rebuilds:

- requirement identities, annotations, source equations, and dependencies
- fixed timing and declared constants
- continuous assignments and physical trajectories
- sensor to physical mappings and their guards
- start and interval properties
- sampled and interval counterexample formulas
- endpoint equalities
- complete equation inventories
- initial conditions and sampled post-state expressions

The proof verifier then checks case coverage, reduction evidence, linear and
convex certificates, factored formula proofs, reachable regions, SMT queries,
proof hashes, and replayed values.

Repeated analysis subtrees are stored once in a content addressed pool. Every
pool entry is named by the SHA-256 hash of its canonical expanded content. The
checker validates every entry, reference count, and the hash of the completely
expanded analysis before checking the proof. Compact storage therefore does
not alter the certified claim.

## Module Layout

| path | responsibility |
|---|---|
| `discretization/analysis.py` | coordinate one model analysis |
| `discretization/obligations.py` | extract contracts, guards, timing inputs, and variable types |
| `discretization/model/` | physical reduction and exact expression handling |
| `discretization/checkers/` | applicability checks and proof generation |
| `discretization/progression.py` | uniform loud deferral records |
| `discretization/certificates/generation.py` | bind checked inputs to generated analysis |
| `discretization/certificates/io.py` | canonical hashes and storage |
| `discretization/certificates/compaction.py` | validated content addressed proof DAG |
| `discretization/certificates/checker.py` | certificate boundary and source file checks |
| `discretization/certificates/verification/` | exact proof and source reconstruction |

## Validation

The validation suite is split by responsibility.

| path | checks |
|---|---|
| `tests/discretization/proof_rule_validation.py` | exact, linear, convex, factored, and reachability rules |
| `tests/discretization/certificate_validation.py` | valid certificates and deliberate proof mutations |
| `tests/discretization/source_mapping_validation.py` | source reconstruction and missing annotation deferral |

The same pipeline and checker implementations are used for the thermostat,
chemical mixing plant, and discrete cruise controller. There are no
model-specific solver branches.
