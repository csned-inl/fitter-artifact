THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Binding plan: one-query thermostat reset counterexample prototype

Status: **frozen on 2026-10-02.** This replay-witness plan remains archival
evidence only and authorizes no further production work. The user selected the
direct SysML symbolic-machine route for the next prototype; its binding scope
is recorded in `SYSML_MARKOV_REPRESENTATION_PROPOSAL.md`.

## 1. Exact goal

Build one fixed-buffer prototype that gives Z3 only the information needed to validate one reset-state counterexample to Markovity for `(b_obs=2, b_act=1)`.

The prototype does not attempt an `UNSAT` proof. A reset counterexample has already been identified and replayed. The only authorized task is to have Z3 validate the exact admissibility/equal-buffer premises, replay the two transitions through the named source oracle, and either confirm the disproof or stop with no result.

## 2. Exact system and visible result

The evaluated composition is explicitly:

```text
fixed policy proposal 0
    -> SpecShield(proposal, raw pending thermostat inputs)
    -> BufferedDiscreteEnv.step(shield-executed action)
```

The repository does not currently provide one production wrapper that performs this composition. The result therefore concerns this explicitly composed shield-plus-buffered-environment profile. It does not claim that an uncomposed `BufferedDiscreteEnv` automatically invokes the shield.

The controller-visible step result is exactly:

```text
(buffered_observation_or_None, reward, done)
```

`info` is outside the Markov-state claim. External time-limit truncation is irrelevant because the decisive witness differs on intrinsic completion in the first action. Terminal observations remain included because the current runtime can return and buffer pending model inputs when intrinsic completion occurs.

## 3. Exact fixed buffer

The current state is the ten-element binary32 buffer produced at reset by `BufferedDiscreteEnv(n_obs=2, n_act=1)`:

```text
[
    current_setpoint_observation,
    current_temperature_observation,
    prior_observation_1_setpoint_zero,
    prior_observation_1_temperature_zero,
    prior_observation_2_setpoint_zero,
    prior_observation_2_temperature_zero,
    prior_action_0_zero,
    prior_action_1_zero,
    prior_action_2_zero,
    prior_action_3_zero,
]
```

No other buffer size is supported or parameterized.

## 4. Exact decisive witness

The two sides use these source-valid reset inputs:

| Value | Left | Right |
| --- | --- | --- |
| Raw setpoint decimal | `24.89999846822714` | `24.900000480562397` |
| Raw setpoint Float64 bits | `0x4038e6664cb37c54` | `0x4038e6666e766658` |
| Raw temperature | `23.9` | `23.9` |
| Raw temperature Float64 bits | `0x4037e66666666666` | same |
| Tolerance Float64 bits | `0x3ff0000000000000` | same |
| Observation scale Float64 bits | `0x4037e6330c12f39e` | same |
| Normalized setpoint Float32 bits | `0x3f855c2a` | `0x3f855c2a` |
| Normalized temperature Float32 bits | `0x3f800113` | `0x3f800113` |
| Reset history | eight binary32 zeros | eight binary32 zeros |
| Policy proposal | `0` | `0` |
| Latched completion | `true` | `false` |
| Shield-executed action | `0` | `1` |
| Reward | `1.0` | `-0.01` |
| Done | `true` | `false` |
| Runtime outcome | terminal success | continuing/running |

Both setpoints satisfy the declared scenario domain `[13,33]`.

The normalized setpoints collide because the runtime performs binary64 division by the fixed scale and then binary32 rounding. The completion expression and shield do not read this rounded observation; they read the raw binary64 setpoint and temperature.

For the left side, raw setpoint is inside the completion band and the initial heater/AC flags are false, so completion is true. The neutral proposal satisfies the shield and executed action is `0`.

For the right side, raw setpoint lies immediately above the upper completion threshold. Completion is false and the shield requires heating, so executed action is `1`.

The current complete buffers and policy proposals are identical, while the returned controller-visible results differ in reward, done, and buffered observation. This is sufficient to disprove the Markov property for the fixed buffer in the explicitly composed profile.

## 5. Exact Z3 query

The query is a ground premise-validation formula. Z3 is not asked to rediscover the witness or model either transition.

It contains only:

