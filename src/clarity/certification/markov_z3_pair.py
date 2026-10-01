"""Thermostat-only paired counterexample query for buffered Markovity.

This is deliberately a prototype endpoint, not a generic obligation compiler.
It places two independent copies of one checked thermostat history window in
the same SMT query, shares the fixed MDP parameters, equates their
controller-visible current buffers and policy proposals, and asks whether any
next controller-visible result can differ.

An ``UNSAT`` result rules out that counterexample for the selected history
case.  ``SAT``, ``UNKNOWN``, timeout, and solver errors are never proofs.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .markov_interval import FiniteDecisionInterval
from .markov_ir import MarkovIR, NativeSort
from .markov_slice import TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_history import BufferSlotEncoding, compile_buffer_history
from .markov_z3_interface import VisibleTermEncoding, compile_thermostat_interface
from .markov_z3_expr import quoted_symbol
from .markov_z3_transition import compile_transition_relation
from .markov_z3_window import HistoryWindowEncoding, compile_history_window


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


def _equal(left: str, right: str, native_sort: NativeSort) -> str:
    if native_sort in {NativeSort.FLOAT32, NativeSort.FLOAT64}:
        return (
            f"(= (fp.to_ieee_bv {left}) "
            f"(fp.to_ieee_bv {right}))"
        )
    if native_sort in {
        NativeSort.BOOL, NativeSort.PRESENCE, NativeSort.INT, NativeSort.ENUM,
    }:
        return f"(= {left} {right})"
    raise UnsupportedLoweringError(
        "paired query cannot compare a runtime container sort"
    )


def _different_visible(
    left: VisibleTermEncoding,
    right: VisibleTermEncoding,
) -> str:
    signature_left = (left.name, left.native_sort, left.declared_type)
    signature_right = (right.name, right.native_sort, right.declared_type)
    if signature_left != signature_right:
        raise UnsupportedLoweringError(
            "paired query visible signatures do not match"
        )
    availability_difference = f"(not (= {left.availability} {right.availability}))"
    value_difference = (
        f"(and {left.availability} {right.availability} "
        f"(not {_equal(left.text, right.text, left.native_sort)}))"
    )
    return _or(availability_difference, value_difference)


def _validate_slot_pair(
    left: BufferSlotEncoding,
    right: BufferSlotEncoding,
) -> None:
    if (
        left.index, left.section, left.lag, left.component
    ) != (
        right.index, right.section, right.lag, right.component
    ):
        raise UnsupportedLoweringError(
            "paired query buffer layouts do not match"
        )


def _validate_alpha_copies(
    left: HistoryWindowEncoding,
    right: HistoryWindowEncoding,
    *,
    left_prefix: str,
    right_prefix: str,
) -> None:
    """Require the paired bases to differ only by their side namespace."""

    def rename(items: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item.replace(left_prefix, right_prefix) for item in items)

    if left.sort_declarations != right.sort_declarations \
            or rename(left.declarations) != right.declarations \
            or rename(left.definitions) != right.definitions \
            or rename(left.assertions) != right.assertions \
            or left.history_case.name != right.history_case.name \
            or left.history_case.kind != right.history_case.kind \
            or left.history_case.decision_count != right.history_case.decision_count \
            or left.carried_state_uids != right.carried_state_uids \
            or left.reinitialized_uids != right.reinitialized_uids:
        raise UnsupportedLoweringError(
            "paired bases are not exact namespace-renamed copies"
        )


_QUOTED_SYMBOL = re.compile(r"\|[^|]+\|")
_DEFINE_HEAD = re.compile(r"^\(define-fun (\|[^|]+\|) \(\)")
_DECLARE_HEAD = re.compile(r"^\(declare-const (\|[^|]+\|) ")


def _symbols(text: str) -> set[str]:
    return set(_QUOTED_SYMBOL.findall(text))


def _head(pattern: re.Pattern[str], text: str, kind: str) -> str:
    match = pattern.match(text)
    if match is None:
        raise UnsupportedLoweringError(f"cannot parse paired-query {kind}")
    return match.group(1)


@dataclass(frozen=True)
class PairedDifferenceEncoding:
    name: str
    assertion: str


@dataclass(frozen=True)
class TargetOverapproximationEncoding:
    """A premise-dropping target query whose UNSAT result is sound.

    Every retained premise is an exact member of the full paired base.  The
    omitted premises can only enlarge the model set, so SAT is only a candidate
    and UNKNOWN is inconclusive, while UNSAT proves the corresponding full
    paired obligation.
    """

    name: str
    sort_declarations: tuple[str, ...]
    declarations: tuple[str, ...]
    definitions: tuple[str, ...]
    premise_assertions: tuple[str, ...]
    difference_assertion: str
    source_assertion_count: int

    @property
    def omitted_assertion_count(self) -> int:
        return self.source_assertion_count - len(self.premise_assertions)

    def smt2(self) -> str:
        return "\n".join((
            *self.sort_declarations,
            *self.declarations,
            *self.definitions,
            *(f"(assert {formula})" for formula in self.premise_assertions),
            f"(assert {self.difference_assertion})",
        )) + "\n"


@dataclass(frozen=True)
class ThermostatPairedQueryEncoding:
    """One complete counterexample query for one finite-history case."""

    namespace: str
    history_case: str
    left: HistoryWindowEncoding
    right: HistoryWindowEncoding
    current_equalities: tuple[str, ...]
    fixed_parameter_equalities: tuple[str, ...]
    differences: tuple[PairedDifferenceEncoding, ...]
    structural_discharges: tuple[str, ...]
    counterexample_assertion: str

    @property
    def difference_selectors(self) -> tuple[str, ...]:
        prefix = "".join(
            character if character.isalnum() else "_"
            for character in self.namespace
        )
        return tuple(
            f"{prefix}_difference_{index}"
            for index, _difference in enumerate(self.differences)
        )

    def _smt2(
        self,
        *,
        extra_declarations: tuple[str, ...] = (),
        extra_assertions: tuple[str, ...] = (),
    ) -> str:
        sort_declarations = tuple(dict.fromkeys((
            *self.left.sort_declarations,
            *self.right.sort_declarations,
        )))
        declarations = (
            self.left.declarations + self.right.declarations
            + extra_declarations
        )
        definitions = self.left.definitions + self.right.definitions
        if len(set(declarations)) != len(declarations):
            raise UnsupportedLoweringError(
                "paired query aliases left and right declarations"
            )
        return "\n".join((
            *sort_declarations,
            *declarations,
            *definitions,
            *(f"(assert {formula})" for formula in (
                *self.left.assertions,
                *self.right.assertions,
                *self.current_equalities,
                *self.fixed_parameter_equalities,
                *extra_assertions,
            )),
        )) + "\n"

    def base_smt2(self) -> str:
        """Return the paired base without any visible-difference assertion."""

        return self._smt2()

    def incremental_smt2(self) -> str:
        """Guard each difference with an assumption selector.

        A solver can parse and assert this base once, then call
        ``check(selector)`` for every selector.  Paired-base nonvacuity follows
        compositionally from the checked single-window witnesses and exact
        alpha copies.  This is exactly a decomposition of the aggregate
        disjunction, not a weakening.
        """

        selectors = self.difference_selectors
        return self._smt2(
            extra_declarations=tuple(
                f"(declare-const {selector} Bool)" for selector in selectors
            ),
            extra_assertions=tuple(
                f"(=> {selector} {difference.assertion})"
                for selector, difference in zip(selectors, self.differences)
            ),
        )

    def smt2(self) -> str:
        """Return the auditable monolithic counterexample formula."""

        return self._smt2(extra_assertions=(self.counterexample_assertion,))


def _compile_side(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    *,
    history_case: str,
    namespace: str,
) -> HistoryWindowEncoding:
    transition = compile_transition_relation(
        slice_, ir, interval, namespace=namespace + "::transition",
    )
    interface = compile_thermostat_interface(
        slice_, ir, interval, transition,
        namespace=namespace + "::interface",
    )
    candidate = compile_buffer_history(
        interface, transition, b_obs=2, b_act=1,
        namespace=namespace + "::buffer",
    )
    return compile_history_window(
        slice_, ir, interval, transition, candidate,
        history_case=history_case, namespace=namespace + "::window",
    )


def compile_thermostat_paired_query(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    *,
    history_case: str,
    namespace: str = "thermostat-markov-prototype",
) -> ThermostatPairedQueryEncoding:
    """Compile the fixed ``b_obs=2, b_act=1`` thermostat Markov query."""

    if not namespace:
        raise ValueError("paired-query namespace is empty")
    left = _compile_side(
        slice_, ir, interval, history_case=history_case,
        namespace=namespace + "::left",
    )
    right = _compile_side(
        slice_, ir, interval, history_case=history_case,
        namespace=namespace + "::right",
    )
    _validate_alpha_copies(
        left,
        right,
        left_prefix=namespace + "::left",
        right_prefix=namespace + "::right",
    )

    left_slots = left.candidate.current_slots
    right_slots = right.candidate.current_slots
    if len(left_slots) != len(right_slots):
        raise UnsupportedLoweringError("paired current-buffer widths do not match")
    current_equalities = []
    for left_slot, right_slot in zip(left_slots, right_slots):
        _validate_slot_pair(left_slot, right_slot)
        current_equalities.append(
            _equal(left_slot.text, right_slot.text, NativeSort.FLOAT32)
        )

    left_proposal_text = quoted_symbol(
        namespace + "::left::transition", "boundary-entry",
        "slice:policy_proposal",
    )
    right_proposal_text = quoted_symbol(
        namespace + "::right::transition", "boundary-entry",
        "slice:policy_proposal",
    )
    if not any(left_proposal_text in item for item in left.declarations) \
            or not any(right_proposal_text in item for item in right.declarations):
        raise UnsupportedLoweringError(
            "paired query cannot resolve policy-proposal boundary symbols"
        )
    current_equalities.append(
        _equal(left_proposal_text, right_proposal_text, NativeSort.ENUM)
    )

    immutable = tuple(
        item.identity for item in slice_.storages
        if item.identity.role == "immutable_parameter"
    )
    expected_immutable = {
        "semantic:outside_temperature",
        "semantic:set_point",
        "semantic:tolerance",
        "slice:configured_dt",
    }
    if {item.uid for item in immutable} != expected_immutable \
            or len(immutable) != len(expected_immutable) \
            or any(item.native_sort is not NativeSort.FLOAT64 for item in immutable):
        raise UnsupportedLoweringError(
            "paired query immutable-parameter inventory mismatch"
        )
    fixed_parameter_equalities = []
    for identity in sorted(immutable, key=lambda item: item.uid):
        left_text = quoted_symbol(
            namespace + "::left::transition", "boundary-entry", identity.uid,
        )
        right_text = quoted_symbol(
            namespace + "::right::transition", "boundary-entry", identity.uid,
        )
        if not any(left_text in item for item in left.declarations) \
                or not any(right_text in item for item in right.declarations):
            raise UnsupportedLoweringError(
                "paired query cannot resolve immutable-parameter symbols"
            )
        fixed_parameter_equalities.append(
            _equal(left_text, right_text, identity.native_sort)
        )

    left_visible = left.candidate.interface.visible_terms
    right_visible = right.candidate.interface.visible_terms
    if len(left_visible) != len(right_visible):
        raise UnsupportedLoweringError("paired visible-term widths do not match")
    differences = []
    for left_term, right_term in zip(left_visible, right_visible):
        if left_term.name == "action_availability_mask":
            if (
                left_term.text, left_term.availability,
                right_term.name, right_term.text, right_term.availability,
            ) != (
                "15", "true", "action_availability_mask", "15", "true",
            ):
                raise UnsupportedLoweringError(
                    "action-availability mask is not the checked constant"
                )
            continue
        differences.append(PairedDifferenceEncoding(
            left_term.name,
            _different_visible(left_term, right_term),
        ))

    left_next = left.candidate.next_slots
    right_next = right.candidate.next_slots
    if len(left_next) != len(right_next):
        raise UnsupportedLoweringError("paired next-buffer widths do not match")
    allowed_next_sources = {
        "interface:next_observation.setPoint.float32_bits",
        "interface:next_observation.temperatureCelcius.float32_bits",
        "interface:executed_action_one_hot",
        *(f"current-slot:{slot.index}" for slot in left_slots),
    }
    for left_slot, right_slot in zip(left_next, right_next):
        _validate_slot_pair(left_slot, right_slot)
        if left_slot.source != right_slot.source \
                or left_slot.source not in allowed_next_sources:
            raise UnsupportedLoweringError(
                "next-buffer slot lacks an exact compared source"
            )

    names = tuple(item.name for item in differences)
    structural_discharges = (
        "action_availability_mask",
        "next_buffer_shift",
    )
    expected_names = {
        term.name for term in slice_.visible_terms
        if term.name not in structural_discharges
    }
    if set(names) != expected_names or len(names) != len(set(names)):
        raise UnsupportedLoweringError(
            "paired query difference inventory is incomplete or duplicated"
        )
    if not {
        "executed_action",
        "next_observation.setPoint.float32_bits",
        "next_observation.temperatureCelcius.float32_bits",
    }.issubset(names):
        raise UnsupportedLoweringError(
            "next-buffer structural discharge lacks a compared source"
        )
    counterexample = _or(*(item.assertion for item in differences))
    return ThermostatPairedQueryEncoding(
        namespace=namespace,
        history_case=history_case,
        left=left,
        right=right,
        current_equalities=tuple(current_equalities),
        fixed_parameter_equalities=tuple(fixed_parameter_equalities),
        differences=tuple(differences),
        structural_discharges=structural_discharges,
        counterexample_assertion=counterexample,
    )


def compile_executed_action_overapproximation(
    query: ThermostatPairedQueryEncoding,
) -> TargetOverapproximationEncoding:
    """Drop everything outside the exact pre-transition shield/action cone."""

    matches = tuple(
        item for item in query.differences if item.name == "executed_action"
    )
    if len(matches) != 1:
        raise UnsupportedLoweringError(
            "executed-action target is missing or duplicated"
        )

    left_prefix = query.namespace + "::left"
    right_prefix = query.namespace + "::right"
    _validate_alpha_copies(
        query.left,
        query.right,
        left_prefix=left_prefix,
        right_prefix=right_prefix,
    )

    left_action = query.left.candidate.interface.visible("executed_action")
    right_action = query.right.candidate.interface.visible("executed_action")
    expected_actions = (
        quoted_symbol(left_prefix + "::transition", "boundary-entry",
                      "slice:executed_action"),
        quoted_symbol(right_prefix + "::transition", "boundary-entry",
                      "slice:executed_action"),
    )
    if (left_action.text, right_action.text) != expected_actions \
            or left_action.native_sort is not NativeSort.ENUM \
            or right_action.native_sort is not NativeSort.ENUM:
        raise UnsupportedLoweringError(
            "executed-action target does not use the checked boundary terms"
        )
    expected_difference = _different_visible(left_action, right_action)
    if matches[0].assertion != expected_difference:
        raise UnsupportedLoweringError(
            "executed-action difference is not the reconstructed predicate"
        )

    premises: list[str] = []
    for side, prefix, action in (
        (query.left, left_prefix, left_action),
        (query.right, right_prefix, right_action),
    ):
        shield_error = quoted_symbol(
            prefix + "::interface", "definition", "shield-error",
        )
        required_action = quoted_symbol(
            prefix + "::interface", "definition", "required-action",
        )
        if action.availability != f"(not {shield_error})":
            raise UnsupportedLoweringError(
                "executed-action availability is not the checked shield guard"
            )
        binding = (
            f"(=> (not {shield_error}) "
            f"(= {action.text} {required_action}))"
        )
        raw_set_point = quoted_symbol(
            prefix + "::transition", "boundary-entry", "slice:shield:setPoint",
        )
        semantic_set_point = quoted_symbol(
            prefix + "::transition", "boundary-entry", "semantic:set_point",
        )
        set_point_bridge = f"(= {raw_set_point} {semantic_set_point})"
        boundary = side.candidate.interface.boundary_assertions
        if binding not in boundary or set_point_bridge not in boundary:
            raise UnsupportedLoweringError(
                "executed-action cone lacks an exact interface premise"
            )
        premises.extend((binding, set_point_bridge))

    slots = query.left.candidate.current_slots
    if len(slots) < 2 \
            or tuple(slot.source for slot in slots[:2]) != (
                "normalized_boundary_observation",
                "normalized_boundary_observation",
            ) \
            or tuple(slot.component for slot in slots[:2]) != (
                "setPoint", "temperatureCelcius",
            ):
        raise UnsupportedLoweringError(
            "executed-action cone cannot identify current observations"
        )
    if len(query.current_equalities) != len(slots) + 1:
        raise UnsupportedLoweringError(
            "paired current-equality inventory mismatch"
        )
    expected_observation_equalities = tuple(
        _equal(
            query.left.candidate.current_slots[index].text,
            query.right.candidate.current_slots[index].text,
            NativeSort.FLOAT32,
        )
        for index in range(2)
    )
    if query.current_equalities[:2] != expected_observation_equalities:
        raise UnsupportedLoweringError(
            "executed-action cone has mutated current-observation equalities"
        )
    premises.extend(query.current_equalities[:2])

    fixed_by_uid = {
        uid: _equal(
            quoted_symbol(left_prefix + "::transition", "boundary-entry", uid),
            quoted_symbol(right_prefix + "::transition", "boundary-entry", uid),
            NativeSort.FLOAT64,
        )
        for uid in ("semantic:set_point", "semantic:tolerance")
    }
    if not set(fixed_by_uid.values()).issubset(query.fixed_parameter_equalities):
        raise UnsupportedLoweringError(
            "executed-action cone lacks shared fixed parameters"
        )
    premises.extend(fixed_by_uid[uid] for uid in sorted(fixed_by_uid))

    full_assertions = (
        query.left.assertions + query.right.assertions
        + query.current_equalities + query.fixed_parameter_equalities
    )
    if len(set(premises)) != len(premises) \
            or not set(premises).issubset(full_assertions):
        raise UnsupportedLoweringError(
            "executed-action cone contains a non-source or duplicate premise"
        )

    all_definitions = query.left.definitions + query.right.definitions
    definitions_by_symbol: dict[str, str] = {}
    for definition in all_definitions:
        symbol = _head(_DEFINE_HEAD, definition, "definition")
        if symbol in definitions_by_symbol:
            raise UnsupportedLoweringError(
                "paired query has duplicate definition symbols"
            )
        definitions_by_symbol[symbol] = definition

    needed = _symbols("\n".join((*premises, matches[0].assertion)))
    selected_symbols: set[str] = set()
    while True:
        pending = (needed & set(definitions_by_symbol)) - selected_symbols
        if not pending:
            break
        selected_symbols.update(pending)
        for symbol in pending:
            needed.update(_symbols(definitions_by_symbol[symbol]) - {symbol})
    definitions = tuple(
        item for item in all_definitions
        if _head(_DEFINE_HEAD, item, "definition") in selected_symbols
    )

    all_declarations = query.left.declarations + query.right.declarations
    declarations_by_symbol: dict[str, str] = {}
    for declaration in all_declarations:
        symbol = _head(_DECLARE_HEAD, declaration, "declaration")
        if symbol in declarations_by_symbol:
            raise UnsupportedLoweringError(
                "paired query has duplicate declaration symbols"
            )
        declarations_by_symbol[symbol] = declaration
    declarations = tuple(
        item for item in all_declarations
        if _head(_DECLARE_HEAD, item, "declaration") in needed
    )

    sort_declarations = tuple(dict.fromkeys((
        *query.left.sort_declarations,
        *query.right.sort_declarations,
    )))
    available = (
        set(declarations_by_symbol) | set(definitions_by_symbol)
        | _symbols("\n".join(sort_declarations))
    )
    unknown = needed - available
    if unknown:
        raise UnsupportedLoweringError(
            "executed-action cone has unresolved SMT symbols: "
            + ", ".join(sorted(unknown))
        )

    encoding = TargetOverapproximationEncoding(
        name="executed_action",
        sort_declarations=sort_declarations,
        declarations=declarations,
        definitions=definitions,
        premise_assertions=tuple(premises),
        difference_assertion=matches[0].assertion,
        source_assertion_count=len(full_assertions),
    )
    if encoding.omitted_assertion_count <= 0:
        raise UnsupportedLoweringError(
            "executed-action query did not over-approximate the full base"
        )
    return encoding


__all__ = [
    "PairedDifferenceEncoding",
    "TargetOverapproximationEncoding",
    "ThermostatPairedQueryEncoding",
    "compile_executed_action_overapproximation",
    "compile_thermostat_paired_query",
]
