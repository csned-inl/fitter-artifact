# Sparse controller-visible Z3 interface

`clarity.certification.markov_z3_interface` attaches the thermostat controller
boundary to the checked sparse transition relation. It is a single-run semantic
component, not a paired Markov query or certificate.

## Exact boundary behavior

The component checks the complete 17-term theorem-visible inventory. Sixteen
terms receive solver expressions; `next_buffer_shift` remains a separately
replayed structural obligation. It then:

- equates pending raw shield inputs with the exact source storages at the
  decision boundary;
- initializes only the three ordered property status/error accumulators;
- preserves command-presence and machine-local state instead of resetting
  hidden state that can survive across decisions;
- selects first-outcome values from all nine checked exit configurations;
- computes normalized observations by binary64 division followed by RNE
  conversion to binary32;
- derives elapsed ticks from the `cycle/time` control selector and elapsed time
  from the exact binary64 engine-clock difference; and
- applies the checked error, violation, intrinsic-terminal, and continue reward
  priority.

The shield comparisons preserve the source AST's IEEE-754 evaluation order:

```text
cold := setPoint >= RNE(temperatureCelcius + tolerance)
hot  := setPoint <= RNE(temperatureCelcius - tolerance)
```

They must not be rearranged to put subtraction/addition on the other side of
the comparison. That real-number algebra is not exact for binary64. A regression
case demonstrates a concrete rounding disagreement with the old rearranged
form.

If `cold` and `hot` are both true, shield selection produces an explicit error
before simulator execution. The transition relation is therefore guarded by
shield success. Requiring a simulator path behind this error would constrain
source state that the actual error path never examines and could unsoundly
remove a counterexample. Executed action and property results carry explicit
availability conditions; next observations are available only on `Continue`.

## Anti-redundancy structure

The interface is a view over the existing sparse transition. It adds no
`declare-const` state cells and retains the transition's 638 declarations.
Identical exit values are grouped by their already checked control selectors,
and each selected storage is defined at most once. Shared nullary `define-fun`
terms name pure selections, shield predicates, outcome priority, reward,
duration, and observation conversion without introducing nondeterministic
state.

The complete guarded transition is emitted once as a conjunction rather than
copying it into each visible result. In the checked fixture the interface uses
25 shared definitions and is within 25 KiB of the transition kernel (currently
slightly smaller because repeated assertion wrappers disappear). Any missing
visible term, exit, exit storage, native-semantic rule, or duplicate definition
fails closed.

This is the standing redundancy policy in concrete form: share pure exact
terms and avoid repeated transport, but do not delete semantic behaviors. The
separately documented sparse finite-buffer view checks the exact layout,
reset padding, and one-step shift without copying this transition. Linear
predecessor windows reuse one transition per historical decision under a
conservative type-domain initial state. The production `ObligationManifest`
boundary remains closed until the exact reset anchor, paired-run,
reachability/invariant, progress, and exhaustive difference relations are
implemented and checked.
