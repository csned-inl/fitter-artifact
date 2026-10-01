"""Deterministic SMT control-flow skeleton for one finite decision interval.

This module lowers only path selection through the already checked acyclic
``FiniteDecisionInterval``. Edge predicates remain symbolic until operation
guards and exception conditions are attached by transition lowering. The
encoding nevertheless proves that every model selects one finite path from the
resume point to exactly one first-outcome state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .markov_interval import ControlEdge, ControlState, FiniteDecisionInterval
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_expr import quoted_symbol


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def state_symbol(namespace: str, state: ControlState) -> str:
    return quoted_symbol(namespace, "control-state", _digest(state.key))


def edge_symbol(namespace: str, edge: ControlEdge) -> str:
    identity = "\0".join((edge.source.key, edge.label, edge.target.key))
    return quoted_symbol(namespace, "control-edge", _digest(identity))


def _or(symbols: tuple[str, ...]) -> str:
    if not symbols:
        return "false"
    if len(symbols) == 1:
        return symbols[0]
    return f"(or {' '.join(symbols)})"


def _at_most_one(symbols: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        f"(not (and {symbols[left]} {symbols[right]}))"
        for left in range(len(symbols))
        for right in range(left + 1, len(symbols))
    )


@dataclass(frozen=True)
class ControlRelationEncoding:
    namespace: str
    state_symbols: tuple[tuple[ControlState, str], ...]
    edge_symbols: tuple[tuple[ControlEdge, str], ...]
    exit_symbols: tuple[str, ...]
    assertions: tuple[str, ...]

    def smt2(self, *extra_assertions: str) -> str:
        declarations = tuple(
            f"(declare-const {symbol} Bool)"
            for _, symbol in (*self.state_symbols, *self.edge_symbols)
        )
        assertions = self.assertions + tuple(extra_assertions)
        return "\n".join(
            ("(set-logic QF_UF)", *declarations,
             *(f"(assert {item})" for item in assertions))
        ) + "\n"


def _compile_control_relation(
    interval: FiniteDecisionInterval,
    *,
    namespace: str,
    include_implied_exclusivity: bool,
) -> ControlRelationEncoding:
    """Encode a single active path through the finite interval, or fail closed."""

    if not namespace:
        raise ValueError("control-relation namespace is empty")
    states = interval.topological_order
    known = set(states)
    edges = tuple(sorted(interval.edges))
    if len(known) != len(states) or known != set(interval.states):
        raise UnsupportedLoweringError("control interval state coverage is malformed")
    if len(set(edges)) != len(edges):
        raise UnsupportedLoweringError("control interval contains duplicate edges")
    if interval.entry not in known or not set(interval.exits).issubset(known):
        raise UnsupportedLoweringError("control interval boundary is malformed")

    incoming: dict[ControlState, list[ControlEdge]] = {state: [] for state in states}
    outgoing: dict[ControlState, list[ControlEdge]] = {state: [] for state in states}
    position = {state: index for index, state in enumerate(states)}
    for edge in edges:
        if edge.source not in known or edge.target not in known:
            raise UnsupportedLoweringError("control edge leaves the finite interval")
        if position[edge.source] >= position[edge.target]:
            raise UnsupportedLoweringError("control edge violates checked DAG order")
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    exits = set(interval.exits)
    for state in states:
        if state == interval.entry and incoming[state]:
            raise UnsupportedLoweringError("control entry has an incoming edge")
        if state != interval.entry and not incoming[state]:
            raise UnsupportedLoweringError("non-entry control state is unreachable")
        if state in exits and outgoing[state]:
            raise UnsupportedLoweringError("first-outcome state has an outgoing edge")
        if state not in exits and not outgoing[state]:
            raise UnsupportedLoweringError("pre-outcome control state is a dead end")

    state_pairs = tuple((state, state_symbol(namespace, state)) for state in states)
    edge_pairs = tuple((edge, edge_symbol(namespace, edge)) for edge in edges)
    state_names = dict(state_pairs)
    edge_names = dict(edge_pairs)
    assertions: list[str] = [state_names[interval.entry]]

    for state in states:
        in_names = tuple(edge_names[edge] for edge in incoming[state])
        out_names = tuple(edge_names[edge] for edge in outgoing[state])
        if state != interval.entry:
            assertions.append(f"(= {state_names[state]} {_or(in_names)})")
            if include_implied_exclusivity:
                assertions.extend(_at_most_one(in_names))
        for edge_name in out_names:
            assertions.append(f"(=> {edge_name} {state_names[state]})")
        if state not in exits:
            assertions.append(f"(=> {state_names[state]} {_or(out_names)})")
            assertions.extend(_at_most_one(out_names))

    exit_names = tuple(state_names[state] for state in interval.exits)
    assertions.append(_or(exit_names))
    if include_implied_exclusivity:
        assertions.extend(_at_most_one(exit_names))
    return ControlRelationEncoding(
        namespace=namespace,
        state_symbols=state_pairs,
        edge_symbols=edge_pairs,
        exit_symbols=exit_names,
        assertions=tuple(assertions),
    )


def compile_control_relation(
    interval: FiniteDecisionInterval,
    *,
    namespace: str = "run",
) -> ControlRelationEncoding:
    """Encode the exact single path without implied merge exclusions.

    The interval is an acyclic graph with one active entry.  Every active
    non-exit state selects exactly one outgoing edge, and every non-entry state
    is active exactly when an incoming edge is active.  Induction over the DAG
    therefore gives one path and implies both incoming-edge and final-exit
    mutual exclusion.  Emitting those pairwise consequences would add
    quadratic clauses without changing the relation.
    """

    return _compile_control_relation(
        interval,
        namespace=namespace,
        include_implied_exclusivity=False,
    )


def _compile_pairwise_reference_control_relation(
    interval: FiniteDecisionInterval,
    *,
    namespace: str = "run",
) -> ControlRelationEncoding:
    """Retain the original redundant relation for translation validation."""

    return _compile_control_relation(
        interval,
        namespace=namespace,
        include_implied_exclusivity=True,
    )


def validate_control_relation(
    encoding: ControlRelationEncoding,
    interval: FiniteDecisionInterval,
) -> list[str]:
    """Reconstruct the compact relation and reject any missing constraint."""

    errors: list[str] = []
    try:
        expected = compile_control_relation(
            interval, namespace=encoding.namespace,
        )
    except Exception as exc:
        return [f"control relation cannot be reconstructed: {exc}"]
    if encoding.state_symbols != expected.state_symbols:
        errors.append("control state-symbol inventory mismatch")
    if encoding.edge_symbols != expected.edge_symbols:
        errors.append("control edge-symbol inventory mismatch")
    if encoding.exit_symbols != expected.exit_symbols:
        errors.append("control exit-symbol inventory mismatch")
    if encoding.assertions != expected.assertions:
        errors.append("compact control assertion inventory mismatch")
    return errors
