"""Finite reference semantics for the buffered-state Markov theorem.

This module is intentionally independent of Z3 and the production source
extractor.  It supplies:

* distinct typed storage identities and immutable copy-event semantics;
* the exact finite observation/action buffer convention;
* disjoint continuing, terminal, and error outcomes; and
* an exhaustive reference checker for finite deterministic systems.

For a declared finite system, successful completion is a computational proof:
the checker constructs the complete reachable augmented-state graph and tests
the deterministic quotient/congruence condition for every pair of reachable
states with the same buffer and every legal proposal.  Resource exhaustion or
an ill-formed/partial transition returns a non-proof status.

The production SMT backend must agree with this checker on finite fixtures.  It
must not use this enumerator for production models.
"""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Callable, Generic, Hashable, Iterable, Mapping, TypeVar


State = TypeVar("State", bound=Hashable)
Proposal = TypeVar("Proposal", bound=Hashable)
Executed = TypeVar("Executed", bound=Hashable)
Observation = TypeVar("Observation", bound=Hashable)


class StorageRole(str, Enum):
    PHYSICAL = "physical"
    SENSOR_HELD = "sensor_held"
    PAYLOAD_SENT = "payload_sent"
    PAYLOAD_RECEIVED = "payload_received"
    CONTROLLER_MEMORY = "controller_memory"
    ACTUATOR = "actuator"
    MACHINE_MODE = "machine_mode"
    CLOCK_OR_PHASE = "clock_or_phase"
    IMMUTABLE_PARAMETER = "immutable_parameter"
    TRANSPORT_VALID = "transport_valid"
    INTRINSIC_TERMINAL = "intrinsic_terminal"


@dataclass(frozen=True, order=True)
class StorageId:
    """Identity of one persistent source location.

    ``origin`` is provenance only.  It deliberately does not participate in
    storage lookup and never authorizes aliasing locations with different
    owner/path/role/type identities.
    """

    owner: str
    path: tuple[str, ...]
    role: StorageRole
    declared_type: str
    origin: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.owner:
            raise ValueError("storage owner must be nonempty")
        if not self.path or any(not part for part in self.path):
            raise ValueError("storage path must contain nonempty components")
        if not self.declared_type:
            raise ValueError("declared_type must be nonempty")


@dataclass(frozen=True)
class TypedStore:
    """Small immutable store used to validate copy/hold semantics."""

    cells: tuple[tuple[StorageId, Hashable], ...]

    def __post_init__(self) -> None:
        identities = [identity for identity, _ in self.cells]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate StorageId in store")

    @classmethod
    def from_mapping(cls, values: Mapping[StorageId, Hashable]) -> "TypedStore":
        return cls(tuple(sorted(values.items(), key=lambda item: item[0])))

    def read(self, identity: StorageId) -> Hashable:
        for candidate, value in self.cells:
            if candidate == identity:
                return value
        raise KeyError(identity)

    def write(self, identity: StorageId, value: Hashable) -> "TypedStore":
        values = dict(self.cells)
        if identity not in values:
            raise KeyError(identity)
        values[identity] = value
        return TypedStore.from_mapping(values)


@dataclass(frozen=True)
class CopyEvent:
    """Copy one event-time value into a distinct destination location."""

    source: StorageId
    destination: StorageId
    event_id: str

    def __post_init__(self) -> None:
        if self.source == self.destination:
            raise ValueError("copy source and destination must be distinct storage")
        if self.source.declared_type != self.destination.declared_type:
            raise ValueError("copy event requires equal declared source/destination types")
        if not self.event_id:
            raise ValueError("copy event_id must be nonempty")

    def apply(self, store: TypedStore) -> TypedStore:
        # The source value is read before the destination store is returned.
        # Later writes to the source cannot mutate the copied destination.
        value = store.read(self.source)
        return store.write(self.destination, value)


def _float_bits(value: float) -> tuple[str, int]:
    bits = int.from_bytes(struct.pack(">d", value), "big")
    return ("float64_bits", bits)


