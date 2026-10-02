THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# OT Markov SysML profile 0.1

## Purpose

This is the normative input contract for the first generic direct-SysML Markov
certificate pipeline. It covers OT models with the structural conventions used
by the standalone Thermostat, Mixing Machine, and Cruise Controller models. It
does not define arbitrary SysML execution semantics.

The required public operation is:

```python
prove_markov(model_path) -> Certificate | Counterexample | Unsupported
```

The compiler MUST derive its relation and candidate buffer from the supplied
model. Model names, variable names, equations, constants, action counts, source
hashes, and buffer depths MUST NOT select model-specific proof code.

## Source preservation

Profile 0.1 requires no edits to the authoritative models. The compiler reads
the model and either accepts its structure under this profile or returns an
explicit `UNSUPPORTED` diagnostic. It MUST NOT repair, rewrite, supplement, or
silently reinterpret the source.

If a future model cannot express a required distinction with the existing
annotations, any proposed SysML annotation change is a separate source change.
It requires a versioned profile update, preserved pre-change source, a tagged
authoritative commit, and user review before certification resumes.

## Semantic anchors

An accepted model MUST provide all of the following.

1. Exactly one instantiated top-level `part system`.
2. Exactly one controller decision interface tagged `#Neural action def Policy`.
3. A `#NeuralRequirement` whose subject is that Policy and whose constraint
   defines valid Policy outputs for its inputs.
4. Zero or more `#ScenarioInput` attributes and one or more
   `#ScenarioConstraint` constraints.
5. Safety requirements tagged `#Prohibition` or `#Obligation`.
6. Explicit persistent attributes, state-machine modes, bindings, constraints,
   assignments, flows, connections, and action calls sufficient to trace one
   decision boundary to the next.

Missing or ambiguous anchors produce `UNSUPPORTED_PROFILE`; they do not cause
the compiler to guess.

## Decision boundary and observation

A decision boundary is an invocation of the tagged Policy. The compiler MUST
locate invocations structurally, including invocations inside performed
subactions such as a scan cycle.

The ordered non-`#Completion` Policy input bindings form the current controller
observation. A scenario input is fixed for one MDP instance; when it is also
bound to a Policy input it is both fixed context and controller-visible.

The expression bound to a `#Completion` input is a selected result predicate,
not automatically an independent observation component. Every dependency of
that expression remains part of the proof and buffer analysis.

Two compared executions share the same fixed scenario context, Policy proposal,
and current buffered controller view. Hidden physical, sensor, actuator, clock,
and protocol state remain independent unless the source equations or buffer
reconstruction prove them equal.

## State and transition relation

The compiler MUST preserve distinct symbols for every persistent physical,
sensor, controller, actuator, protocol, timer, and state-machine value. It may
identify two values only when an explicit source binding or proved equation
requires equality at the selected decision boundary.

The one-step transition is the symbolic composition of source-defined effects
from one Policy invocation to the next:

- assignments and continuous-rate updates;
- performed subactions, expanded in source order;
- sends, accepts, and their matched state-machine effects;
- bindings, flows, and algebraic constraints;
- scan guards, clocks, and held sampled values; and
- actuator commands selected from the executed Policy output.

Source order is respected within an action body. Algebraic bindings and
constraints are simultaneous relations. If concurrent effects, message choice,
event ordering, or the next decision boundary are not uniquely determined by
the supported structure, compilation returns `UNSUPPORTED_SEMANTICS`.

The compiler MUST construct this relation directly from the parsed source. It
MUST NOT run, encode, or symbolically recreate the general-purpose simulator.

## Proposed and executed actions

The controller action is the tuple of Policy output values. Profile 0.1 accepts
finite Boolean, enumeration, or explicitly bounded-integer output domains.

The NeuralRequirement defines whether a proposed action is valid. The symbolic
shield keeps a valid proposal. An invalid proposal may be replaced only when
the source constraint determines exactly one valid output tuple at that
decision boundary. Otherwise the model requires an explicit deterministic
replacement rule or compilation returns `UNSUPPORTED_SHIELD_NONDETERMINISM`.

The executed action, not merely the proposal, is used for actuator effects and
stored action history.

## Analytical buffer candidate

Candidate generation is a backward dependency and reconstructibility analysis,
not a search over executions or buffer sizes.

The roots are:

- next observation;
- completion or outcome;
- reward, when present;
- every selected prohibition and obligation status; and
- persistent state needed to reach the next decision boundary.

For every root dependency, the compiler MUST produce one checked explanation:

- available in the current observation;
- fixed process context;
- available at an exact prior-observation lag;
- available at an exact prior-executed-action lag;
- exactly reconstructible through a source equation;
- overwritten before every relevant use; or
- outside the complete selected-result and successor dependency cone.

Temporal dependency edges carry an exact decision-boundary lag. The proposed
observation and action depths are the maximum required lags. The current
observation is always present, so depth zero means no prior observations.

Algebraic reconstruction is allowed only when the compiler proves the inverse
is total and unique over the source domain. A temporal cycle without a proved
finite reconstruction bound returns `NO_FINITE_CANDIDATE`. Any unclassified
dependency returns `UNSUPPORTED_RECONSTRUCTION`. Neither result is a proof of
non-Markov behavior.

The candidate and its reconstruction evidence are recorded in the proof
artifact. Candidate generation alone never certifies the Markov property.

## Certificate obligations

The generic prover builds two independent copies of the compiled relation that
share fixed context, proposed action, and candidate buffer. A positive
certificate requires all of the following queries to be `unsat`:

1. no valid initial state exists (the initialization-emptiness countercheck);
2. an initial state lies outside the reconstruction invariant;
3. a continuing transition leaves the reconstruction invariant; and
4. equal buffered views and equal proposed actions can produce different
   selected results or different successor buffered views.

The first query is deliberately negated: `unsat` means at least one valid
initial state exists. Solver `unknown`, timeout, formula-budget
failure, incomplete compilation, or any failed obligation yields `NO_RESULT`.
None may be converted into a certificate.

## Profile 0.1 acceptance test

The same compiler entry point MUST consume the unchanged authoritative
Thermostat, Mixing Machine, and Cruise Controller SysML files. It MUST NOT
import or dispatch to model-specific symbolic-machine modules. Existing
model-specific proofs remain regression oracles only and are not part of the
generic pipeline.
