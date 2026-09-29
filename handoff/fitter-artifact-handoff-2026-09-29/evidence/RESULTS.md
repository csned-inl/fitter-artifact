# Compact/lazy implementation and workstation results

## Outcome

**Implementation is incomplete. No complete new MDP or discretization certificate was produced.**

The new expression equations, storage inventory and native-type specialization
are not connected to `source_solver.py`. Normal certification still invokes
`transition_equations.compile_program`, which enumerates configuration paths.
The full pipeline reached Stage 3 and its service was killed at the enforced
8 GB memory limit after 23.009 seconds. Stages 1 and 2 wrote results; Stages 4
and 5 did not run. A memory termination is not a safety counterexample.

## Implemented components

- `lazy_expressions.py`: shared named equations, conditional values/statuses,
  native integer and binary64 arithmetic, short-circuit error propagation,
  separate physical and held values. Increasing independent guards produces
  linear equation/serialization growth in the tested fixture family.
- `lazy_storage.py`: source-derived finite mailbox/frame/payload inventory;
  recursive calls and unknown mailbox writers are rejected. This inventory
  does not implement the symbolic heap or prove message-operation semantics.
- `lazy_specialization.py`: checks initial and preserved value constructors
  before projecting to native solver fields. Type-changing updates are rejected;
  possible execution errors are reported separately from type preservation.
- Both producer and independent property reconstruction reject a stored sampled
  value simultaneously classified as a timeless definition. No sampled-to-physical
  replacement is introduced.

## Latest component validation

All computations ran on `kubuntu-workstation` through Harnesslite.

- 11 lazy-expression/storage/property-preservation tests passed at commit
  `8096e07fcc5720a0e7805543c4c5bfb3facbd489` (1.110 seconds test runtime,
  75.6 MB service memory peak). These include 726 native value/error comparisons,
  short-circuit checks, distinct physical/held values, substitution rejection,
  source storage inventories and invalid mailbox-writer rejection.
- 5 specialization tests passed at commit
  `31fed06333aeed8e6ca88647446202bac61fdcd9` (0.020 seconds).
- Backend checks: named float equations, recursive float fixture and the
  constructor-checked native recursive fixture returned UNSAT. The generic
  mixed-type recursive fixture returned UNKNOWN at the unchanged 1000 ms limit.
  This is a timeout, not a counterexample or an unsupported-theory diagnosis.
  The capability script correctly exits unsuccessfully.

The native specialization result is established for those fixtures; it does
not establish native kinds for all benchmark storage or solve the mixed-type
integration problem.

## Full validation run

Pinned commit: `7af340804dac76585e5166ab185bea0c71a67ed3`.
Harness job: `f044175b-13dc-4213-bd4a-001e3f7c8ba6`.
Each command had its own enforced 8 GB memory limit and no swap; original
solver timeouts, seeds, episode counts and full pipeline settings were retained.
Later component commits did not change the executed production pipeline.

Exit code 0 means the command completed successfully. Exit code 1 includes
assertion failures and systemd reporting an out-of-memory termination; the
notes distinguish them. Seconds are wall-clock command duration.

| Command | Exit code | Seconds | Result |
|---|---:|---:|---|
| lazy_expressions | 0 | 1.422 | 10 tests passed at this earlier snapshot |
| execution_equations | 0 | 1.218 | 14 tests passed |
| backend_capability | 1 | 1.243 | Mixed-type recursive query timed out |
| mixing_contract | 1 | 0.088 | Assertion about the old chained property interpretation failed |
| reference_models | 1 | 0.194 | Four message-response fields lack source transitions or justified holds |
| response_timing | 0 | 0.153 | 3 tests passed |
| original_parser | 0 | 0.278 | 4 tests passed |
| certification_battery | 1 | 583.657 | Killed at 8 GB during path compilation |
| value_integration | 1 | 0.791 | 7 assertion failures and 1 error among 36 tests |
| discretization_arithmetic | 0 | 0.310 | Passed |
| source_safety | 0 | 1.101 | Passed; 12 source properties counted in all three evaluation paths |
| discretization_values | 1 | 0.516 | 11 assertion failures and 2 errors among 20 tests |
| compact_transition | 1 | 24.761 | Killed at 8 GB after path compilation |
| decision_transition | 1 | 24.771 | Killed at 8 GB after path compilation |
| reduced_stack | 1 | 572.838 | Killed at 8 GB in certificate gate |
| saved_policy_replay | 0 | 78.138 | 600 episodes completed; zero safety failures or accounting mismatches |
| full_pipeline | 1 | 23.009 | Killed at 8 GB in Stage 3; Stages 4 and 5 did not run |

The failing integration tests include expectations for earlier property text,
parser behavior, checking boundaries and diagnostic names. Their logs are
preserved; this run does not establish that every failure is only a stale test.
No failing assertion was weakened to obtain a pass.

## Saved-policy safety and source-equation comparison

The original three checkpoints were replayed for 200 episodes each: 600 total.
Every model recorded zero requirement violations, independently observed zero
violations, zero evaluation errors and zero accounting or instrumentation
behavior mismatches. The independent concrete source-equation evaluator agreed
on 224,400 controller responses and 1,200 initializations (instrumented and
uninstrumented executions). These are execution-test results, not a universal
MDP or continuous-interval proof.

## Remaining implementation

The normal MDP route still needs compact local equations for message copies,
ordered acceptance/removal, object sharing, machine continuations and constraint
propagation; a source-justified type representation for those operations;
replacement of configuration/path enumeration; and connected progress,
finite-history reconstruction and independent certificate checks. The current
mixed-type recursive feasibility check has not succeeded.

## Protected inputs and reversible snapshots

The SysML files, parser, simulator, controller/runtime and training settings
match the task-start hashes. The user's normal HEAD and staging area were
preserved. Baseline snapshot commit:
`256941e8926de722ae277d86b458043e57a2e6b5`.
Latest component snapshot on `codex-compact-lazy-worker`:
`8096e07fcc5720a0e7805543c4c5bfb3facbd489`.

Collected worker JSON results are under `worker-artifacts/`; complete harness
event streams and fetched log tails are in this directory. The worker reported
no active `clarity-validation-*` units after validation.
