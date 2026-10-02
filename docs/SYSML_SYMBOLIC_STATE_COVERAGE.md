THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Symbolic-state coverage and separation rules

Status: current thermostat prototype record, 2026-10-02.

## Non-collapse rule

A physical process value, sensor-held value, controller-visible reading, command,
and actuator state are different state components unless the pinned source and
approved semantic profile justify an equality at the exact decision epoch.
An equality is an explicit invariant or update equation; it is never created by
giving two source concepts the same symbolic variable.

This rule directly addresses the earlier mixing-machine failure in which true
physical values were collapsed with delayed sensor values.

## Proof visibility versus controller visibility

The proof object has three disjoint roles:

- **fixed process context** is a generic collection of named background and
  initial-condition fields shared by both compared histories of one MDP
  instance;
- **complete state** contains every evolving physical, sensor, actuator, delay,
  and phase value needed by the transition;
- **controller observation/buffer** contains only source-authorized visible
  fields.

The proof sees all three roles. Equality of controller buffers never implies
equality of hidden state or fixed parameters by omission: fixed parameters are
shared explicitly, while hidden state remains independently quantified.

`FixedProcessContext` is model-independent and may contain zero or more named
fields. A thermostat may declare outside temperature and thermal constants; a
cruise model may instead declare lead-speed or road-profile conditions. A field
belongs there only when the certified process contract keeps it fixed for the
whole trace and reset regime. If it can evolve or be independently selected on
reset, it belongs in complete state instead.

## Current thermostat coverage

| Source concept | Symbolic treatment | Current limitation |
| --- | --- | --- |
| `Environment.temperatureCelcius` | `physical_temperature` | Exact-real thermal equation |
| `TemperatureSensor.lastReadingCelcius`, controller `reading.temperatureCelcius`, and `lastObservedTemperature` | `sensor_temperature` | Distinct symbol, but the thermostat synchronous profile explicitly constrains it equal to physical temperature at each decision epoch |
| Controller setpoint | Persistent, exactly observed state | Reset-selectable within `[13,33]` |
| Outside temperature | First-class `FixedProcessContext["outside_temperature"]` shared by the paired histories | Fixed for the entire certified MDP instance, including resets; a varying outside temperature is not certified |
| Controller heater/AC flags | State reconstructed from the prior executed action | Assumes atomic command application |
| Physical actuator mode and heat output | Collapsed with controller flags by an explicit atomic-actuator contract | No actuator delay, queue, or independently evolving mode is supported |
| `currentTime` | Hidden state retained in the relation | Proved irrelevant to the selected result under this profile |
| `propagationDelay = 3` | Not represented | The pinned source does not read it; any source change is rejected by the source hash |
| Neural requirement and `SpecShield` | Proposed action is kept when live and requirement-satisfying; otherwise replaced by the unique required action | Exact-real profile, not a proof of binary64 equivalence for every boundary value |
| Prohibition and obligations | Parsed and retained as monitored predicates | They do not constrain the Markov transition in this prototype |
| Buffer | Current sensor observation, two prior sensor observations, and one prior executed action | Exact-real observation, not Float32 runtime encoding |

The source hash is pinned. Therefore a new assignment, delay use, sensor
transformation, queue, or changed connection is rejected rather than silently
interpreted using the thermostat relation.

## What is not yet supported

The current profile does not certify models with:

- delayed, sampled, filtered, quantized, noisy, or lossy sensors;
- independently evolving physical and sensor values without an explicit update;
- message queues, nondeterministic scheduling, or transport delay;
- physical actuator state that can differ from controller flags;
- scenario inputs that vary inside the certified MDP while remaining hidden;
- floating-point behavior in place of the declared exact-real arithmetic.

Encountering any of these is `UNSUPPORTED` for this prototype. It must not be
handled by equating, omitting, or reconstructing the extra state without proof.

## Mixing Machine implementation of the delayed-sensor rule

`MIXING_SYMBOLIC_MACHINE_CONTRACT.md` now instantiates the required separation:
physical feeder levels and controller-held sampled levels are different symbols,
and the source `2 ml` tolerance is an explicit subtraction. The selected
controller decision boundary is the `Policy` call after a synchronous scan, so
the profile proves the boundary invariant `sampled = physical - 2` without ever
identifying the two variables. The held value between scan events remains real
state; a policy invocation between scans or delayed/asynchronous delivery is
outside this profile and must add phase or queue state.

## General delayed-sensor extension

A mixing-machine or other delayed-sensor profile must add, at minimum:

1. the true physical value `P`;
2. the sensor-held or controller-visible value `Y`;
3. any delay memory, queue, timestamp, sample phase, or pending measurement `Q`;
4. separate plant update `P' = F(P, actuator, environment)`;
5. separate sensor update `(Y', Q') = H(P, Y, Q, phase)`;
6. an observation function that exposes `Y`, never `P` unless the source says so.

The paired Markov query must allow `P` and `Q` to differ between histories with
the same controller buffer. If that produces different next results, Z3 must
return `SAT`; the buffer is then insufficient. A stronger buffer may only be
certified after its observation/history actually reconstructs every relevant
latent component.

This extension still compiles equations and finite memory directly. It does not
authorize encoding or symbolically executing a simulator control graph.