def exact_key(value: Any) -> Hashable:
    """Return a type-sensitive, IEEE-bit-sensitive immutable key.

    Python equality merges ``True`` with ``1`` and positive with negative zero.
    Neither behavior is suitable for an exact controller-interface checker.
    """

    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return _float_bits(value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value)
    if isinstance(value, StorageId):
        return (
            "storage_id",
            value.owner,
            value.path,
            value.role.value,
            value.declared_type,
        )
    if isinstance(value, Enum):
        return ("enum", type(value).__module__, type(value).__qualname__, value.name)
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            type(value).__module__,
            type(value).__qualname__,
            tuple((field.name, exact_key(getattr(value, field.name))) for field in fields(value)),
        )
    if isinstance(value, Mapping):
        items = [(exact_key(key), exact_key(item)) for key, item in value.items()]
        return ("mapping", tuple(sorted(items, key=repr)))
    if isinstance(value, tuple):
        return ("tuple", tuple(exact_key(item) for item in value))
    if isinstance(value, list):
        return ("list", tuple(exact_key(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return ("set", tuple(sorted((exact_key(item) for item in value), key=repr)))
    raise TypeError(f"unsupported exact-key value: {type(value).__name__}")


@dataclass(frozen=True)
class BufferSpec(Generic[Observation, Executed]):
    b_obs: int
    b_act: int
    observation_padding: Observation
    action_padding: Executed

    def __post_init__(self) -> None:
        if type(self.b_obs) is not int or self.b_obs < 0:
            raise ValueError("b_obs must be a nonnegative integer")
        if type(self.b_act) is not int or self.b_act < 0:
            raise ValueError("b_act must be a nonnegative integer")
        exact_key(self.observation_padding)
        exact_key(self.action_padding)


@dataclass(frozen=True)
class DecisionBuffer(Generic[Observation, Executed]):
    current_observation: Observation
    past_observations: tuple[Observation, ...]
    past_executed_actions: tuple[Executed, ...]

    @classmethod
    def initial(
        cls,
        spec: BufferSpec[Observation, Executed],
        current_observation: Observation,
    ) -> "DecisionBuffer[Observation, Executed]":
        return cls(
            current_observation=current_observation,
            past_observations=(spec.observation_padding,) * spec.b_obs,
            past_executed_actions=(spec.action_padding,) * spec.b_act,
        )

    def validate(self, spec: BufferSpec[Observation, Executed]) -> None:
        if len(self.past_observations) != spec.b_obs:
            raise ValueError("observation history length does not match BufferSpec")
        if len(self.past_executed_actions) != spec.b_act:
            raise ValueError("action history length does not match BufferSpec")
        self.key()

    def shift(
        self,
        spec: BufferSpec[Observation, Executed],
        next_observation: Observation,
        executed_action: Executed,
    ) -> "DecisionBuffer[Observation, Executed]":
        self.validate(spec)
        observations = (
            (self.current_observation,) + self.past_observations[: max(0, spec.b_obs - 1)]
            if spec.b_obs
            else ()
        )
        actions = (
            (executed_action,) + self.past_executed_actions[: max(0, spec.b_act - 1)]
            if spec.b_act
            else ()
        )
        return DecisionBuffer(next_observation, observations, actions)

    def key(self) -> Hashable:
        return (
            "decision_buffer_v1",
            exact_key(self.current_observation),
            tuple(exact_key(value) for value in self.past_observations),
            tuple(exact_key(value) for value in self.past_executed_actions),
        )


class OutcomeKind(str, Enum):
    CONTINUE = "continue"
    TERMINAL = "terminal"
    ERROR = "error"


@dataclass(frozen=True)
class StepOutcome(Generic[State, Observation]):
    """One complete first outcome from a decision boundary."""

    kind: OutcomeKind
    visible: tuple[Hashable, ...] = ()
    next_state: State | None = None
    next_observation: Observation | None = None

    def __post_init__(self) -> None:
        if self.kind is OutcomeKind.CONTINUE:
            if self.next_state is None or self.next_observation is None:
                raise ValueError("continue outcome requires next state and observation")
        elif self.next_state is not None or self.next_observation is not None:
            raise ValueError("terminal/error outcome cannot carry a continuing state")
        exact_key(self.visible)


class TransitionBlocked(RuntimeError):
    """Raised by a fixture when a purported decision transition has no outcome."""


@dataclass(frozen=True)
class FiniteDeterministicSystem(Generic[State, Proposal, Executed, Observation]):
    """Finite formal system consumed by the exhaustive reference checker."""

    name: str
    state_domain: tuple[State, ...]
    reset_states: tuple[State, ...]
    proposal_domain: tuple[Proposal, ...]
    executed_action_domain: tuple[Executed, ...]
    buffer_spec: BufferSpec[Observation, Executed]
    observe: Callable[[State], Observation]
    availability: Callable[[State, DecisionBuffer[Observation, Executed]], Iterable[Proposal]]
    execute: Callable[[State, DecisionBuffer[Observation, Executed], Proposal], Executed]
    step: Callable[
        [State, DecisionBuffer[Observation, Executed], Proposal, Executed],
        StepOutcome[State, Observation],
    ]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("system name must be nonempty")
        for label, values in (
            ("state_domain", self.state_domain),
            ("reset_states", self.reset_states),
            ("proposal_domain", self.proposal_domain),
            ("executed_action_domain", self.executed_action_domain),
        ):
            if not values:
                raise ValueError(f"{label} must be nonempty")
            keys = [exact_key(value) for value in values]
            if len(set(keys)) != len(keys):
                raise ValueError(f"{label} contains exact-value duplicates")
        state_keys = {exact_key(value) for value in self.state_domain}
        if any(exact_key(value) not in state_keys for value in self.reset_states):
            raise ValueError("reset state is outside state_domain")


@dataclass(frozen=True)
class AugmentedState(Generic[State, Observation, Executed]):
    source_state: State
    buffer: DecisionBuffer[Observation, Executed]

    def key(self) -> Hashable:
        return ("augmented_state_v1", exact_key(self.source_state), self.buffer.key())


class ProofStatus(str, Enum):
    PROVED = "proved"
    COUNTEREXAMPLE = "counterexample"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class MarkovWitness(Generic[State, Proposal, Executed, Observation]):
    reason: str
    buffer: DecisionBuffer[Observation, Executed]
    left_state: State
    right_state: State
    proposal: Proposal | None
    left_value: Hashable
    right_value: Hashable


@dataclass(frozen=True)
class FiniteProofResult(Generic[State, Proposal, Executed, Observation]):
    status: ProofStatus
    method: str
    reachable_augmented_states: int
    checked_transitions: int
    checked_pairs: int
    witness: MarkovWitness[State, Proposal, Executed, Observation] | None = None
    diagnostic: str | None = None

    @property
    def proved(self) -> bool:
        return self.status is ProofStatus.PROVED


@dataclass(frozen=True)
class _TransitionRecord(Generic[State, Executed, Observation]):
    executed_action: Executed
    outcome: StepOutcome[State, Observation]
    next_buffer: DecisionBuffer[Observation, Executed] | None

    def key(self) -> Hashable:
        return (
            exact_key(self.executed_action),
            self.outcome.kind.value,
            exact_key(self.outcome.visible),
            None if self.next_buffer is None else self.next_buffer.key(),
        )


def _outcome_full_key(outcome: StepOutcome[Any, Any]) -> Hashable:
    return (
        outcome.kind.value,
        exact_key(outcome.visible),
        exact_key(outcome.next_state),
        exact_key(outcome.next_observation),
    )


def _domain_map(values: Iterable[Any]) -> dict[Hashable, Any]:
    return {exact_key(value): value for value in values}


def prove_finite_buffer_markov(
    system: FiniteDeterministicSystem[State, Proposal, Executed, Observation],
    *,
    max_augmented_states: int = 100_000,
    max_transitions: int = 1_000_000,
) -> FiniteProofResult[State, Proposal, Executed, Observation]:
    """Exhaustively prove or refute the finite deterministic quotient property.

    Limits are operational only.  Reaching either limit returns ``INCOMPLETE``;
    it can never return ``PROVED`` from a truncated graph.
    """

    if type(max_augmented_states) is not int or max_augmented_states <= 0:
        raise ValueError("max_augmented_states must be a positive integer")
    if type(max_transitions) is not int or max_transitions <= 0:
        raise ValueError("max_transitions must be a positive integer")

    state_domain = _domain_map(system.state_domain)
    proposal_domain = _domain_map(system.proposal_domain)
    executed_domain = _domain_map(system.executed_action_domain)
    reachable: dict[Hashable, AugmentedState[State, Observation, Executed]] = {}
    transitions: dict[tuple[Hashable, Hashable], _TransitionRecord[State, Executed, Observation]] = {}
    availability_by_state: dict[Hashable, dict[Hashable, Proposal]] = {}
    pending: deque[AugmentedState[State, Observation, Executed]] = deque()

    def invalid(message: str) -> FiniteProofResult[State, Proposal, Executed, Observation]:
        return FiniteProofResult(
            ProofStatus.INVALID,
            "exhaustive_finite_reachability_and_pairwise_congruence_v1",
            len(reachable),
            len(transitions),
            0,
            diagnostic=message,
        )

    def add(candidate: AugmentedState[State, Observation, Executed]) -> bool:
        candidate.buffer.validate(system.buffer_spec)
        key = candidate.key()
        if key in reachable:
            return True
        if len(reachable) >= max_augmented_states:
            return False
        reachable[key] = candidate
        pending.append(candidate)
        return True

    try:
        for reset in system.reset_states:
            observation = system.observe(reset)
            initial = DecisionBuffer.initial(system.buffer_spec, observation)
            if not add(AugmentedState(reset, initial)):
                return FiniteProofResult(
                    ProofStatus.INCOMPLETE,
                    "exhaustive_finite_reachability_and_pairwise_congruence_v1",
                    len(reachable),
                    len(transitions),
                    0,
                    diagnostic="augmented-state limit reached during reset expansion",
                )

        while pending:
            augmented = pending.popleft()
            available = tuple(system.availability(augmented.source_state, augmented.buffer))
            available_map = _domain_map(available)
            if len(available_map) != len(available):
                return invalid("availability contains exact-value duplicates")
            if not available:
                return invalid("decision state has no legal policy proposal")
            if any(key not in proposal_domain for key in available_map):
                return invalid("availability contains proposal outside proposal_domain")
            repeated_available = tuple(system.availability(
                augmented.source_state, augmented.buffer
            ))
            repeated_map = _domain_map(repeated_available)
            if set(repeated_map) != set(available_map):
                return invalid("availability is not deterministic")
            availability_by_state[augmented.key()] = available_map

            for proposal_key, proposal in available_map.items():
                if len(transitions) >= max_transitions:
                    return FiniteProofResult(
                        ProofStatus.INCOMPLETE,
                        "exhaustive_finite_reachability_and_pairwise_congruence_v1",
                        len(reachable),
                        len(transitions),
                        0,
                        diagnostic="transition limit reached before fixed point",
                    )
                executed = system.execute(augmented.source_state, augmented.buffer, proposal)
                if exact_key(executed) not in executed_domain:
                    return invalid("execute returned action outside executed_action_domain")
                repeated_executed = system.execute(
                    augmented.source_state, augmented.buffer, proposal
                )
                if exact_key(repeated_executed) != exact_key(executed):
                    return invalid("execute is not deterministic")
                outcome = system.step(augmented.source_state, augmented.buffer, proposal, executed)
                if not isinstance(outcome, StepOutcome):
                    return invalid("step did not return StepOutcome")
                repeated_outcome = system.step(
                    augmented.source_state, augmented.buffer, proposal, executed
                )
                if not isinstance(repeated_outcome, StepOutcome):
                    return invalid("repeated step did not return StepOutcome")
                if _outcome_full_key(repeated_outcome) != _outcome_full_key(outcome):
                    return invalid("step is not deterministic")

                next_buffer = None
                if outcome.kind is OutcomeKind.CONTINUE:
                    state_key = exact_key(outcome.next_state)
                    if state_key not in state_domain:
                        return invalid("continue outcome returned state outside state_domain")
                    observed = system.observe(outcome.next_state)  # type: ignore[arg-type]
                    if exact_key(observed) != exact_key(outcome.next_observation):
                        return invalid("continue outcome observation disagrees with observe(next_state)")
                    next_buffer = augmented.buffer.shift(
                        system.buffer_spec,
                        outcome.next_observation,  # type: ignore[arg-type]
                        executed,
                    )
                    if not add(AugmentedState(outcome.next_state, next_buffer)):  # type: ignore[arg-type]
                        return FiniteProofResult(
                            ProofStatus.INCOMPLETE,
                            "exhaustive_finite_reachability_and_pairwise_congruence_v1",
                            len(reachable),
                            len(transitions),
                            0,
                            diagnostic="augmented-state limit reached before fixed point",
                        )
                transitions[(augmented.key(), proposal_key)] = _TransitionRecord(
                    executed,
                    outcome,
                    next_buffer,
                )
    except TransitionBlocked as exc:
        return invalid(f"blocked/non-total decision transition: {exc}")
    except Exception as exc:  # Fail closed for malformed formal fixtures.
        return invalid(f"formal-system evaluation failed: {type(exc).__name__}: {exc}")

    groups: dict[Hashable, list[AugmentedState[State, Observation, Executed]]] = {}
    for augmented in reachable.values():
        groups.setdefault(augmented.buffer.key(), []).append(augmented)

    checked_pairs = 0
    try:
        for group in groups.values():
            for left_index, left in enumerate(group):
                for right in group[left_index:]:
                    checked_pairs += 1
                    left_available = availability_by_state[left.key()]
                    right_available = availability_by_state[right.key()]
                    if set(left_available) != set(right_available):
                        return FiniteProofResult(
                            ProofStatus.COUNTEREXAMPLE,
                            "exhaustive_finite_reachability_and_pairwise_congruence_v1",
                            len(reachable),
                            len(transitions),
                            checked_pairs,
                            MarkovWitness(
                                "action_availability_difference",
                                left.buffer,
                                left.source_state,
                                right.source_state,
                                None,
                                tuple(sorted(left_available, key=repr)),
                                tuple(sorted(right_available, key=repr)),
                            ),
                        )
                    for proposal_key in left_available:
                        left_record = transitions[(left.key(), proposal_key)]
                        right_record = transitions[(right.key(), proposal_key)]
                        if left_record.key() != right_record.key():
                            return FiniteProofResult(
                                ProofStatus.COUNTEREXAMPLE,
                                "exhaustive_finite_reachability_and_pairwise_congruence_v1",
                                len(reachable),
                                len(transitions),
                                checked_pairs,
                                MarkovWitness(
                                    "visible_outcome_difference",
                                    left.buffer,
                                    left.source_state,
                                    right.source_state,
                                    left_available[proposal_key],
                                    left_record.key(),
                                    right_record.key(),
                                ),
                            )
    except Exception as exc:
        return invalid(f"pairwise congruence check failed: {type(exc).__name__}: {exc}")

    return FiniteProofResult(
        ProofStatus.PROVED,
        "exhaustive_finite_reachability_and_pairwise_congruence_v1",
        len(reachable),
        len(transitions),
        checked_pairs,
        diagnostic=(
            "complete finite reachable graph; every equal-buffer reachable pair "
            "has equal availability, executed action, outcome constructor, visible "
            "output, and next buffer for every legal proposal"
        ),
    )


__all__ = [
    "AugmentedState",
    "BufferSpec",
    "CopyEvent",
    "DecisionBuffer",
    "FiniteDeterministicSystem",
    "FiniteProofResult",
    "MarkovWitness",
    "OutcomeKind",
    "ProofStatus",
    "StepOutcome",
    "StorageId",
    "StorageRole",
    "TransitionBlocked",
    "TypedStore",
    "exact_key",
    "prove_finite_buffer_markov",
]
