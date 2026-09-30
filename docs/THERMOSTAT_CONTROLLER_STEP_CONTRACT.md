# Thermostat controller-step contract

Status: Phase-1 golden interface, version 1. This document and its JSON artifact
freeze the source/runtime boundary that the first `MarkovIR` extractor must
preserve. They do not certify a Markov buffer, the executable simulator, or any
production model.

Machine-readable artifact:
`src/clarity/certification/contracts/thermostat_controller_step_v1.json`.
Independent reconstruction:
`clarity.certification.markov_contract.validate_thermostat_controller_step_contract`.

## Boundary

The decision interval begins immediately after the shielded, executed policy
response is installed at `system::controller/step/3/resume`. It ends at the
first next controller request, intrinsic terminal outcome, or execution error.
The next request is `system::controller/step/3`. The preceding `dt` input
declaration is retained as source event 0; it is not silently discarded merely
because it has no runtime state effect.

The `done` input is latched before the simulator pauses for the response. Even
when it is true, the response is installed, the remainder of the source cycle
runs, and all properties are checked at `cycle_end` before terminal success is
reported.

For a continuing transition, the ordered phases are:

1. Install the executed policy outputs.
2. Finish controller assignments and synchronous heater/AC command sends.
3. Finish the remaining source step programs, advance engine time, and evaluate
   all required properties at `cycle_end`.
4. Test the latched completion input.
5. Begin the next cycle, process state machines in source order, and solve the
   source constraints.
6. Update the physical environment.
7. Sample the updated temperature into the thermometer's held state, create a
   copied payload, and send it to the controller mailbox.
8. Accept and remove that payload into the controller's received storage.
9. Build the next neural inputs and stop before applying the next response.

Blocked accepts, evaluation errors, and nontermination are not identity
transitions. They prevent the later proof from certifying.

## Observation and buffer

The raw neural inputs, in source order, are `setPoint`,
`temperatureCelcius`, and the completion input `done`. The policy observation
excludes `done` and contains:

1. `setPoint`, read from `system::controller::setPointCelcius`;
2. `temperatureCelcius`, read from the accepted payload storage
   `system::controller::reading::temperatureCelcius`.

Each policy field is encoded as
`Float32(float(source_value) / 23.899216417910445)`. The exact binary32 values,
not mathematical reals, are controller-visible.

The buffered layout is current observation, prior observations newest first,
then prior executed actions newest first as four-wide Float32 one-hot vectors.
Both histories use zero padding. At reset, the returned buffer contains the
current observation and zero histories; that observation is then retained as
the first prior observation for the next decision.

## Proposed and executed actions

The policy proposal domain is the fixed set `{0,1,2,3}` over Boolean outputs in
the order `heaterState`, `acState`. Proposal 3 requests simultaneous heating
and cooling and is structurally dead, but the policy-facing proposal domain is
still retained: `SpecShield` transforms an invalid proposal into the unique
action required by the raw pending controller inputs.

History records the final executed action passed to `SysMLEnv.step`, never the
unshielded proposal. The shield reads raw pending model inputs while the policy
buffer contains normalized Float32 values. Therefore executed action remains a
function of source state, buffer, and proposal until a checked obligation proves
that the raw shield inputs are reconstructible from the controller buffer.

## Outcomes

The intrinsic formal outcomes are `Continue`, `Terminal`, and `Error`. Phase-2
runtime reward precedence is:

1. execution or requirement-evaluation error: reward `0.0`, error;
2. a false required property: reward `-1.0`, terminal violation;
3. intrinsic simulator completion: reward `1.0`, terminal success;
4. next decision: reward `-0.01`, continue.

The environment's `max_steps` result is wrapper truncation, not intrinsic MDP
termination. If it truncates a continuing step, it replaces the reward with
`0.0` and reports `TRUNCATED`. A proof must either exclude that wrapper from the
intrinsic theorem or include its hidden step count in the visible state.

The complete visible outcome also includes the executed action, next buffer for
a continuing result, elapsed ticks/time, ordered requirement events and errors,
and every required property result.

## Storage separation

The golden artifact assigns distinct typed identities to:

- physical environment temperature;
- thermometer-held temperature;
- sent mailbox payload;
- controller-received payload;
- controller command flags and heater/AC outputs;
- heater and AC machine modes;
- source and engine clocks;
- immutable scenario/controller parameters;
- transport presence; and
- the latched intrinsic-completion state.

Shared temperature provenance does not authorize equality among the physical,
held, sent, and received locations. Sampling, sending, and accepting are
separate copy events at separate positions in the source schedule.

## Validation boundary

The independent checker reparses the exact model, rebuilds the ordered execution
and decision-transition graph, reconstructs the environment, shield, and buffer
wrapper, and checks the declared interface facts and storage identities. It is
fail-closed under source hash, event-order, action-layout, buffer-layout, or
storage-identity changes.

Passing this check authorizes the next Phase-2 task: extracting these exact
objects into typed event SSA with an independent source-to-IR validator. It does
not authorize a Z3 `UNSAT` claim, a certificate, or pipeline integration.
