"""Finite first-outcome interval derivation for thermostat ``MarkovIR``.

The interval is a DAG of explicit control configurations ``(node, return
stack)``.  It stops at the first next decision, terminal, or execution error.
Any repeated configuration, unmatched return, dead end, or excessive state
budget is a failure rather than an assumed progress fact.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .markov_ir import MarkovIR, fingerprint


INTERVAL_SCHEMA = "clarity.markov-first-outcome-interval"
INTERVAL_VERSION = 1


@dataclass(frozen=True, order=True)
class ControlState:
    node_id: str
    return_stack: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.node_id + "|return=" + ">".join(self.return_stack)


@dataclass(frozen=True, order=True)
class ControlEdge:
    source: ControlState
    label: str
    target: ControlState


@dataclass(frozen=True)
class FiniteDecisionInterval:
    ir_sha256: str
    entry: ControlState
    exits: tuple[ControlState, ...]
    states: tuple[ControlState, ...]
    edges: tuple[ControlEdge, ...]
    topological_order: tuple[ControlState, ...]
    max_node_visits_to_first_outcome: int
    max_return_stack_depth: int
    first_outcome_structural: bool
    progress_status: str
    schema: str = INTERVAL_SCHEMA
    version: int = INTERVAL_VERSION

    def __post_init__(self) -> None:
        if self.schema != INTERVAL_SCHEMA or self.version != INTERVAL_VERSION:
            raise ValueError("unsupported finite-interval schema/version")
        if len(set(self.states)) != len(self.states):
            raise ValueError("duplicate control state")
        if set(self.topological_order) != set(self.states):
            raise ValueError("topological order does not cover the interval")
        known = set(self.states)
        if self.entry not in known or not set(self.exits).issubset(known):
            raise ValueError("interval boundary is outside its state set")
        if any(edge.source not in known or edge.target not in known for edge in self.edges):
            raise ValueError("interval edge leaves its state set")


class IntervalDerivationError(ValueError):
    pass


def _successors(state: ControlState, events, exit_nodes: set[str]):
    if state.node_id in exit_nodes:
        return ()
    event = events[state.node_id]
    successors = dict(event.successors)
    result: list[tuple[str, ControlState]] = []
    if event.operation == "return":
        if not state.return_stack:
            raise IntervalDerivationError(f"unmatched machine return: {state.node_id}")
        result.append(("return", ControlState(
            state.return_stack[-1], state.return_stack[:-1]
        )))
    elif event.operation == "call_machine":
        if set(successors) != {"call", "return"}:
            raise IntervalDerivationError(f"malformed call node: {state.node_id}")
        result.append(("call", ControlState(
            successors["call"], state.return_stack + (successors["return"],)
        )))
    else:
        result.extend((label, ControlState(target, state.return_stack))
                      for label, target in event.successors)
    if event.on_exception and event.on_exception not in exit_nodes:
        raise IntervalDerivationError(
            f"exception target is not a first outcome: {event.on_exception}"
        )
    if event.on_exception:
        result.append(("exception", ControlState(event.on_exception, state.return_stack)))
    # Preserve differently labelled edges: they are distinct rule outcomes.
    return tuple(result)


def derive_thermostat_decision_interval(
    ir: MarkovIR,
    *,
    max_control_states: int = 4096,
) -> FiniteDecisionInterval:
    events = {event.node_id: event for event in ir.events}
    entry = ControlState(ir.decision_boundary.resume_node)
    exit_nodes = set(ir.decision_boundary.exits)
    missing = ({entry.node_id} | exit_nodes) - set(events)
    if missing:
        raise IntervalDerivationError(f"missing interval nodes: {sorted(missing)}")

    queue = deque([entry])
    discovered = {entry}
    adjacency: dict[ControlState, tuple[tuple[str, ControlState], ...]] = {}
    edges: list[ControlEdge] = []
    while queue:
        state = queue.popleft()
        outgoing = _successors(state, events, exit_nodes)
        if state.node_id not in exit_nodes and not outgoing:
            raise IntervalDerivationError(f"dead end before first outcome: {state.key}")
        adjacency[state] = outgoing
        for label, target in outgoing:
            if target.node_id not in events:
                raise IntervalDerivationError(
                    f"unresolved interval edge: {state.node_id} -> {target.node_id}"
                )
            edges.append(ControlEdge(state, label, target))
            if target not in discovered:
                discovered.add(target)
                if len(discovered) > max_control_states:
                    raise IntervalDerivationError("finite interval control-state budget exceeded")
                queue.append(target)

    color: dict[ControlState, int] = {}
    postorder: list[ControlState] = []

    def visit(state: ControlState) -> None:
        color[state] = 1
        for _, target in adjacency[state]:
            if color.get(target) == 1:
                raise IntervalDerivationError(
                    f"unproved cycle before first outcome: {state.key} -> {target.key}"
                )
            if color.get(target, 0) == 0:
                visit(target)
        color[state] = 2
        postorder.append(state)

    visit(entry)
    distance: dict[ControlState, int] = {}
    for state in postorder:
        outgoing = adjacency[state]
        distance[state] = 1 if not outgoing else 1 + max(
            distance[target] for _, target in outgoing
        )
    exits = tuple(sorted(state for state in discovered if state.node_id in exit_nodes))
    if {state.node_id for state in exits} != exit_nodes:
        raise IntervalDerivationError("not every first-outcome constructor is reachable")
    if any(adjacency[state] for state in exits):
        raise IntervalDerivationError("first-outcome state has an outgoing edge")

    return FiniteDecisionInterval(
        ir_sha256=fingerprint(ir.to_dict(include_fingerprint=False)),
        entry=entry,
        exits=exits,
        states=tuple(sorted(discovered)),
        edges=tuple(sorted(edges)),
        topological_order=tuple(reversed(postorder)),
        max_node_visits_to_first_outcome=distance[entry],
        max_return_stack_depth=max(len(state.return_stack) for state in discovered),
        first_outcome_structural=True,
        progress_status="finite_acyclic_control_relation_checked",
    )


def interval_metrics(interval: FiniteDecisionInterval) -> dict[str, int | str | bool]:
    return {
        "control_states": len(interval.states),
        "control_edges": len(interval.edges),
        "source_nodes": len({state.node_id for state in interval.states}),
        "max_node_visits_to_first_outcome": interval.max_node_visits_to_first_outcome,
        "max_return_stack_depth": interval.max_return_stack_depth,
        "first_outcome_structural": interval.first_outcome_structural,
        "progress_status": interval.progress_status,
    }


def validate_thermostat_decision_interval(
    interval: FiniteDecisionInterval,
    ir: MarkovIR,
) -> list[str]:
    """Independently replay graph expansion and longest-path checks."""

    errors: list[str] = []
    events = {event.node_id: event for event in ir.events}
    entry = ControlState(ir.decision_boundary.resume_node)
    exits = set(ir.decision_boundary.exits)
    expected_states = {entry}
    pending = deque([entry])
    expected_edges: list[ControlEdge] = []
    adjacency: dict[ControlState, list[ControlState]] = {}
    while pending:
        state = pending.popleft()
        if state.node_id in exits:
            outgoing = []
        else:
            event = events.get(state.node_id)
            if event is None:
                errors.append(f"validator encountered missing node: {state.node_id}")
                continue
            successors = dict(event.successors)
            labelled: list[tuple[str, ControlState]] = []
            if event.operation == "return":
                if not state.return_stack:
                    errors.append(f"validator found unmatched return: {state.key}")
                else:
                    labelled.append(("return", ControlState(
                        state.return_stack[-1], state.return_stack[:-1]
                    )))
            elif event.operation == "call_machine":
                labelled.append(("call", ControlState(
                    successors["call"], state.return_stack + (successors["return"],)
                )))
            else:
                labelled.extend((label, ControlState(target, state.return_stack))
                                for label, target in event.successors)
            if event.on_exception:
                labelled.append(("exception", ControlState(
                    event.on_exception, state.return_stack
                )))
            outgoing = [target for _, target in labelled]
            expected_edges.extend(ControlEdge(state, label, target)
                                  for label, target in labelled)
        adjacency[state] = outgoing
        for target in outgoing:
            if target not in expected_states:
                expected_states.add(target); pending.append(target)

    if set(interval.states) != expected_states:
        errors.append("finite interval state coverage mismatch")
    if tuple(sorted(interval.edges)) != tuple(sorted(expected_edges)):
        errors.append("finite interval edge/order mismatch")
    expected_exit_states = tuple(sorted(state for state in expected_states
                                        if state.node_id in exits))
    if interval.entry != entry or interval.exits != expected_exit_states:
        errors.append("finite interval boundary mismatch")

    visiting: set[ControlState] = set()
    finished: set[ControlState] = set()
    post: list[ControlState] = []

    def dfs(state: ControlState) -> None:
        if state in visiting:
            errors.append("finite interval contains a pre-outcome cycle")
            return
        if state in finished:
            return
        visiting.add(state)
        for target in adjacency.get(state, []):
            dfs(target)
        visiting.remove(state); finished.add(state); post.append(state)

    dfs(entry)
    longest: dict[ControlState, int] = {}
    for state in post:
        outgoing = adjacency.get(state, [])
        longest[state] = 1 if not outgoing else 1 + max(longest[target] for target in outgoing)
    if longest.get(entry) != interval.max_node_visits_to_first_outcome:
        errors.append("finite interval bound mismatch")
    if max(map(lambda state: len(state.return_stack), expected_states)) != interval.max_return_stack_depth:
        errors.append("finite interval stack bound mismatch")
    if interval.ir_sha256 != fingerprint(ir.to_dict(include_fingerprint=False)):
        errors.append("finite interval IR identity mismatch")
    if not interval.first_outcome_structural or interval.progress_status != \
            "finite_acyclic_control_relation_checked":
        errors.append("finite interval progress/first-outcome status mismatch")
    return errors
