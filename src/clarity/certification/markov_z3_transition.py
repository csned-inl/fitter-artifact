"""Compose finite control flow with event-local typed SSA relations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .markov_interval import ControlEdge, ControlState, FiniteDecisionInterval
from .markov_ir import MarkovIR
from .markov_slice import TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_control import compile_control_relation
from .markov_z3_event import (
    ScalarEventEncoding, compile_identity_event, compile_scalar_event,
)
from .markov_z3_expr import quoted_symbol


def _and(*items: str) -> str:
    if not items:
        return "true"
    if len(items) == 1:
        return items[0]
    return f"(and {' '.join(items)})"


def _or(*items: str) -> str:
    if not items:
        return "false"
    if len(items) == 1:
        return items[0]
    return f"(or {' '.join(items)})"


@dataclass(frozen=True)
class TransitionRelationEncoding:
    namespace: str
    control_state_count: int
    control_edge_count: int
    state_term_count: int
    boundary_entry_terms: tuple[tuple[str, str], ...]
    exit_state_terms: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    control_state_symbols: tuple[tuple[ControlState, str], ...]
    control_edge_symbols: tuple[tuple[ControlEdge, str], ...]
    sort_declarations: tuple[str, ...]
    declarations: tuple[str, ...]
    control_assertions: tuple[str, ...]
    event_assertions: tuple[str, ...]
    edge_assertions: tuple[str, ...]
    bridge_assertions: tuple[str, ...]

    @property
    def assertions(self) -> tuple[str, ...]:
        return (self.control_assertions + self.event_assertions
                + self.edge_assertions + self.bridge_assertions)

    def smt2(self, *extra_assertions: str) -> str:
        return "\n".join((
            *self.sort_declarations,
            *self.declarations,
            *(f"(assert {formula})"
              for formula in self.assertions + tuple(extra_assertions)),
        )) + "\n"


def _substitute_symbols(formula: str, replacements: dict[str, str]) -> str:
    """Substitute complete quoted SMT symbols without changing operators."""

    result = formula
    for source in sorted(replacements, key=len, reverse=True):
        result = result.replace(source, replacements[source])
    return result


def _state_token(state: ControlState) -> str:
    return hashlib.sha256(state.key.encode()).hexdigest()


def _validate_event_equations(
    encoding: ScalarEventEncoding,
    writes: set[str],
    storage_uids: tuple[str, ...],
) -> None:
    """Prove the local complete-frame relation is safe to sparsify."""

    entry = {
        uid: quoted_symbol(encoding.namespace, "entry", uid)
        for uid in storage_uids
    }
    exit_ = {
        uid: quoted_symbol(encoding.namespace, "exit", uid)
        for uid in storage_uids
    }
    expected_frames = {
        f"(= {exit_[uid]} {entry[uid]})" for uid in set(storage_uids) - writes
    }
    if set(encoding.frames) != expected_frames \
            or len(encoding.frames) != len(expected_frames):
        raise UnsupportedLoweringError(
            "event frames are not an exact complement of retained writes"
        )

    update_targets: list[str] = []
    for formula in encoding.updates:
        matches = [
            uid for uid in writes if formula.startswith(f"(= {exit_[uid]} ")
        ]
        if len(matches) != 1:
            raise UnsupportedLoweringError(
                "event update does not define exactly one retained write"
            )
        update_targets.append(matches[0])
    if set(update_targets) != writes or len(update_targets) != len(writes):
        raise UnsupportedLoweringError(
            "event updates do not define every retained write exactly once"
        )


def compile_transition_relation(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    *,
    namespace: str = "run",
) -> TransitionRelationEncoding:
    """Compose one exact typed state vector along the selected finite path.

    Event-local lowering remains complete-frame SSA.  Composition validates
    that complete relation, then removes identities and carries unchanged
    values symbolically.  New SMT cells are allocated only for boundary
    inputs, actual writes, and control-flow phi merges.  This sparse form is
    equisatisfiable with the complete-frame relation but avoids declaring and
    bridging every retained storage at every inactive control configuration.
    """

    if not namespace:
        raise ValueError("transition namespace is empty")
    source = {event.node_id: event for event in ir.events}
    sliced = {event.node_id: event for event in slice_.events}
    interval_nodes = {state.node_id for state in interval.states}
    if not interval_nodes.issubset(source):
        raise UnsupportedLoweringError("interval references a missing source event")

    control = compile_control_relation(interval, namespace=namespace + "::flow")
    control_states = dict(control.state_symbols)
    control_edges = dict(control.edge_symbols)
    outgoing: dict[ControlState, list[ControlEdge]] = {
        state: [] for state in interval.states
    }
    for edge in interval.edges:
        outgoing[edge.source].append(edge)

    instances: dict[ControlState, ScalarEventEncoding] = {}
    for state in interval.topological_order:
        event = source[state.node_id]
        state_namespace = (
            namespace + "::instance::" + hashlib.sha256(state.key.encode()).hexdigest()
        )
        if state.node_id in sliced:
            instances[state] = compile_scalar_event(
                slice_, event, sliced[state.node_id], namespace=state_namespace
            )
        else:
            instances[state] = compile_identity_event(
                slice_, state.node_id, event.operation, namespace=state_namespace
            )

    sort_declarations = tuple(dict.fromkeys(
        declaration for encoding in instances.values()
        for declaration in encoding.sort_declarations
    ))
    declarations = [
        *(f"(declare-const {symbol} Bool)"
          for _, symbol in (*control.state_symbols, *control.edge_symbols)),
    ]

    event_assertions: list[str] = []
    edge_assertions: list[str] = []
    bridge_assertions: list[str] = []
    storage_uids = tuple(sorted(storage.identity.uid for storage in slice_.storages))
    incoming: dict[ControlState, list[ControlEdge]] = {
        state: [] for state in interval.states
    }
    for edge in interval.edges:
        incoming[edge.target].append(edge)

    storage_sorts: dict[str, str] = {}
    event_writes: dict[ControlState, set[str]] = {}
    for state in interval.topological_order:
        encoding = instances[state]
        writes = set(sliced[state.node_id].writes) if state.node_id in sliced else set()
        _validate_event_equations(encoding, writes, storage_uids)
        event_writes[state] = writes
        local_declarations = dict(encoding.declarations)
        for uid in storage_uids:
            symbol = quoted_symbol(encoding.namespace, "entry", uid)
            sort = local_declarations.get(symbol)
            if sort is None:
                raise UnsupportedLoweringError("event entry storage is undeclared")
            previous = storage_sorts.setdefault(uid, sort)
            if previous != sort:
                raise UnsupportedLoweringError("retained storage sort changed across events")

    boundary_entry_terms = tuple(
        (uid, quoted_symbol(namespace, "boundary-entry", uid))
        for uid in storage_uids
    )
    declarations.extend(
        f"(declare-const {symbol} {storage_sorts[uid]})"
        for uid, symbol in boundary_entry_terms
    )
    boundary_values = dict(boundary_entry_terms)
    state_entry_values: dict[ControlState, dict[str, str]] = {}
    state_exit_values: dict[ControlState, dict[str, str]] = {}

    for state in interval.topological_order:
        encoding = instances[state]
        active = control_states[state]
        if state == interval.entry:
            entry_values = dict(boundary_values)
        else:
            entry_values = {}
            for uid in storage_uids:
                predecessors = incoming[state]
                predecessor_values = [
                    (state_entry_values[edge.source][uid]
                     if edge.label in {"exception", "blocked"}
                     else state_exit_values[edge.source][uid])
                    for edge in predecessors
                ]
                if not predecessor_values:
                    raise UnsupportedLoweringError(
                        "non-entry control state lacks a predecessor value"
                    )
                if len(set(predecessor_values)) == 1:
                    entry_values[uid] = predecessor_values[0]
                    continue
                phi = quoted_symbol(namespace, "state-phi", _state_token(state), uid)
                declarations.append(
                    f"(declare-const {phi} {storage_sorts[uid]})"
                )
                entry_values[uid] = phi
                by_value: dict[str, list[str]] = {}
                for edge, value in zip(predecessors, predecessor_values):
                    by_value.setdefault(value, []).append(control_edges[edge])
                bridge_assertions.extend(
                    f"(=> {_or(*selectors)} (= {phi} {value}))"
                    for value, selectors in by_value.items()
                )
        state_entry_values[state] = entry_values

        writes = event_writes[state]
        exit_values = dict(entry_values)
        for uid in sorted(writes):
            symbol = quoted_symbol(
                namespace, "state-write", _state_token(state), uid
            )
            declarations.append(
                f"(declare-const {symbol} {storage_sorts[uid]})"
            )
            exit_values[uid] = symbol
        state_exit_values[state] = exit_values

        replacements = {
            quoted_symbol(encoding.namespace, "entry", uid): entry_values[uid]
            for uid in storage_uids
        }
        replacements.update({
            quoted_symbol(encoding.namespace, "exit", uid): exit_values[uid]
            for uid in storage_uids
        })

        exception = _substitute_symbols(
            encoding.exception_condition.text, replacements
        )
        normal = _and(active, f"(not {exception})")
        execution = normal
        if encoding.precondition is not None:
            execution = _and(normal, _substitute_symbols(
                encoding.precondition.text, replacements
            ))
        event_assertions.extend(
            f"(=> {execution} {_substitute_symbols(formula, replacements)})"
            for formula in encoding.updates
        )

        edges = outgoing[state]
        if not edges:
            continue
        by_label = {edge.label: edge for edge in edges}
        if len(by_label) != len(edges) or "exception" not in by_label:
            raise UnsupportedLoweringError("event edges lack unique exception label")
        edge_assertions.append(
            f"(= {control_edges[by_label['exception']]} {_and(active, exception)})"
        )
        operation = encoding.operation
        if operation in {"branch", "completion_test", "machine_from_state"}:
            if encoding.branch_condition is None or not {"true", "false"}.issubset(by_label):
                raise UnsupportedLoweringError("Boolean branch labels are malformed")
            guard = _substitute_symbols(
                encoding.branch_condition.text, replacements
            )
            edge_assertions.extend((
                f"(= {control_edges[by_label['true']]} {_and(normal, guard)})",
                f"(= {control_edges[by_label['false']]} {_and(normal, f'(not {guard})')})",
            ))
        elif operation == "match_trigger":
            if encoding.branch_condition is None or not {"matched", "absent"}.issubset(by_label):
                raise UnsupportedLoweringError("trigger-match labels are malformed")
            guard = _substitute_symbols(
                encoding.branch_condition.text, replacements
            )
            edge_assertions.extend((
                f"(= {control_edges[by_label['matched']]} {_and(normal, guard)})",
                f"(= {control_edges[by_label['absent']]} {_and(normal, f'(not {guard})')})",
            ))
        elif operation == "accept_copy":
            if encoding.precondition is None or not {"accepted", "blocked"}.issubset(by_label):
                raise UnsupportedLoweringError("accept-copy labels are malformed")
            present = _substitute_symbols(
                encoding.precondition.text, replacements
            )
            edge_assertions.extend((
                f"(= {control_edges[by_label['accepted']]} {_and(normal, present)})",
                f"(= {control_edges[by_label['blocked']]} {_and(normal, f'(not {present})')})",
            ))
        elif operation == "send_copy":
            if not {"sent", "absent"}.issubset(by_label):
                raise UnsupportedLoweringError("send-copy labels are malformed")
            edge_assertions.extend((
                f"(= {control_edges[by_label['sent']]} {normal})",
                f"(not {control_edges[by_label['absent']]})",
            ))
        else:
            ordinary = [edge for edge in edges if edge.label != "exception"]
            if len(ordinary) != 1:
                raise UnsupportedLoweringError(
                    f"operation has ambiguous normal edges: {operation}"
                )
            edge_assertions.append(
                f"(= {control_edges[ordinary[0]]} {normal})"
            )

    if len(declarations) != len(set(declarations)):
        raise UnsupportedLoweringError("composed relation has duplicate declarations")

    exit_state_terms = tuple(
        (state.key, tuple((uid, state_entry_values[state][uid])
                          for uid in storage_uids))
        for state in interval.exits
    )

    return TransitionRelationEncoding(
        namespace=namespace,
        control_state_count=len(interval.states),
        control_edge_count=len(interval.edges),
        state_term_count=len(storage_uids),
        boundary_entry_terms=boundary_entry_terms,
        exit_state_terms=exit_state_terms,
        control_state_symbols=control.state_symbols,
        control_edge_symbols=control.edge_symbols,
        sort_declarations=sort_declarations,
        declarations=tuple(declarations),
        control_assertions=control.assertions,
        event_assertions=tuple(event_assertions),
        edge_assertions=tuple(edge_assertions),
        bridge_assertions=tuple(bridge_assertions),
    )
