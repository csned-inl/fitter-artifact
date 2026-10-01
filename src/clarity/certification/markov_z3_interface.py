"""Exact, sparse controller-visible interface over one thermostat transition.

This layer adds no duplicate transition state.  It initializes only the
per-decision requirement accumulators, relates the pending raw shield inputs
to their source storages, and exposes controller-visible results as shared SMT
expressions over the existing sparse transition relation.

Shield failure is handled before the simulator interval.  Consequently the
transition relation is required only when shield selection succeeds; forcing a
post-shield transition on that error path would under-approximate errors and
could make a later UNSAT result unsound.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Mapping

from .markov_interval import FiniteDecisionInterval
from .markov_ir import MarkovIR, NativeSort
from .markov_native_semantics import (
    load_native_semantics_contract, validate_thermostat_native_semantics,
)
from .markov_slice import TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_event import (
    enum_constructor, enum_sort, enum_sort_declarations,
)
from .markov_z3_expr import fp_literal, quoted_symbol, smt_sort
from .markov_z3_transition import TransitionRelationEncoding


STRUCTURAL_VISIBLE_TERMS = ("next_buffer_shift",)


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


def _ite(condition: str, when_true: str, when_false: str) -> str:
    return f"(ite {condition} {when_true} {when_false})"


@dataclass(frozen=True)
class VisibleTermEncoding:
    name: str
    text: str
    native_sort: NativeSort
    declared_type: str | None
    availability: str


@dataclass(frozen=True)
class ThermostatInterfaceEncoding:
    namespace: str
    sort_declarations: tuple[str, ...]
    declarations: tuple[str, ...]
    definitions: tuple[str, ...]
    boundary_assertions: tuple[str, ...]
    guarded_transition: str
    visible_terms: tuple[VisibleTermEncoding, ...]
    structural_terms: tuple[str, ...]
    shield_error: str
    source_error: str
    requirement_check_active: str
    transition_assertion_count: int

    @property
    def assertions(self) -> tuple[str, ...]:
        return self.boundary_assertions + (self.guarded_transition,)

    def visible(self, name: str) -> VisibleTermEncoding:
        matches = [term for term in self.visible_terms if term.name == name]
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]

    def smt2(self, *extra_assertions: str) -> str:
        return "\n".join((
            *self.sort_declarations,
            *self.declarations,
            *self.definitions,
            *(f"(assert {formula})"
              for formula in self.assertions + tuple(extra_assertions)),
        )) + "\n"


def _slug(name: str) -> str:
    return name.replace(" ", "_").lower()


def _expected_visible_signature(
    ir: MarkovIR,
) -> tuple[tuple[str, NativeSort, str | None], ...]:
    terms: list[tuple[str, NativeSort, str | None]] = [
        ("executed_action", NativeSort.ENUM, "ExecutedAction"),
        ("outcome_constructor", NativeSort.ENUM, "OutcomeConstructor"),
        ("next_observation.setPoint.float32_bits", NativeSort.FLOAT32, None),
        ("next_observation.temperatureCelcius.float32_bits", NativeSort.FLOAT32, None),
        ("next_buffer_shift", NativeSort.ENUM, None),
        ("reward", NativeSort.FLOAT64, None),
        ("elapsed_ticks", NativeSort.INT, None),
        ("elapsed_time", NativeSort.FLOAT64, None),
        ("intrinsic_termination", NativeSort.BOOL, None),
        ("execution_error", NativeSort.ENUM, "ExecutionError"),
        ("action_availability_mask", NativeSort.INT, None),
    ]
    for name in ir.required_properties:
        terms.extend((
            ("property_status:" + name, NativeSort.ENUM, "PropertyStatus"),
            ("property_error:" + name, NativeSort.ENUM, "PropertyError"),
        ))
    return tuple(terms)


def _validate_inputs(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    transition: TransitionRelationEncoding,
) -> None:
    expected_signature = tuple(
        (name, sort) for name, sort, _ in _expected_visible_signature(ir)
    )
    actual_signature = tuple(
        (term.name, term.native_sort) for term in slice_.visible_terms
    )
    if actual_signature != expected_signature:
        raise UnsupportedLoweringError(
            "controller-visible signature does not match the checked interface"
        )

    storage = {item.identity.uid: item.identity for item in slice_.storages}
    if len(storage) != len(slice_.storages):
        raise UnsupportedLoweringError("interface storage inventory has duplicates")
    boundary = dict(transition.boundary_entry_terms)
    expected_uids = set(storage)
    if len(boundary) != len(transition.boundary_entry_terms) \
            or set(boundary) != expected_uids:
        raise UnsupportedLoweringError("transition boundary storage coverage mismatch")
    for uid, symbol in boundary.items():
        expected = quoted_symbol(transition.namespace, "boundary-entry", uid)
        if symbol != expected:
            raise UnsupportedLoweringError("transition boundary symbol mismatch")

    if transition.state_term_count != len(expected_uids) \
            or transition.control_state_count != len(interval.states) \
            or transition.control_edge_count != len(interval.edges):
        raise UnsupportedLoweringError("transition/interface metric mismatch")
    state_symbols = dict(transition.control_state_symbols)
    if len(state_symbols) != len(transition.control_state_symbols) \
            or set(state_symbols) != set(interval.states):
        raise UnsupportedLoweringError("transition control-state coverage mismatch")
    node_counts = Counter(state.node_id for state in interval.states)
    required_node_counts = {
        ir.decision_boundary.request_node: 1,
        "terminal": 1,
        "execution_error": 7,
        "cycle/time": 1,
        "cycle/check": 1,
    }
    if any(node_counts[node_id] != count
           for node_id, count in required_node_counts.items()):
        raise UnsupportedLoweringError(
            "controller-interface control-selector inventory mismatch"
        )

    exit_rows = dict(transition.exit_state_terms)
    expected_exit_keys = {state.key for state in interval.exits}
    if len(exit_rows) != len(transition.exit_state_terms) \
            or set(exit_rows) != expected_exit_keys:
        raise UnsupportedLoweringError("transition first-outcome coverage mismatch")
    for terms in exit_rows.values():
        values = dict(terms)
        if len(values) != len(terms) or set(values) != expected_uids:
            raise UnsupportedLoweringError("transition exit storage coverage mismatch")

    expected_storage_types = {
        "semantic:engine_time": (NativeSort.FLOAT64, "Real"),
        "semantic:latched_completion": (NativeSort.BOOL, "Boolean"),
        "semantic:received_temperature_payload": (NativeSort.FLOAT64, "Real"),
        "semantic:set_point": (NativeSort.FLOAT64, "Real"),
        "semantic:tolerance": (NativeSort.FLOAT64, "Real"),
        "slice:executed_action": (NativeSort.ENUM, "ExecutedAction"),
        "slice:policy_proposal": (NativeSort.ENUM, "PolicyProposal"),
        "slice:shield:done": (NativeSort.BOOL, "Boolean"),
        "slice:shield:setPoint": (NativeSort.FLOAT64, "Real"),
        "slice:shield:temperatureCelcius": (NativeSort.FLOAT64, "Real"),
    }
    for name in ir.required_properties:
        slug = _slug(name)
        expected_storage_types.update({
            f"slice:property:{slug}:status": (NativeSort.ENUM, "PropertyStatus"),
            f"slice:property:{slug}:error": (NativeSort.ENUM, "PropertyError"),
        })
    for uid, (native_sort, declared_type) in expected_storage_types.items():
        identity = storage.get(uid)
        if identity is None or (identity.native_sort, identity.declared_type) != (
                native_sort, declared_type):
            raise UnsupportedLoweringError(
                f"controller-interface storage identity mismatch: {uid}"
            )


def compile_thermostat_interface(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    transition: TransitionRelationEncoding,
    *,
    namespace: str = "interface",
    native_contract: Mapping | None = None,
) -> ThermostatInterfaceEncoding:
    """Attach exact thermostat-visible terms to an existing sparse relation."""

    if not namespace:
        raise ValueError("interface namespace is empty")
    selected_contract = (
        load_native_semantics_contract()
        if native_contract is None else dict(native_contract)
    )
    semantic_errors = validate_thermostat_native_semantics(selected_contract)
    if semantic_errors:
        raise UnsupportedLoweringError(
            "invalid native-semantics contract: " + "; ".join(semantic_errors)
        )
    _validate_inputs(slice_, ir, interval, transition)

    scale = selected_contract["observation"]["scale"]
    if type(scale) not in {int, float} or not math.isfinite(float(scale)) \
            or float(scale) == 0.0:
        raise UnsupportedLoweringError("observation scale is not finite and nonzero")

    boundary = dict(transition.boundary_entry_terms)
    storage = {item.identity.uid: item.identity for item in slice_.storages}
    control_by_state = dict(transition.control_state_symbols)
    control_by_key = {state.key: symbol for state, symbol in control_by_state.items()}
    exit_values = {
        key: dict(terms) for key, terms in transition.exit_state_terms
    }

    definitions: list[str] = []
    definition_names: set[str] = set()

    def define(label: str, sort: str, expression: str) -> str:
        symbol = quoted_symbol(namespace, "definition", label)
        if symbol in definition_names:
            raise UnsupportedLoweringError(
                f"duplicate controller-interface definition: {label}"
            )
        definition_names.add(symbol)
        definitions.append(f"(define-fun {symbol} () {sort} {expression})")
        return symbol

    def storage_sort(uid: str) -> str:
        identity = storage[uid]
        if identity.native_sort is NativeSort.ENUM:
            return enum_sort(identity.declared_type)
        return smt_sort(identity.native_sort)

    def node_active(node_id: str) -> str:
        return _or(*(symbol for state, symbol in transition.control_state_symbols
                     if state.node_id == node_id))

    selected_cache: dict[str, str] = {}

    def selected(uid: str) -> str:
        cached = selected_cache.get(uid)
        if cached is not None:
            return cached
        groups: dict[str, list[str]] = {}
        for state in interval.exits:
            value = exit_values[state.key][uid]
            groups.setdefault(value, []).append(control_by_key[state.key])
        rows = [(_or(*selectors), value) for value, selectors in groups.items()]
        result = rows[-1][1]
        for selector, value in reversed(rows[:-1]):
            result = _ite(selector, value, result)
        if len(rows) > 1:
            result = define("selected::" + uid, storage_sort(uid), result)
        selected_cache[uid] = result
        return result

    raw_set_point = boundary["slice:shield:setPoint"]
    raw_temperature = boundary["slice:shield:temperatureCelcius"]
    raw_done = boundary["slice:shield:done"]
    set_point = boundary["semantic:set_point"]
    temperature = boundary["semantic:received_temperature_payload"]
    tolerance = boundary["semantic:tolerance"]
    executed_action = boundary["slice:executed_action"]

    # These are the exact source AST orders.  Do not algebraically move the
    # tolerance across an IEEE-754 comparison.
    cold = define(
        "shield-cold", "Bool",
        f"(fp.geq {raw_set_point} (fp.add RNE {raw_temperature} {tolerance}))",
    )
    hot = define(
        "shield-hot", "Bool",
        f"(fp.leq {raw_set_point} (fp.sub RNE {raw_temperature} {tolerance}))",
    )
    shield_error = define("shield-error", "Bool", _and(cold, hot))
    required_action = define(
        "required-action",
        enum_sort("ExecutedAction"),
        _ite(
            cold,
            enum_constructor("ExecutedAction", "action_1"),
            _ite(
                hot,
                enum_constructor("ExecutedAction", "action_2"),
                enum_constructor("ExecutedAction", "action_0"),
            ),
        ),
    )

    boundary_assertions = [
        f"(= {raw_set_point} {set_point})",
        f"(= {raw_temperature} {temperature})",
        f"(= {raw_done} {boundary['semantic:latched_completion']})",
        f"(=> (not {shield_error}) (= {executed_action} {required_action}))",
    ]
    for name in ir.required_properties:
        slug = _slug(name)
        boundary_assertions.extend((
            f"(= {boundary[f'slice:property:{slug}:status']} "
            f"{enum_constructor('PropertyStatus', 'property_true')})",
            f"(= {boundary[f'slice:property:{slug}:error']} "
            f"{enum_constructor('PropertyError', 'no_error')})",
        ))

    source_error = define(
        "source-error", "Bool", node_active("execution_error")
    )
    source_terminal = node_active("terminal")
    cycle_time_active = node_active("cycle/time")
    requirement_check_active = node_active("cycle/check")

    property_values: dict[str, tuple[str, str]] = {}
    false_conditions = []
    error_conditions = []
    for name in ir.required_properties:
        slug = _slug(name)
        status = selected(f"slice:property:{slug}:status")
        error = selected(f"slice:property:{slug}:error")
        property_values[name] = (status, error)
        false_conditions.append(
            f"(= {status} {enum_constructor('PropertyStatus', 'property_false')})"
        )
        error_conditions.append(
            f"(= {error} {enum_constructor('PropertyError', 'evaluation_error')})"
        )
    any_property_false = define(
        "any-property-false", "Bool",
        _and(requirement_check_active, _or(*false_conditions)),
    )
    any_property_error = define(
        "any-property-error", "Bool",
        _and(requirement_check_active, _or(*error_conditions)),
    )
    interface_error = define(
        "interface-error", "Bool",
        _or(shield_error, source_error, any_property_error),
    )

    outcome_continue = enum_constructor("OutcomeConstructor", "outcome_continue")
    outcome_terminal = enum_constructor("OutcomeConstructor", "outcome_terminal")
    outcome_error = enum_constructor("OutcomeConstructor", "outcome_error")
    outcome = define(
        "outcome", enum_sort("OutcomeConstructor"), _ite(
            interface_error, outcome_error,
            _ite(any_property_false, outcome_terminal,
                 _ite(source_terminal, outcome_terminal, outcome_continue)),
        ),
    )

    zero64 = fp_literal(0.0, NativeSort.FLOAT64)
    reward = define(
        "reward", smt_sort(NativeSort.FLOAT64), _ite(
            interface_error, zero64,
            _ite(any_property_false, fp_literal(-1.0, NativeSort.FLOAT64),
                 _ite(source_terminal, fp_literal(1.0, NativeSort.FLOAT64),
                      fp_literal(-0.01, NativeSort.FLOAT64))),
        ),
    )
    intrinsic_termination = define(
        "intrinsic-termination", "Bool", _and(
            f"(not {interface_error})", f"(not {any_property_false})", source_terminal
        ),
    )
    execution_error = define(
        "execution-error", enum_sort("ExecutionError"), _ite(
            interface_error,
            enum_constructor("ExecutionError", "execution_error"),
            enum_constructor("ExecutionError", "no_execution_error"),
        ),
    )

    scale_term = fp_literal(float(scale), NativeSort.FLOAT64)

    def observation(uid: str, label: str) -> str:
        divided = f"(fp.div RNE {selected(uid)} {scale_term})"
        return define(
            label, smt_sort(NativeSort.FLOAT32),
            f"((_ to_fp 8 24) RNE {divided})",
        )

    elapsed_ticks = define(
        "elapsed-ticks", "Int",
        _ite(shield_error, "0", _ite(cycle_time_active, "1", "0")),
    )
    elapsed_time = define(
        "elapsed-time", smt_sort(NativeSort.FLOAT64), _ite(
            shield_error, zero64,
            f"(fp.sub RNE {selected('semantic:engine_time')} "
            f"{boundary['semantic:engine_time']})",
        ),
    )
    next_observation_available = f"(= {outcome} {outcome_continue})"
    executed_action_available = f"(not {shield_error})"
    property_available = define(
        "property-results-available", "Bool",
        _and(f"(not {shield_error})", requirement_check_active),
    )

    visible_by_name: dict[str, tuple[str, str]] = {
        "executed_action": (executed_action, executed_action_available),
        "outcome_constructor": (outcome, "true"),
        "next_observation.setPoint.float32_bits": (
            observation("semantic:set_point", "observation-set-point"),
            next_observation_available,
        ),
        "next_observation.temperatureCelcius.float32_bits": (
            observation("semantic:received_temperature_payload",
                        "observation-temperature"),
            next_observation_available,
        ),
        "reward": (reward, "true"),
        "elapsed_ticks": (elapsed_ticks, "true"),
        "elapsed_time": (elapsed_time, "true"),
        "intrinsic_termination": (intrinsic_termination, "true"),
        "execution_error": (execution_error, "true"),
        "action_availability_mask": ("15", "true"),
    }
    for name, (status, error) in property_values.items():
        visible_by_name["property_status:" + name] = (status, property_available)
        visible_by_name["property_error:" + name] = (error, property_available)

    visible_terms = tuple(
        VisibleTermEncoding(
            name=name,
            text=visible_by_name[name][0],
            native_sort=native_sort,
            declared_type=declared_type,
            availability=visible_by_name[name][1],
        )
        for name, native_sort, declared_type in _expected_visible_signature(ir)
        if name not in STRUCTURAL_VISIBLE_TERMS
    )

    transition_body = _and(*transition.assertions)
    guarded_transition = _or(shield_error, transition_body)
    interface_sorts = enum_sort_declarations((
        "ExecutionError", "OutcomeConstructor",
    ))
    return ThermostatInterfaceEncoding(
        namespace=namespace,
        sort_declarations=tuple(dict.fromkeys(
            (*transition.sort_declarations, *interface_sorts)
        )),
        declarations=transition.declarations,
        definitions=tuple(definitions),
        boundary_assertions=tuple(boundary_assertions),
        guarded_transition=guarded_transition,
        visible_terms=visible_terms,
        structural_terms=STRUCTURAL_VISIBLE_TERMS,
        shield_error=shield_error,
        source_error=source_error,
        requirement_check_active=requirement_check_active,
        transition_assertion_count=len(transition.assertions),
    )
