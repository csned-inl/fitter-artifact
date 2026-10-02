THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Mixing Machine direct symbolic contract

Status: focused prototype retaining the fixed `(b_obs=2, b_act=1)` regression
query and analytically proposing `(b_obs=0, b_act=0)` for independent Z3
certification.

## Authoritative source

- Repository: `csned-inl/clarity-standalone`
- Commit: `aa3c6ae4640e3bcadcf66542eb0124e6c57e2bb3`
- Path: `sysml-models/mixing-sysml-model/model.sysml`
- Git blob: `20ef2ed6dd55ab083ce8b1a403beab17c58dbdff`
- SHA-256: `09d59723435da435325299934ed8472bbac3e89c6d9840df892b9f7e1c912f8f`

The local file is a byte-identical proof input. Historical Mixing certificates,
SMV exports, simulator traces, and similarly named files elsewhere are not
semantic authority.

## Exact model boundary

The controller-facing decision boundary is the `Policy` invocation inside
`ScanCycle`. The scan has already synchronously read each feeder tank and
stored:

```text
sampled_level_i = physical_level_i - toleranceMl
```

The two values remain distinct symbols. `observedLevel1/2` are held controller
state between scan events; they are not globally interchangeable with physical
tank levels. Under this profile, no controller decision occurs between scans,
so at every selected Policy boundary the explicit sample relation above holds.
Changing the implementation to invoke the policy between scans, delay Modbus
delivery, queue readings, or sample asynchronously invalidates this profile.

The standalone source declares `toleranceMl : Integer = 2`. Its nearby comment
says “5Ml interval,” but executable value `2` and the subtraction equations are
the formal semantics used by the proof. No SMV-derived tolerance is used.

## State roles

| Role | Fields |
| --- | --- |
| Fixed process context | transfer targets, original feeder levels, tolerance, scan frequency, timestep, flow rates, rewards |
| Complete evolving state | two physical levels, two held sampled levels, pump states, valve states |
| Controller observation | sampled levels, targets, original levels |
| Buffer | current observation, two prior observations, one prior executed action |

Targets and original levels are both fixed for one MDP instance and visible to
the controller. Tolerance is proof-visible but not a Policy input.

## Direct equations

The exact-real/integer profile has one scan period between post-warmup Policy
decisions. A valid executed action drains feeder `i` by
`maxFlowRate_i * dt = 10 * 1/10 = 1 ml` when both its pump and valve are on,
and by zero otherwise. The next sample is the next physical level minus `2`.

The source neural requirement uniquely selects on/off for each feeder from the
held sampled level. The symbolic shield retains the explicit CLARITY
keep-or-replace branch even though every proposal ultimately executes that
unique required action.

## Properties and outcome

The relation retains all four source properties: No Dry Running, No Dead
Heading, Fluid Transfer Termination Safety, and Fluid Transfer Liveness.
Commands are applied and properties are checked before completion becomes
terminal success. Running transitions return the next buffer; terminal success
or a property violation does not invent a continuing successor.

## Deliberate exclusions

This theorem does not cover binary floating-point normalization, asynchronous
or delayed message delivery, decisions between scan events, Modbus errors,
partial actuator writes, external truncation, or a changed source. Any such
case is `UNSUPPORTED`, not silently approximated.
