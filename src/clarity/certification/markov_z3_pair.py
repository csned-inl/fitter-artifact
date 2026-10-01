"""Thermostat-only paired counterexample query for buffered Markovity.

This is deliberately a prototype endpoint, not a generic obligation compiler.
It places two independent copies of one checked thermostat history window in
the same SMT query, equates their controller-visible current buffers and policy
proposals, and asks whether any next controller-visible result can differ.

An ``UNSAT`` result rules out that counterexample for the selected history
case.  ``SAT``, ``UNKNOWN``, timeout, and solver errors are never proofs.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class PairedDifferenceEncoding:
    name: str
    assertion: str


@dataclass(frozen=True)
class ThermostatPairedQueryEncoding:
    """One complete counterexample query for one finite-history case."""

    namespace: str
    history_case: str
    left: HistoryWindowEncoding
    right: HistoryWindowEncoding
    current_equalities: tuple[str, ...]
    differences: tuple[PairedDifferenceEncoding, ...]
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
                *extra_assertions,
            )),
        )) + "\n"

    def base_smt2(self) -> str:
        """Return the paired base without any visible-difference assertion."""

        return self._smt2()

    def incremental_smt2(self) -> str:
        """Guard each difference with an assumption selector.

        A solver can parse and assert this base once, first check that it is
        satisfiable, then call ``check(selector)`` for every selector.  This is
        exactly a decomposition of the aggregate disjunction, not a weakening.
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

    left_visible = left.candidate.interface.visible_terms
    right_visible = right.candidate.interface.visible_terms
    if len(left_visible) != len(right_visible):
        raise UnsupportedLoweringError("paired visible-term widths do not match")
    differences = [
        PairedDifferenceEncoding(
            left_term.name,
            _different_visible(left_term, right_term),
        )
        for left_term, right_term in zip(left_visible, right_visible)
    ]

    left_next = left.candidate.next_slots
    right_next = right.candidate.next_slots
    if len(left_next) != len(right_next):
        raise UnsupportedLoweringError("paired next-buffer widths do not match")
    next_slot_differences = []
    for left_slot, right_slot in zip(left_next, right_next):
        _validate_slot_pair(left_slot, right_slot)
        next_slot_differences.append(
            f"(not {_equal(left_slot.text, right_slot.text, NativeSort.FLOAT32)})"
        )
    left_available = left.candidate.next_available
    right_available = right.candidate.next_available
    next_buffer_difference = _or(
        f"(not (= {left_available} {right_available}))",
        _and(left_available, right_available, _or(*next_slot_differences)),
    )
    differences.append(PairedDifferenceEncoding(
        "next_buffer_shift", next_buffer_difference,
    ))

    names = tuple(item.name for item in differences)
    expected_names = {term.name for term in slice_.visible_terms}
    if set(names) != expected_names or len(names) != len(set(names)):
        raise UnsupportedLoweringError(
            "paired query difference inventory is incomplete or duplicated"
        )
    counterexample = _or(*(item.assertion for item in differences))
    return ThermostatPairedQueryEncoding(
        namespace=namespace,
        history_case=history_case,
        left=left,
        right=right,
        current_equalities=tuple(current_equalities),
        differences=tuple(differences),
        counterexample_assertion=counterexample,
    )


__all__ = [
    "PairedDifferenceEncoding",
    "ThermostatPairedQueryEncoding",
    "compile_thermostat_paired_query",
]
