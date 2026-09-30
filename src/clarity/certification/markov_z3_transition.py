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


@dataclass(frozen=True)
class TransitionRelationEncoding:
    namespace: str
    control_state_count: int
    control_edge_count: int
    state_term_count: int
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


def compile_transition_relation(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    *,
    namespace: str = "run",
) -> TransitionRelationEncoding:
    """Compose one exact typed state vector along the selected finite path."""

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
        *(f"(declare-const {symbol} {sort})"
          for encoding in instances.values()
          for symbol, sort in encoding.declarations),
    ]
    if len(declarations) != len(set(declarations)):
        raise UnsupportedLoweringError("composed relation has duplicate declarations")

    event_assertions: list[str] = []
    edge_assertions: list[str] = []
    bridge_assertions: list[str] = []
    storage_uids = tuple(sorted(storage.identity.uid for storage in slice_.storages))

    for state in interval.topological_order:
        encoding = instances[state]
        active = control_states[state]
        exception = encoding.exception_condition.text
        normal = _and(active, f"(not {exception})")
        execution = normal
        if encoding.precondition is not None:
            execution = _and(normal, encoding.precondition.text)
        event_assertions.extend(
            f"(=> {execution} {formula})"
            for formula in encoding.updates + encoding.frames
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
            guard = encoding.branch_condition.text
            edge_assertions.extend((
                f"(= {control_edges[by_label['true']]} {_and(normal, guard)})",
                f"(= {control_edges[by_label['false']]} {_and(normal, f'(not {guard})')})",
            ))
        elif operation == "match_trigger":
            if encoding.branch_condition is None or not {"matched", "absent"}.issubset(by_label):
                raise UnsupportedLoweringError("trigger-match labels are malformed")
            guard = encoding.branch_condition.text
            edge_assertions.extend((
                f"(= {control_edges[by_label['matched']]} {_and(normal, guard)})",
                f"(= {control_edges[by_label['absent']]} {_and(normal, f'(not {guard})')})",
            ))
        elif operation == "accept_copy":
            if encoding.precondition is None or not {"accepted", "blocked"}.issubset(by_label):
                raise UnsupportedLoweringError("accept-copy labels are malformed")
            present = encoding.precondition.text
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

        source_entry = {
            uid: quoted_symbol(encoding.namespace, "entry", uid)
            for uid in storage_uids
        }
        source_exit = {
            uid: quoted_symbol(encoding.namespace, "exit", uid)
            for uid in storage_uids
        }
        del source_entry  # retained in symbol construction symmetry and review
        for edge in edges:
            target_encoding = instances[edge.target]
            selected = control_edges[edge]
            for uid in storage_uids:
                target_entry = quoted_symbol(target_encoding.namespace, "entry", uid)
                bridge_assertions.append(
                    f"(=> {selected} (= {target_entry} {source_exit[uid]}))"
                )

    return TransitionRelationEncoding(
        namespace=namespace,
        control_state_count=len(interval.states),
        control_edge_count=len(interval.edges),
        state_term_count=len(storage_uids),
        sort_declarations=sort_declarations,
        declarations=tuple(declarations),
        control_assertions=control.assertions,
        event_assertions=tuple(event_assertions),
        edge_assertions=tuple(edge_assertions),
        bridge_assertions=tuple(bridge_assertions),
    )
