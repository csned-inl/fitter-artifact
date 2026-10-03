THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Structural discretization-safety certificate

Status: implemented experimental profile `direct-sysml-structural-discretization-0.1`.

## Exact claim

For every literal source `#Prohibition` and `#Obligation` accepted by the
profile, the checked shield establishes its mapped predicate at completion of
the Policy-containing action. Every value retained by that predicate is then
held through the quiescent interval before the next Policy-containing action.

The profile treats one SysML action execution as an atomic logical boundary. It
does not certify transient states inside a non-atomic hardware implementation,
message-delivery delay absent from the source semantics, floating-point
refinement, or an unstated physical property. An implementation that exposes
intermediate command states must separately prove that those microsteps
preserve the properties or refine the atomic source action.

In particular, the Mixing Machine certificate proves the literal requirements
over `controller.observedLevel*`, `pump*.isRunning`, and `valve*.isOpen`. It does
not silently strengthen “sensor reports empty” into “physical tank level is
empty.” The 2 mL tolerance appears in the source sampling relation, but this
profile does not yet issue a derivative/Lipschitz margin certificate for a new
physical-level requirement.

## Fast proof rules

The checker is one-way: it returns a positive certificate or `INCONCLUSIVE`.
It never reports a negative theorem and never converts a timeout or unsupported
construct into safety.

1. Parse the model and literal safety-property inventory directly from SysML.
2. Require a total functional shield relation and extract its action equations.
3. Map each property reference only through a checked source relation:
   controller Policy input binding, fixed controller parameter, unique direct
   sample copy, direct controller latch, or verified actuator command effect.
4. For event-driven actuator effects, check the actual command message type,
   receiver guard, Boolean payload, receiver assignment, controller branch
   conditions, and independence from the prior actuator value.
5. Substitute the shield equations and refute `domain AND NOT(property)` using
   bounded propositional normalization, affine interval contradiction, or a
   bounded unit-multiplier Farkas certificate.
6. Accept only predicates whose mapped observation, fixed-context, and action
   latch values are held during the certified interval.

The Farkas rule adds selected affine inequalities with exact rational
arithmetic. It certifies only when all variable coefficients cancel and the
sum is an impossible constant inequality. It tries at most four source rows.
Failure is inconclusive and causes fallback; it does not authorize a broader
search or a heuristic approximation.

## Independent semantic validation

`discretization_semantic_validation.py` reconstructs each compiled
counterexample independently in Z3:

```text
source scenario domain
AND mapped domain
AND fixed-observation equalities
AND shield relation
AND NOT(source-derived property)
```

Every query must be `unsat`. `sat`, `unknown`, timeout, tool failure, or an
unavailable solver means `NOT_VALIDATED`. This cross-check validates the
Boolean and arithmetic consequence after mapping. Source-mapping soundness is
tested separately with semantic mutations because reusing the same mapping in
both paths would not independently detect an omitted or inverted source edge.

Current adversarial mutations include inverted actuator receive assignments,
wrong ON/OFF payloads, command addresses rejected by the receiver guard,
missing scenario inequalities, false safety requirements, and requirements
over unsupported changing physical values. Every such mutation must fail
closed.

## Source identities reviewed on 2026-10-03

All three files match `csned-inl/clarity-standalone` commit
`aa3c6ae4640e3bcadcf66542eb0124e6c57e2bb3` byte-for-byte:

| Model | Standalone path | Git blob | SHA-256 |
| --- | --- | --- | --- |
| Thermostat | `sysml-models/thermostat/model.sysml` | `820df2a56af2d3a9fe54855e15a4da034ae3750b` | `44dec2333511c5cf7a40ad1828b612a043f9c7c71f6b960c276ce866f4a521aa` |
| Mixing Machine | `sysml-models/mixing-sysml-model/model.sysml` | `20ef2ed6dd55ab083ce8b1a403beab17c58dbdff` | `09d59723435da435325299934ed8472bbac3e89c6d9840df892b9f7e1c912f8f` |
| Cruise Controller | `sysml-models/cruise-controller-model/model.sysml` | `a789060d978dd52331f7b1f9485233518fa63e6a` | `3fd949d55c97de73946be48f803f982eecbb2fee78e568843d77ef4964403254` |

## Deliberately unimplemented fallback

A future physical-state rule may extract a continuous vector field, prove a
symbolic derivative bound, compute a source-declared safety margin, and check
`margin >= rate_bound * interval`. That is not part of profile 0.1. Until that
rule and its semantic mutation tests exist, any property that depends on a
changing physical value returns `INCONCLUSIVE` and must proceed to a separately
approved semantic proof.

## Commands

Fast structural certificate:

```bash
PYTHONPATH=src python scripts/certify_discretization.py path/to/model.sysml
```

Structural certificate plus Z3 cross-check:

```bash
PYTHONPATH=src python scripts/certify_discretization.py \
  path/to/model.sysml --validate-z3
```

Acceptance suite:

```bash
PYTHONPATH=src python tests/certification/validate_structural_discretization.py -v
```
