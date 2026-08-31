# About the Checker Sequence

The artifact applies the same five stages to the thermostat, chemical mixing
plant, and discrete cruise controller.

For the cruise controller, the current neural inputs determine the immediate
controller constraint. The stronger Markov process check proves that one prior
observation and two prior actions reconstruct enough modeled state for the
next step. The discretization stage then uses the full SysML physical model,
including the quadratic drag equation, to certify all five safety properties
throughout each interval between controller updates.

The cruise model creates substantially more incidental logical structure than
the other two models. Lazy constraint construction keeps the obligation
factored and asks only for proof branches required by the selected checker.
Shared reachable regions and proof subtrees are computed once and referenced
by content hash. These mechanisms are model independent and are applied
unchanged to all three inputs.
