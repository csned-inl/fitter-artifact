# Transfer reserve and SysML safety accounting

## Why this correction was necessary

Commit `b28ffc26a2e6f2b880ecd7ce26d3519e1321c0b2` (July 22, 2026)
removed the historical 5 mL reserve from scenario generation. It allowed transfer
targets to equal the entire starting tank volume while retaining a controller
rule that acts on delayed volume readings. In the reproduced test set, 84 of
200 chemical-mixing episodes violated the actual `No Dry Running` requirement.

The same change replaced the reduced trainer's system-requirement-based safety
flag with a check of whether the executed action obeyed `#NeuralRequirement`.
The shield could therefore obey that rule, cause a system safety violation, and
still receive a reported safety violation rate of zero. Stage 1 used the same
incorrect proxy. The recurrent evaluation path additionally limited its count
to two hardcoded requirement names.

## Restored 5 mL reserve

Scenario generation again applies the historical rule to each extracted
`greater >= lesser` scenario relation:

```python
if original < requested_transfer + 5:
    requested_transfer = max(original - 5, lower_transfer_bound)
```

In the three bundled models, these relations are the two mixing-tank
original-volume/transfer-target pairs. Thus a nonzero transfer leaves at least
5 mL; when a tank starts below 5 mL, its transfer target is zero. An 11 mL tank
can therefore be asked to transfer at most 6 mL.

This is the explicitly restored scenario-generation policy. It changes the
requested transfer, not the safety definition. No SysML model or requirement
expression was changed, and no safety violation is excused by a tolerance,
reserve, controller rule, or whitelist. Other experimental settings, including
seeds, architectures, training counts, optimizer settings, and `dt`, remain
unchanged.

## The safety source of truth

For every environment step, the simulator evaluates the parsed system
requirement expressions from the actual SysML source against its current state.
The environment uses that exact `requirement_statuses()` dictionary for both
termination and `info["statuses"]`. It publishes all requirement results on
nonterminal as well as terminal steps.

All three evaluation paths—direct Stage 1 evaluation, reduced feedforward
training/evaluation, and recurrent evaluation—count an episode as having a
safety violation when **any system requirement result is false**. Both
`#Prohibition` and `#Obligation` are included. There is no requirement-name filter
and no second test against a reconstructed physical property or the neural
controller rule. The latter remains the shield's action-selection rule, not a
replacement definition of safety.

A failure observed earlier in an episode remains recorded if the property later
becomes true. In phase 2, a false requirement takes priority over completion
and truncation. Missing status fields are errors rather than defaults to safe.
Aggregation derives the safety count from recorded failed requirement names,
so an inconsistent cached Boolean flag cannot erase a violation.

### Recorded quantities

- `safety_violation_rate`: episodes with any false system requirement divided
  by the number of evaluated episodes.
- `requirement_checks`: number of evaluations recorded for each source
  requirement name.
- `requirement_violation_episodes`: number of episodes in which each source
  requirement was false at least once; an episode can appear under more than
  one requirement.
- `violation_rate`: episodes ending in the environment's violation outcome.
  With phase-2 termination on the first failed requirement, this agrees with
  the safety episode rate.
- Stage 1 `pointwise_agreement`: agreement with the neural controller rule.
  This is separate from system safety and never determines the safety count.

Checkpoint selection uses the corrected system safety rate. A pipeline run
with no zero-safety-violation trained candidate fails after saving its training
reports, rather than finishing successfully with an unsafe selection.

## Errors cannot silently remove a requirement

A source requirement that cannot be parsed, contains an unparsed expression
suffix, or duplicates another requirement name is rejected. Requirement
evaluation rejects unresolved references, division by zero, unsupported
expressions, and non-Boolean results. These errors stop evaluation; they do not
turn a missing value into zero/false or silently drop a requirement.

The dynamics evaluator and simulation update order were not changed by this
strict requirement-evaluation behavior.

## Regression tests

