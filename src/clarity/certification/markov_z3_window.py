"""Linear finite predecessor windows for the thermostat buffer relation.

The compiler instantiates one already-checked sparse transition per historical
decision and connects successive decision-boundary stores directly.  It does
not enumerate control-flow paths and it does not copy a transition per visible
predicate.

The current profile has a unique shield-selected executed action for every
successful decision.  Consequently the buffer is write-only history with
respect to the source transition: repeated buffer shifts can be replayed as a
direct lag projection from historical observations and executed actions.  A
future profile whose executed action depends on the proposal must not use this
reduction without a separate proof.

No exact source-reset relation exists in ``MarkovIR`` yet.  Reset-prefix and
steady-state windows therefore begin in the full type-correct state domain
(``I = true``).  This is a conservative over-approximation: it can cause a
spurious SAT result, but cannot remove a reachable counterexample.  The result
is not a reachability proof or certificate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .markov_interval import FiniteDecisionInterval
from .markov_ir import MarkovIR, NativeSort
from .markov_slice import TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_event import enum_constructor
from .markov_z3_expr import fp_literal
from .markov_z3_history import (
    ACTION_COMPONENTS,
    OBSERVATION_FIELDS,
    BufferHistoryCaseEncoding,
    BufferHistoryEncoding,
    compile_buffer_history,
)
from .markov_z3_interface import (
    ThermostatInterfaceEncoding,
    compile_thermostat_interface,
)
from .markov_z3_transition import (
    TransitionRelationEncoding,
    compile_transition_relation,
)


INITIAL_STATE_PREMISE = "true_type_domain_overapproximation"


def _reinitialized_uids(ir: MarkovIR) -> tuple[str, ...]:
    values = {
        "slice:policy_proposal",
        "slice:executed_action",
        "slice:shield:setPoint",
        "slice:shield:temperatureCelcius",
        "slice:shield:done",
    }
    for name in ir.required_properties:
        slug = name.replace(" ", "_").lower()
        values.update((
            f"slice:property:{slug}:status",
            f"slice:property:{slug}:error",
        ))
    return tuple(sorted(values))


def _and(*items: str) -> str:
    if not items:
        return "true"
    if len(items) == 1:
        return items[0]
    return f"(and {' '.join(items)})"


@dataclass(frozen=True)
class HistoricalStepEncoding:
    """One continuing historical decision in a finite predecessor window."""

    index: int
    decision_offset: int
    transition: TransitionRelationEncoding
    interface: ThermostatInterfaceEncoding
    request_selector: str
    continue_assertion: str


@dataclass(frozen=True)
class WindowCorrespondence:
    """One final-buffer scalar projected from a historical decision."""

    target_index: int
    target_section: str
    target_lag: int
    target_component: str
    source_step: int
    source_kind: str
    assertion: str


@dataclass(frozen=True)
class HistoryWindowEncoding:
    """One shared history-case base formula, reusable across all predicates."""

    namespace: str
    history_case: BufferHistoryCaseEncoding
    candidate: BufferHistoryEncoding
    steps: tuple[HistoricalStepEncoding, ...]
    sort_declarations: tuple[str, ...]
    declarations: tuple[str, ...]
    definitions: tuple[str, ...]
    step_assertions: tuple[str, ...]
    state_bridges: tuple[str, ...]
    correspondences: tuple[WindowCorrespondence, ...]
    carried_state_uids: tuple[str, ...]
    reinitialized_uids: tuple[str, ...]
    initial_state_premise: str
    reset_anchor_available: bool

    @property
    def assertions(self) -> tuple[str, ...]:
        return (
            self.candidate.interface.assertions
            + self.history_case.padding_assertions
            + self.step_assertions
            + self.state_bridges
            + tuple(item.assertion for item in self.correspondences)
        )

    @property
    def transition_copy_count(self) -> int:
        # The candidate decision plus exactly one transition per predecessor.
        return 1 + len(self.steps)

    def smt2(self, *extra_assertions: str) -> str:
        return "\n".join((
            *self.sort_declarations,
            *self.declarations,
            *self.definitions,
            *(f"(assert {formula})"
              for formula in self.assertions + tuple(extra_assertions)),
        )) + "\n"


def _validate_candidate(
    candidate: BufferHistoryEncoding,
    transition: TransitionRelationEncoding,
) -> None:
    replay = compile_buffer_history(
        candidate.interface,
        transition,
        b_obs=candidate.b_obs,
        b_act=candidate.b_act,
        namespace=candidate.namespace,
    )
    if candidate != replay:
        raise UnsupportedLoweringError(
            "history window received a mutated or noncanonical candidate buffer"
        )


def _case_steps(
    case: BufferHistoryCaseEncoding,
    history_length: int,
) -> int:
    if case.kind == "steady_state" and case.name == "steady_state" \
            and case.decision_count is None:
        return history_length
    if case.kind == "reset_prefix" \
            and case.name == f"reset_prefix_{case.decision_count}" \
            and type(case.decision_count) is int \
            and 0 <= case.decision_count < history_length:
        return case.decision_count
    raise UnsupportedLoweringError("malformed finite-history case")


def _request_exit(
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    transition: TransitionRelationEncoding,
) -> tuple[str, dict[str, str]]:
    request_states = [
        state for state in interval.exits
        if state.node_id == ir.decision_boundary.request_node
    ]
    if len(request_states) != 1:
        raise UnsupportedLoweringError(
            "history window requires one next-decision exit"
        )
    request = request_states[0]
    selectors = dict(transition.control_state_symbols)
    exit_rows = dict(transition.exit_state_terms)
    if request not in selectors or request.key not in exit_rows:
        raise UnsupportedLoweringError(
            "next-decision exit lacks selector or state terms"
        )
    values = dict(exit_rows[request.key])
    if len(values) != transition.state_term_count:
        raise UnsupportedLoweringError(
            "next-decision exit has incomplete retained state"
        )
    return selectors[request], values


def _current_observation(
    transition: TransitionRelationEncoding,
    field: str,
    scale: float,
) -> str:
    source = {
        "setPoint": "slice:shield:setPoint",
        "temperatureCelcius": "slice:shield:temperatureCelcius",
    }[field]
    boundary = dict(transition.boundary_entry_terms)
    scale_term = fp_literal(scale, NativeSort.FLOAT64)
    divided = f"(fp.div RNE {boundary[source]} {scale_term})"
    return f"((_ to_fp 8 24) RNE {divided})"


def _one_hot(executed_action: str, component: str) -> str:
    one = fp_literal(1.0, NativeSort.FLOAT32)
    zero = fp_literal(0.0, NativeSort.FLOAT32)
    return (
        f"(ite (= {executed_action} "
        f"{enum_constructor('ExecutedAction', component)}) {one} {zero})"
    )


def compile_history_window(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
    candidate_transition: TransitionRelationEncoding,
    candidate: BufferHistoryEncoding,
    *,
    history_case: str,
    namespace: str = "history-window",
) -> HistoryWindowEncoding:
    """Compile one linear reset-prefix or steady-state predecessor window."""

    if not namespace:
        raise ValueError("history-window namespace is empty")
    _validate_candidate(candidate, candidate_transition)
    try:
        selected_case = candidate.case(history_case)
    except KeyError as exc:
        raise UnsupportedLoweringError(
            f"unknown finite-history case: {history_case}"
        ) from exc
    history_length = max(candidate.b_obs, candidate.b_act)
    step_count = _case_steps(selected_case, history_length)

    steps: list[HistoricalStepEncoding] = []
    for index in range(step_count):
        step_namespace = f"{namespace}::step::{index}"
        transition = compile_transition_relation(
            slice_, ir, interval, namespace=step_namespace,
        )
        interface = compile_thermostat_interface(
            slice_, ir, interval, transition,
            namespace=step_namespace + "::interface",
        )
        request_selector, _ = _request_exit(ir, interval, transition)
        observations = tuple(
            interface.visible(
                f"next_observation.{field}.float32_bits"
            ).availability
            for field in OBSERVATION_FIELDS
        )
        continue_assertion = _and(
            interface.visible("executed_action").availability,
            *observations,
        )
        steps.append(HistoricalStepEncoding(
            index=index,
            decision_offset=index - step_count,
            transition=transition,
            interface=interface,
            request_selector=request_selector,
            continue_assertion=continue_assertion,
        ))

    state_bridges: list[str] = []
    all_state_uids = {
        storage.identity.uid for storage in slice_.storages
    }
    reinitialized = _reinitialized_uids(ir)
    if not set(reinitialized).issubset(all_state_uids):
        raise UnsupportedLoweringError(
            "history-window boundary initializer references unknown storage"
        )
    carried = tuple(sorted(all_state_uids - set(reinitialized)))
    for index, step in enumerate(steps):
        _selector, source = _request_exit(ir, interval, step.transition)
        target_transition = (
            steps[index + 1].transition
            if index + 1 < len(steps) else candidate_transition
        )
        target = dict(target_transition.boundary_entry_terms)
        if set(source) != all_state_uids or set(target) != all_state_uids:
            raise UnsupportedLoweringError(
                "historical state bridge has incomplete storage coverage"
            )
        state_bridges.extend(
            f"(= {target[uid]} {source[uid]})" for uid in carried
        )

    correspondences: list[WindowCorrespondence] = []
    observation_lags = min(step_count, candidate.b_obs)
    for lag in range(1, observation_lags + 1):
        step_index = step_count - lag
        step = steps[step_index]
        for field in OBSERVATION_FIELDS:
            target = candidate.slot("current", "past_observation", lag, field)
            source = _current_observation(
                step.transition, field, candidate.observation_scale,
            )
            correspondences.append(WindowCorrespondence(
                target.index, target.section, target.lag, target.component,
                step_index, "boundary_observation",
                f"(= {target.text} {source})",
            ))

    action_lags = min(step_count, candidate.b_act)
    for lag in range(1, action_lags + 1):
        step_index = step_count - lag
        step = steps[step_index]
        executed = step.interface.visible("executed_action").text
        for component in ACTION_COMPONENTS:
            target = candidate.slot(
                "current", "past_executed_action", lag, component,
            )
            correspondences.append(WindowCorrespondence(
                target.index, target.section, target.lag, target.component,
                step_index, "executed_action_one_hot",
                f"(= {target.text} {_one_hot(executed, component)})",
            ))

    sort_declarations = tuple(dict.fromkeys((
        *candidate.interface.sort_declarations,
        *(item for step in steps for item in step.interface.sort_declarations),
    )))
    declarations = (
        candidate.interface.declarations
        + candidate.declarations
        + tuple(item for step in steps for item in step.interface.declarations)
    )
    definitions = (
        candidate.interface.definitions
        + candidate.definitions
        + tuple(item for step in steps for item in step.interface.definitions)
    )
    step_assertions = tuple(
        assertion
        for step in steps
        for assertion in (
            *step.interface.assertions,
            step.request_selector,
            step.continue_assertion,
        )
    )

    if len(set(declarations)) != len(declarations):
        raise UnsupportedLoweringError(
            "history-window namespaces alias solver declarations"
        )
    expected_bridges = step_count * len(carried)
    if len(state_bridges) != expected_bridges:
        raise UnsupportedLoweringError(
            "history-window state bridge count mismatch"
        )
    expected_correspondences = (
        len(OBSERVATION_FIELDS) * observation_lags
        + len(ACTION_COMPONENTS) * action_lags
    )
    if len(correspondences) != expected_correspondences:
        raise UnsupportedLoweringError(
            "history-window buffer correspondence count mismatch"
        )

    return HistoryWindowEncoding(
        namespace=namespace,
        history_case=selected_case,
        candidate=candidate,
        steps=tuple(steps),
        sort_declarations=sort_declarations,
        declarations=declarations,
        definitions=definitions,
        step_assertions=step_assertions,
        state_bridges=tuple(state_bridges),
        correspondences=tuple(correspondences),
        carried_state_uids=carried,
        reinitialized_uids=reinitialized,
        initial_state_premise=INITIAL_STATE_PREMISE,
        reset_anchor_available=False,
    )


__all__ = [
    "INITIAL_STATE_PREMISE",
    "HistoricalStepEncoding",
    "HistoryWindowEncoding",
    "WindowCorrespondence",
    "compile_history_window",
]
