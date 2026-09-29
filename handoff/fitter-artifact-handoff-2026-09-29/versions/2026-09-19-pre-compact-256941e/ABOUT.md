# Controller Simplification Sequence

The cruise controller illustrates the relationship between the five stages.
See `README.md` for setup and execution and
`DISCRETIZATION_CERTIFICATION_DESIGN.md` for the Stage 4 method.

Its `#NeuralRequirement` applies throttle when the target speed exceeds the
current speed by more than the tolerance and the following gap is safe. It
applies the brake when the current speed exceeds the target by more than the
tolerance or the gap is unsafe. Otherwise both outputs are false and the
vehicle coasts, while the contract prohibits simultaneous throttle and brake.
Direct extraction evaluates these rules without learning, and the memoryless
check establishes that the current inputs determine the immediate controller
constraint without prior observations or actions.

That immediate result does not make the complete process Markov. The stronger
check proves that one prior observation and two prior executed actions
reconstruct enough modeled state to determine the next sampled step. It writes
the certificate and reduced MDP specification used by the later stages.

The sensor equations connect the readings to physical speed and following gap,
and the actuator equations map the Boolean outputs to the action held over the
next physical interval. Discretization certification combines these equations,
the controller contract, and the fixed `dt` to check the system requirements
throughout that interval. After certification, a small feedforward architecture
derived from the requirement structure is trained from the reduced MDP
specification and executed with the shield extracted from the original
controller contract.