`tests/training/validate_sysml_safety.py` is included in the existing
`validate_reduced_stack.py` validation command. It:

1. Creates temporary SysML variants for each of the 12 bundled system
   requirements, changes that actual source clause to `false`, and gives it a
   new name. Reduced, direct, and recurrent evaluation must each report a
   safety rate of 1.0 and retain that name.
2. Checks serial/process collection and aggregation preserve requirement
   names, evaluation counts, and failure counts.
3. Checks unsafe candidates are rejected, transient failures persist, and
   completion cannot mask a failed property.
4. Rejects malformed, unresolved, non-Boolean, and division-by-zero
   requirements, plus duplicate names.
5. Checks the reserve on both tanks for the unchanged 200 test seeds.
6. Recreates an original dry-running failure with an unsafe transfer request
   in a separate test fixture, leaves the original SysML file unchanged, and
   requires that `No Dry Running` is counted as a safety violation.

The Markov negative test for a malformed source requirement now expects
rejection during parsing, before certificate construction.

## Validation results

Completed on September 15, 2026, on Kubuntu-Desktop. The full five-stage run is
saved in `outputs/safety_reserve_full/`; validation and replay results are in
`outputs/safety_reserve_validation/`.

### Full pipeline and saved-policy replay

The full run completed with the existing settings: `dt=0.1`, training seed 0,
2,000 PPO episodes, 100 oracle epochs, and 200 held-out test episodes per model.
It used the original derived hidden widths of 3, 4, and 2 respectively. This
was the complete training run, not the optional smoke run.

| Model | Test episodes | Successful episodes | Episodes with a safety violation | Steps checked | Source requirements checked per step |
|---|---:|---:|---:|---:|---:|
| Cruise controller | 200 | 200 | 0 | 8,500 | 5 |
| Chemical mixing | 200 | 200 | 0 | 6,509 | 4 |
| Thermostat | 200 | 200 | 0 | 96,997 | 3 |

Each saved trained policy was replayed on the same 200 test seeds. A separate
observer queried the simulator's actual source-requirement results after every
step and checked that the published statuses, episode failure names, safety
flags, and summary counts agreed. All agreed. All three SysML files have the
same SHA-256 hashes as before this correction.

### Control that must fail safety

In a separate replay only, the restored reserve was disabled while keeping the
new safety accounting and the original mixing specification. This reproduced
**84 of 200 episodes failing `No Dry Running`**, and the reported safety
violation rate was correctly **0.42 (42%)**. The unsafe episodes are therefore
detected and counted; the zero rate in the restored-reserve run is not produced
by suppressing that requirement.

### Validation suites

All five existing validation commands passed: reference models, Markov
certification battery, discretization, reduced training stack, and recurrent
gradients. The discretization suite was rerun against the new full-run
certificates: all three complete certificates were accepted and 48 deliberately
incomplete or inconsistent certificate mutations were rejected.

The added safety tests passed for all 12 source requirements in all three
evaluation paths. Malformed, unresolved, non-Boolean, division-by-zero, and
duplicate-name requirements were rejected; all 400 tank reserve checks passed.
The original dry-running fixture, transient failure preservation, completion
priority, and serial/process accounting checks also passed.

The initial Markov battery stopped because its malformed-requirement test
expected rejection later during certification. Its expectation was updated to
require the new, earlier parsing rejection; the rerun passed. Both logs are
retained with the validation evidence.

## Scope of the result

These changes restore the reserve and correct evaluation of the actual SysML
safety properties at the environment's observation points. Phase-1 oracle data
collection still has its documented training-only reward behavior; the safety
accounting is not defined by that reward.

This repair does not resolve the previously diagnosed formal proof/runtime
alignment problems: the physical proof's initial set, sensor-to-physical
mapping, and controller-interval timing still require separate correction.
Passing runtime evaluations and certificate-completeness tests must not be
reported as resolution of those continuous-safety proof problems.