1. the two fixed binary64 setpoint constants;
2. the shared binary64 observation scale and domain-bound constants;
3. exact domain-membership checks for both setpoints;
4. exact binary64 division followed by round-to-nearest-even conversion to binary32;
5. equality of the resulting setpoint observation bits.

The other current-buffer components are not emitted to Z3 because they are identical constants on both sides:

- both temperature observations use the same raw temperature and scale;
- both prior-observation slots are binary32 zero;
- both prior-action one-hots are four binary32 zeros.
- both policy proposals are the same fixed integer `0`.

Completion, shield action, reward, done, and returned-buffer differences are not encoded in Z3. The concrete controller/simulator oracle establishes those transition results by executing both sides. Classification requires both the Z3-validated premises and the oracle-observed result difference.

There are no free search variables and no transition equation of any kind: no shield, completion, reward, plant, outside temperature, actuator mode, command transport, scheduler, history, or reachability relation.

## 6. Minimal source binding

Only these current source facts may be checked:

1. thermostat setpoint scenario bounds;
2. reset raw temperature and initial false controller flags;
3. observation field order, normalization scale, binary64 division, and binary32 cast;
4. reset buffer layout and zero padding;
5. the thermostat neural-requirement comparisons and action-bit mapping;
6. the completion expression;
7. terminal publication of pending model inputs;
8. intrinsic-success and continuing reward/done results;
9. action push before returned-buffer augmentation.

Binding uses exact source-file hashes plus direct execution of the named current functions/classes. No new parser, extractor, intermediate representation, event inventory, scheduler model, translation validator, reduction framework, certificate system, or source graph may be created.

If any listed source fact differs from Section 4, production stops. The witness is not adjusted and the proof scope is not broadened without user approval.

## 7. Exact runtime replay

The test will replay both sides using a test-only deterministic scenario provider that supplies the exact admissible setpoint and a fixed admissible outside temperature, while leaving the normal environment reset and step path unchanged.

For each side it will:

1. create `BufferedDiscreteEnv` with `dt=0.1`, phase 2, `n_obs=2`, and `n_act=1`;
2. reset with the exact scenario value;
3. record the complete reset buffer as binary32 bits;
4. read the raw pending model inputs;
5. call `SpecShield(0, raw_inputs)`;
6. pass the shield result to `BufferedDiscreteEnv.step`;
7. record the returned buffered observation bits, reward, and done;
8. close the environment.

The replay passes only if:

- current reset buffers are bit-identical;
- the shared proposal is `0`;
- completion is exactly left `true`, right `false`;
- shield actions are exactly left `0`, right `1`;
- rewards are exactly left `1.0`, right `-0.01`;
- done values are exactly left `true`, right `false`;
- both returned observations are present;
- returned buffers differ consistently with the exact wrapper update.

The scenario claim is relative to the model's bounded nondeterministic scenario domain. The plan does not claim that a particular NumPy RNG seed generates either exact binary64 value.

## 8. Result classification

Exactly two classifications are permitted.

### `DISPROVED_MARKOV_FIXED_BUFFER_FOR_EXPLICIT_COMPOSITE_PROFILE`

Returned only when:

- source binding passes;
- the one Z3 ground query returns `SAT`;
- every query/formula budget passes;
- exact runtime replay passes every condition in Section 7.

### `NO_RESULT`

Returned for `UNSAT`, `UNKNOWN`, timeout, exception, source mismatch, replay mismatch, budget failure, or any test failure.

`UNSAT` does not prove Markovity because this prototype checks one concrete counterexample only. Any non-SAT result stops production and requires a new user-approved plan. It does not authorize R1, steady-state, plant, scheduler, or timeout work.

## 9. Hard architecture and complexity limits

| Metric | Hard limit |
| --- | ---: |
| Z3 queries | exactly 1 |
| Free symbolic inputs | 0 |
| Serialized query size | 4,096 bytes |
| Declarations/definitions | 8 |
| Assertions | 8 |
| Unique Z3 AST nodes | 120 |
| Maximum `ite` depth | 0 |
| Solver timeout | 5 seconds |
| Plant-transition instances | 0 |
| History-transition instances | 0 |
| Control/event/path selectors | 0 |
| Quantifiers | 0 |
| Arrays | 0 |
| Recursive relations | 0 |
| Generic runtime containers | 0 |
| Production modules | exactly 1 |
| Focused test modules | exactly 1 |
| Production logical lines | at most 300 |
| Test logical lines | at most 500 |

