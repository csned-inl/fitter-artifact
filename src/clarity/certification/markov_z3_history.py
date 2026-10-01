"""Exact, sparse SMT view of the finite controller buffer.

This module describes only the controller-facing buffer representation, its
reset padding, and its one-step shift.  It deliberately does not construct a
history transition chain, a paired-run query, or a proof certificate.  The
existing thermostat transition relation is referenced once through the
controller-visible interface; it is never copied into buffer slots.

The representation matches ``BufferedDiscreteEnv`` exactly:

* current Float32 observation;
* past Float32 observations, newest first; and
* past *executed* actions, newest first, as four-element Float32 one-hot
  vectors.

Next-buffer expressions are available only for a continuing outcome.  Error
and terminal paths therefore cannot accidentally acquire a fictitious next
decision buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .markov_contract import load_controller_step_contract
from .markov_ir import NativeSort
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_event import enum_constructor
from .markov_z3_expr import fp_literal, quoted_symbol, smt_sort
from .markov_z3_interface import ThermostatInterfaceEncoding
from .markov_z3_transition import TransitionRelationEncoding


OBSERVATION_FIELDS = ("setPoint", "temperatureCelcius")
ACTION_COMPONENTS = ("action_0", "action_1", "action_2", "action_3")


def _and(*items: str) -> str:
    if not items:
        return "true"
    if len(items) == 1:
        return items[0]
    return f"(and {' '.join(items)})"


@dataclass(frozen=True)
class BufferSlotEncoding:
    """One scalar Float32 position in a flattened policy buffer."""

    index: int
    section: str
    lag: int
    component: str
    text: str
    source: str


@dataclass(frozen=True)
class BufferHistoryCaseEncoding:
    """Only the reset-padding component of one manifest history case."""

    name: str
    kind: str
    decision_count: int | None
    padding_assertions: tuple[str, ...]


@dataclass(frozen=True)
class BufferHistoryEncoding:
    """Shared buffer terms attached to one controller-visible interface."""

    namespace: str
    b_obs: int
    b_act: int
    interface: ThermostatInterfaceEncoding
    declarations: tuple[str, ...]
    definitions: tuple[str, ...]
    current_slots: tuple[BufferSlotEncoding, ...]
    next_slots: tuple[BufferSlotEncoding, ...]
    history_cases: tuple[BufferHistoryCaseEncoding, ...]
    next_available: str
    transition_declaration_count: int

    @property
    def scalar_width(self) -> int:
        return len(self.current_slots)

    def case(self, name: str) -> BufferHistoryCaseEncoding:
        matches = [case for case in self.history_cases if case.name == name]
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]

    def slot(
        self, phase: str, section: str, lag: int, component: str,
    ) -> BufferSlotEncoding:
        slots = self.current_slots if phase == "current" else self.next_slots \
            if phase == "next" else ()
        matches = [slot for slot in slots if (
            slot.section, slot.lag, slot.component
        ) == (section, lag, component)]
        if len(matches) != 1:
            raise KeyError((phase, section, lag, component))
        return matches[0]

    def smt2(
        self,
        *extra_assertions: str,
        history_case: str | None = None,
    ) -> str:
        padding = () if history_case is None \
            else self.case(history_case).padding_assertions
        return "\n".join((
            *self.interface.sort_declarations,
            *self.interface.declarations,
            *self.declarations,
            *self.interface.definitions,
            *self.definitions,
            *(f"(assert {formula})" for formula in (
                self.interface.assertions + padding + tuple(extra_assertions)
            )),
        )) + "\n"


def _validate_contract(contract: Mapping[str, Any]) -> float:
    expected = {
        ("observation", "policy_fields"): list(OBSERVATION_FIELDS),
        ("observation", "dtype"): "float32",
        ("actions", "proposal_ids"): [0, 1, 2, 3],
        ("actions", "history_records"): "executed_action_after_spec_shield",
        ("buffer", "layout"): [
            "current_observation",
            "past_observations_newest_first",
            "past_executed_actions_newest_first_one_hot",
        ],
        ("buffer", "observation_padding"): "float32_zero",
        ("buffer", "action_padding"): "float32_zero_vector",
        ("buffer", "action_width"): len(ACTION_COMPONENTS),
        ("buffer", "reset_rule"): (
            "return current observation followed by zero histories, then retain that "
            "current observation as the first prior observation"
        ),
        ("buffer", "step_rule"): (
            "push executed action, form next buffer from next observation and retained "
            "histories, then retain next observation"
        ),
    }
    errors = []
    for (section, key), value in expected.items():
        actual_section = contract.get(section)
        actual = actual_section.get(key) if isinstance(actual_section, Mapping) else None
        if actual != value:
            errors.append(f"{section}.{key}: expected {value!r}, found {actual!r}")
    observation = contract.get("observation")
    scale = observation.get("observation_scale") \
        if isinstance(observation, Mapping) else None
    if type(scale) not in {int, float} or not math.isfinite(float(scale)) \
            or float(scale) == 0.0:
        errors.append("observation.observation_scale must be finite and nonzero")
    if errors:
        raise UnsupportedLoweringError(
            "invalid finite-buffer contract: " + "; ".join(errors)
        )
    return float(scale)


def _validate_interface(
    interface: ThermostatInterfaceEncoding,
    transition: TransitionRelationEncoding,
) -> None:
    if interface.declarations != transition.declarations \
            or interface.transition_assertion_count != len(transition.assertions):
        raise UnsupportedLoweringError(
            "buffer/interface transition identity mismatch"
        )
    if interface.structural_terms != ("next_buffer_shift",):
        raise UnsupportedLoweringError(
            "buffer shift is not the sole structural visible term"
        )

    boundary = dict(transition.boundary_entry_terms)
    if len(boundary) != len(transition.boundary_entry_terms):
        raise UnsupportedLoweringError("duplicate transition boundary storage")
    required_boundary = {
        "slice:shield:setPoint",
        "slice:shield:temperatureCelcius",
        "slice:executed_action",
    }
    if not required_boundary.issubset(boundary):
        raise UnsupportedLoweringError(
            "transition lacks a required finite-buffer boundary term"
        )

    executed = interface.visible("executed_action")
    outcome = interface.visible("outcome_constructor")
    observations = tuple(interface.visible(
        f"next_observation.{field}.float32_bits"
    ) for field in OBSERVATION_FIELDS)
    expected_outcome = quoted_symbol(interface.namespace, "definition", "outcome")
    expected_available = (
        f"(= {expected_outcome} "
        f"{enum_constructor('OutcomeConstructor', 'outcome_continue')})"
    )
    expected_observations = tuple(
        quoted_symbol(interface.namespace, "definition", label)
        for label in ("observation-set-point", "observation-temperature")
    )
    if (
        executed.native_sort is not NativeSort.ENUM
        or executed.declared_type != "ExecutedAction"
        or executed.text != boundary["slice:executed_action"]
        or executed.availability != f"(not {interface.shield_error})"
        or outcome.native_sort is not NativeSort.ENUM
        or outcome.declared_type != "OutcomeConstructor"
        or outcome.text != expected_outcome
    ):
        raise UnsupportedLoweringError(
            "executed-action or outcome interface identity mismatch"
        )
    for term, expected_text in zip(observations, expected_observations):
        if term.native_sort is not NativeSort.FLOAT32 \
                or term.text != expected_text \
                or term.availability != expected_available:
            raise UnsupportedLoweringError(
                "next-observation interface identity or availability mismatch"
            )


def compile_buffer_history(
    interface: ThermostatInterfaceEncoding,
    transition: TransitionRelationEncoding,
    *,
    b_obs: int,
    b_act: int,
    namespace: str = "buffer-history",
    controller_contract: Mapping[str, Any] | None = None,
) -> BufferHistoryEncoding:
    """Compile exact layout, padding, and one-step shift without path copies."""

    if type(b_obs) is not int or b_obs < 0:
        raise ValueError("b_obs must be a nonnegative integer")
    if type(b_act) is not int or b_act < 0:
        raise ValueError("b_act must be a nonnegative integer")
    if not namespace:
        raise ValueError("buffer-history namespace is empty")

    contract = load_controller_step_contract() \
        if controller_contract is None else dict(controller_contract)
    scale = _validate_contract(contract)
    _validate_interface(interface, transition)

    fp32 = smt_sort(NativeSort.FLOAT32)
    zero = fp_literal(0.0, NativeSort.FLOAT32)
    one = fp_literal(1.0, NativeSort.FLOAT32)
    scale_term = fp_literal(scale, NativeSort.FLOAT64)
    boundary = dict(transition.boundary_entry_terms)
    declarations: list[str] = []
    definitions: list[str] = []
    definition_symbols: set[str] = set()

    def define(label: str, expression: str) -> str:
        symbol = quoted_symbol(namespace, "definition", label)
        if symbol in definition_symbols:
            raise UnsupportedLoweringError(
                f"duplicate buffer-history definition: {label}"
            )
        definition_symbols.add(symbol)
        definitions.append(f"(define-fun {symbol} () {fp32} {expression})")
        return symbol

    current_observation: dict[str, str] = {}
    for field, source_uid in zip(OBSERVATION_FIELDS, (
        "slice:shield:setPoint", "slice:shield:temperatureCelcius",
    )):
        divided = f"(fp.div RNE {boundary[source_uid]} {scale_term})"
        current_observation[field] = define(
            f"current-observation::{field}",
            f"((_ to_fp 8 24) RNE {divided})",
        )

    current_slots: list[BufferSlotEncoding] = []

    def add_current(section: str, lag: int, component: str, text: str, source: str):
        current_slots.append(BufferSlotEncoding(
            len(current_slots), section, lag, component, text, source,
        ))

    for field in OBSERVATION_FIELDS:
        add_current("current_observation", 0, field,
                    current_observation[field], "normalized_boundary_observation")
    for lag in range(1, b_obs + 1):
        for field in OBSERVATION_FIELDS:
            symbol = quoted_symbol(
                namespace, "current", "past-observation", str(lag), field,
            )
            declarations.append(f"(declare-const {symbol} {fp32})")
            add_current("past_observation", lag, field, symbol,
                        "history_or_reset_padding")
    for lag in range(1, b_act + 1):
        for component in ACTION_COMPONENTS:
            symbol = quoted_symbol(
                namespace, "current", "past-executed-action", str(lag), component,
            )
            declarations.append(f"(declare-const {symbol} {fp32})")
            add_current("past_executed_action", lag, component, symbol,
                        "executed_action_history_or_reset_padding")

    executed = interface.visible("executed_action")
    next_observations = {
        field: interface.visible(
            f"next_observation.{field}.float32_bits"
        ) for field in OBSERVATION_FIELDS
    }
    one_hot = {
        component: (
            f"(ite (= {executed.text} "
            f"{enum_constructor('ExecutedAction', component)}) {one} {zero})"
        ) for component in ACTION_COMPONENTS
    }

    current_by_key = {
        (slot.section, slot.lag, slot.component): slot for slot in current_slots
    }
    next_sources: list[tuple[str, int, str, str, str]] = []
    for field in OBSERVATION_FIELDS:
        term = next_observations[field]
        next_sources.append((
            "current_observation", 0, field, term.text,
            f"interface:{term.name}",
        ))
    for lag in range(1, b_obs + 1):
        source = "current_observation" if lag == 1 else "past_observation"
        source_lag = 0 if lag == 1 else lag - 1
        for field in OBSERVATION_FIELDS:
            slot = current_by_key[(source, source_lag, field)]
            next_sources.append((
                "past_observation", lag, field, slot.text,
                f"current-slot:{slot.index}",
            ))
    for lag in range(1, b_act + 1):
        for component in ACTION_COMPONENTS:
            if lag == 1:
                source_text = one_hot[component]
                source = "interface:executed_action_one_hot"
            else:
                slot = current_by_key[(
                    "past_executed_action", lag - 1, component,
                )]
                source_text = slot.text
                source = f"current-slot:{slot.index}"
            next_sources.append((
                "past_executed_action", lag, component, source_text, source,
            ))

    next_slots: list[BufferSlotEncoding] = []
    for index, (section, lag, component, source_text, source) in enumerate(next_sources):
        symbol = define(
            f"next-slot::{index}::{section}::{lag}::{component}", source_text,
        )
        next_slots.append(BufferSlotEncoding(
            index, section, lag, component, symbol, source,
        ))

    next_available_symbol = quoted_symbol(
        namespace, "definition", "next-buffer-available",
    )
    if next_available_symbol in definition_symbols:
        raise UnsupportedLoweringError("duplicate next-buffer availability definition")
    definition_symbols.add(next_available_symbol)
    next_available_expression = _and(
        executed.availability,
        *(next_observations[field].availability for field in OBSERVATION_FIELDS),
    )
    definitions.append(
        f"(define-fun {next_available_symbol} () Bool {next_available_expression})"
    )

    length = max(b_obs, b_act)
    cases: list[BufferHistoryCaseEncoding] = []
    for decision_count in range(length):
        padding = tuple(
            f"(= {slot.text} {zero})" for slot in current_slots
            if (slot.section == "past_observation" and slot.lag > decision_count)
            or (slot.section == "past_executed_action" and slot.lag > decision_count)
        )
        cases.append(BufferHistoryCaseEncoding(
            f"reset_prefix_{decision_count}", "reset_prefix", decision_count,
            padding,
        ))
    cases.append(BufferHistoryCaseEncoding(
        "steady_state", "steady_state", None, (),
    ))

    if len(current_slots) != len(next_slots):
        raise UnsupportedLoweringError("current/next finite-buffer width mismatch")
    if len(set(declarations)) != len(declarations) \
            or len(set(definitions)) != len(definitions):
        raise UnsupportedLoweringError("duplicate finite-buffer SMT declaration")
    if set(declarations).intersection(interface.declarations):
        raise UnsupportedLoweringError("finite-buffer aliases transition declarations")

    return BufferHistoryEncoding(
        namespace=namespace,
        b_obs=b_obs,
        b_act=b_act,
        interface=interface,
        declarations=tuple(declarations),
        definitions=tuple(definitions),
        current_slots=tuple(current_slots),
        next_slots=tuple(next_slots),
        history_cases=tuple(cases),
        next_available=next_available_symbol,
        transition_declaration_count=len(transition.declarations),
    )


__all__ = [
    "ACTION_COMPONENTS",
    "OBSERVATION_FIELDS",
    "BufferHistoryCaseEncoding",
    "BufferHistoryEncoding",
    "BufferSlotEncoding",
    "compile_buffer_history",
]
