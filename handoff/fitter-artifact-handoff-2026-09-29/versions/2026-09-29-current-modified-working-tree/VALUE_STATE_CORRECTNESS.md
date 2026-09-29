# Physical and held values: correctness repair

## Current result

The value representation and feature-value operators are repaired. The bundled
models are **not certified**: preserving their received sensor values exposes a
missing ordered composition of sampling, delivery, decisions, and plant updates.
The pipeline now stops at Stage 3 with `sample_event_composition_required`; it
does not authorize Stage 5. This change does not complete the broader
discretization correspondence repair plan.

## 1. Initial values and bindings have different meanings

SysML `attribute x := expression` initializes stored state. `attribute x =
expression` binds its value to that expression. See SysML 2.0 Language §7.13.4:
<https://www.omg.org/spec/SysML/2.0/Language/PDF>.

The parser now retains this distinction instead of guessing initialization from
a later assignment. Runtime initialization evaluates initial expressions once,
in dependency order. Reading a bound expression evaluates its current
dependencies, including after an intervening action, without waiting for the
next constraint-solver call. Cyclic bindings/initializations and assignments to
bound values are rejected.

Twelve declarations in the bundled sources were explicitly corrected from `=`
to `:=`:

| Model | Mutable initial values corrected |
|---|---|
| Cruise | System clock; initial vehicle speed and gap |
| Thermostat | System clock; initial temperature |
| Mixing | Two held tank readings; scan clock and system clock; three initial tank levels |

These are source-model corrections, not meaning-preserving rewrites of the old
declarations. All 12 safety requirement definitions are unchanged. Actual
bindings such as `lastObservedSpeed = speedSensor.lastReadingMps` remain
bindings: the observation follows the held sensor reading, not vehicle speed.
The existing 5 mL reserve is unchanged.

## 2. Physical and delayed values remain distinct

The equation model now retains separate physical, stored sensor, sent payload,
and received payload variables. Its `state_value_pairs` associate each stored
reading with its physical origin; multiple delay stages can therefore have
separate pairs for one physical quantity. A pair records provenance and storage
identity. It does not assert that the values are equal or that their timing has
been proved.

For cruise, for example, vehicle speed, the sensor's last speed, its sent speed,
and the controller's accepted speed remain separate. The policy reads the
accepted value. A source requirement referring to the sensor's last reading
retains that exact reference. Physical interval calculations use physical state.
Dependencies and sampling assignments no longer authorize replacing a held
reading by the current physical value, including transformed readings.

Runtime callers can obtain an explicit snapshot at any time:

```python
engine.state_value_pairs()   # also adapter.state_value_pairs / env.state_value_pairs
# [{"engine_time": ...,
#   "physical": {"state_variable": ..., "available": True, "value": ...},
#   "sampled": {"state_variable": ..., "available": ..., "value": ...}}, ...]
```

An unreceived value is unavailable (`None`), rather than fabricated from current
physics. Reading these snapshots does not advance execution. Physical values
are not added to the trained policy's observation vector.

The same inventory is propagated through Markov certificates, reduced MDP
specifications, and discretization analyses/certificates as `value_semantics`.
Source verification reconstructs and compares it; deleting pairs, swapping
roles, or reintroducing the old sensor-to-physical substitutions is rejected.
The Markov schema is now 3, reduced specification schema 2, and discretization
schema 4. These versions identify this value-semantics change; version 4 does
not assert implementation of every obligation in the broader repair plan.

Stage 3 writes `03_markov_mdp/value_semantics/*.json` even when certification
fails. The pipeline preserves its partial summary and failure report.

## 3. Exact unresolved transition obligation

The extractor stores sampling and delivery equations separately as events. An
accepted value cannot simply become an alias to the sending plant variable.
The bundled models require those events to be composed in their actual order
between decisions, with held values retained between updates. That composition
is not implemented by this repair. Missing receive transitions and
`sample_event_composition_required` therefore block certification.

The positive test fixture `tests/discretization/fixtures/held_constant.sysml`
contains distinct held and physical state without asynchronous message events.
It passes saved Markov certificate → reduced specification → discretization
certificate generation, reload, and checking. It demonstrates that the new
value format works for a supported case; it does not certify the bundled models.

## 4. Verification and replay

Run from the repository with its existing virtual environment:

```bash
PYTHONPATH=src .venv/bin/python tests/discretization/value_state_validation.py
PYTHONPATH=src .venv/bin/python tests/training/validate_sysml_safety.py --out-dir results/value_safety
PYTHONPATH=src .venv/bin/python tests/training/validate_value_replay.py \
  --saved-runs outputs/safety_reserve_full/05_reduced_training/runs \
  --out-dir results/value_replay
OUT_DIR=outputs/value_state_repair_full bash run_fitting_sequence.sh --dt 0.1
```

Use fresh output paths. The full launcher retains its existing experimental
settings; it stops at Stage 3, so no new training run is completed.

The focused suite covers 20 tests, including the existing certificate mutation
cases applied to a genuinely passing paired-state fixture. The safety accounting
suite checks all 12 source requirements across direct, reduced, and recurrent
evaluation paths, plus reserve and failure-preservation regressions. These tests
check specific behavior; they are not a proof of all executions.

The full saved-policy replay uses the existing best weights, all 200 test
episodes per model, saved settings and seeds, and original requirement
expressions. It records requirements at policy pauses and completed cycles,
including reset warm-up. It also repeats all episodes without instrumentation
and compares observations, actions, rewards, values, log probabilities, outcomes,
and requirement reports exactly (execution-time measurements are excluded).

| Model | Episodes with a false source requirement during reset | During controlled rollout |
|---|---:|---:|
| Cruise | 200/200 | 0/200 |
| Mixing | 0/200 | 0/200 |
| Thermostat | 181/200 | 0/200 |

The reset failures concern `Accelerate When Below Target`, `Heat When Cold`,
and `Cool When Hot`: the source clock is positive while warm-up uses inactive
outputs. These are failures of the actual source safety requirements, not a
different category of violation. The normal episode evaluator excludes those
reset checks and reports zero. The replay records both boundaries, retains all
false evaluations in a compressed ledger, and exits unsuccessfully when any
source safety requirement is false. Fixing warm-up/controller timing is not
part of the two value repairs above.

The legacy Markov battery still assumes that all three bundled models certify;
it fails those expectations and then attempts to mutate an absent proof. It is
not an all-green validation result. Its certificate mutations are exercised
separately by the focused suite against the valid paired-state fixture.