Crossing any limit produces `NO_RESULT` before solving and stops production.

## 10. File allowlist

Only these files may change after user approval:

1. `src/clarity/certification/thermostat_reset_markov_witness.py` — the fixed ground Z3 query, source identities, budgets, and result classification;
2. `tests/certification/validate_thermostat_reset_markov_witness.py` — source checks, replay, mutations, budgets, and fail-closed tests;
3. this plan document, only through a user-approved amendment.

No workstation script, runbook, generic solver module, existing proof module, runtime module, model, or buffer implementation may be changed.

The implementation may import the pinned `z3` package directly. It may import the current runtime classes only for source replay. It may not import any existing `clarity.certification.markov_*`, graph, extraction, transition, history-window, or paired-query module.

## 11. Required focused tests

The one test module must check:

1. exact source file identities;
2. exact witness Float64 bit patterns;
3. exact normalized Float32 bit collision;
4. exact complete reset-buffer bit equality;
5. raw completion `true` versus `false`;
6. raw shield action `0` versus `1` for shared proposal `0`;
7. exact reward and done difference;
8. terminal observation is returned rather than omitted;
9. action push occurs before returned-buffer augmentation;
10. exact oracle-returned tuple and returned-buffer difference;
11. Z3 returns `SAT` on the ground admissibility/equal-buffer premise formula;
12. serialized formula satisfies every budget;
13. forbidden imports and forbidden symbol classes are absent;
14. table-driven mutations to the Z3 bounds/Float32 premise or to the oracle replay expectations for completion, shield action, padding, action mapping, reward/done, or buffer ordering prevent disproof classification;
15. `UNSAT`, `UNKNOWN`, timeout, source mismatch, replay mismatch, and budget failure all return `NO_RESULT`;
16. query generation and query hash are deterministic.

No generic fixture, mutation, extraction, certificate, or test framework may be created.

## 12. Implementation sequence

1. Obtain explicit user approval of this exact revised plan.
2. Create the one production module and one test module.
3. Run non-solver source, replay, mutation, and budget checks locally.
4. Run the single ground Z3 query locally if the pinned Z3 package is available.
5. If local Z3 is unavailable, publish only the two allowlisted code/test files and have the user run one direct test command; do not modify workstation automation.
6. Report exactly one classification from Section 8.
7. Stop.

At any failure, produce one consolidated report and make no code change, timeout increase, formula split, abstraction change, rerun, or new query until the user approves an amended plan.

## 13. Explicitly forbidden work

This plan does not authorize:

- R1 or steady-state cases;
- an `UNSAT` Markov proof;
- plant or physical-temperature dynamics;
- outside-temperature reasoning beyond one admissible replay value;
- actuator modes or command mailboxes;
- scheduler or event semantics;
- source extraction infrastructure;
- a generic Markov IR;
- a generic buffer implementation;
- proposal variables or proposal search;
- witness search;
- invariant generation;
- automatic buffer selection;
- certificate infrastructure;
- full-runtime recreation;
- another SMT solver;
- optimization after timeout;
- deletion of the obsolete implementation;
- any other production model.

If the exact reset witness fails validation for any reason, production stops and returns to the user. Nothing in this document permits expanding the implementation to find another witness or attempt a proof.

## 14. Final approval checklist

- [ ] The user approved this exact revised plan.
- [ ] The implementation contains exactly one production module and one test module.
- [ ] Z3 receives exactly one ground admissibility/equal-buffer premise formula.
- [ ] Z3 receives no plant, history, scheduler, event, transport, or runtime-container representation.
- [ ] The full current reset buffers are exactly equal.
- [ ] The policy proposal is exactly the same fixed value `0`.
- [ ] The oracle replay confirms completion and shield read raw binary64 inputs.
- [ ] The oracle-observed controller-visible returned tuples differ exactly as specified.
- [ ] Both returned observations are modeled and replayed.
- [ ] Source checks, budgets, mutations, Z3 SAT, and runtime replay all pass.
- [ ] The result claim is limited to the explicit composite profile.
- [ ] Production stops immediately after classification.

Any unchecked item means `NO_RESULT` and immediate stop.
