"""Exhaustive, solver-independent obligation manifest for the Markov theorem.

This module fixes what must be asked of a future SMT backend.  It never imports
Z3 and never treats an unrun, unknown, timed-out, or failed query as evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .markov_interval import FiniteDecisionInterval
from .markov_ir import NativeSort, canonical_json, fingerprint
from .markov_slice import TheoremSlice


MANIFEST_SCHEMA = "clarity.markov-obligation-manifest"
MANIFEST_VERSION = 1


class EqualitySemantics(str, Enum):
    NATIVE = "native_typed_equality"
    IEEE_BITS = "ieee_bit_equality"
    CONSTRUCTOR = "disjoint_constructor_equality"
    EXACT_SHIFT = "exact_buffer_shift_replay"


class DischargeMethod(str, Enum):
    SOLVER_UNSAT = "solver_unsat"
    STRUCTURAL_REPLAY = "structural_replay"


class ObligationStatus(str, Enum):
    NOT_RUN = "not_run"
    CHECKED = "checked"
    UNSAT = "unsat"
    SAT = "sat"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class HistoryCase:
    name: str
    kind: str
    decision_count: int | None
    history_length: int


@dataclass(frozen=True)
class DifferencePredicate:
    name: str
    visible_terms: tuple[str, ...]
    native_sort: NativeSort
    equality: EqualitySemantics
    discharge: DischargeMethod


@dataclass(frozen=True)
class QueryObligation:
    obligation_id: str
    history_case: str
    predicate: str
    required_result: str
    status: ObligationStatus = ObligationStatus.NOT_RUN
    query_sha256: str | None = None
    solver_identity: str | None = None


@dataclass(frozen=True)
class StructuralObligation:
    obligation_id: str
    history_case: str
    predicate: str
    status: ObligationStatus
    evidence: str


@dataclass(frozen=True)
class ProofPremise:
    name: str
    required: bool
    status: ObligationStatus
    evidence: str


@dataclass(frozen=True)
class ObligationManifest:
    ir_sha256: str
    interval_sha256: str
    slice_sha256: str
    b_obs: int
    b_act: int
    history_cases: tuple[HistoryCase, ...]
    difference_predicates: tuple[DifferencePredicate, ...]
    queries: tuple[QueryObligation, ...]
    structural_obligations: tuple[StructuralObligation, ...]
    premises: tuple[ProofPremise, ...]
    schema: str = MANIFEST_SCHEMA
    version: int = MANIFEST_VERSION

    def __post_init__(self) -> None:
        if self.schema != MANIFEST_SCHEMA or self.version != MANIFEST_VERSION:
            raise ValueError("unsupported obligation-manifest schema/version")
        if type(self.b_obs) is not int or type(self.b_act) is not int \
                or min(self.b_obs, self.b_act) < 0:
            raise ValueError("buffer lengths must be nonnegative integers")
        ids = [query.obligation_id for query in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate proof-obligation identity")

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        value = asdict(self)
        # canonical_json performs enum conversion; round trip gives a plain map.
        import json
        record = json.loads(canonical_json(value))
        if include_fingerprint:
            record["sha256"] = fingerprint(record)
        return record

    @property
    def certificate_ready(self) -> bool:
        return (
            all(not premise.required or premise.status is ObligationStatus.CHECKED
                for premise in self.premises)
            and all(
                query.status is ObligationStatus.UNSAT
                and query.query_sha256 is not None
                and query.solver_identity is not None
                for query in self.queries
            )
            and all(
                obligation.status is ObligationStatus.CHECKED
                and bool(obligation.evidence)
                for obligation in self.structural_obligations
            )
        )


def _equality(name: str, sort: NativeSort) -> EqualitySemantics:
    if name == "outcome_constructor":
        return EqualitySemantics.CONSTRUCTOR
    if name == "next_buffer_shift":
        return EqualitySemantics.EXACT_SHIFT
    if sort in {NativeSort.FLOAT32, NativeSort.FLOAT64}:
        return EqualitySemantics.IEEE_BITS
    return EqualitySemantics.NATIVE


def build_obligation_manifest(
    slice_: TheoremSlice,
    interval: FiniteDecisionInterval,
    *,
    b_obs: int,
    b_act: int,
) -> ObligationManifest:
    if type(b_obs) is not int or type(b_act) is not int or min(b_obs, b_act) < 0:
        raise ValueError("buffer lengths must be nonnegative integers")
    length = max(b_obs, b_act)
    cases = tuple(
        HistoryCase(f"reset_prefix_{count}", "reset_prefix", count, length)
        for count in range(length)
    ) + (HistoryCase("steady_state", "steady_state", None, length),)

    predicates = tuple(DifferencePredicate(
        name="different:" + term.name,
        visible_terms=(term.name,),
        native_sort=term.native_sort,
        equality=_equality(term.name, term.native_sort),
        discharge=(DischargeMethod.STRUCTURAL_REPLAY
                   if term.name == "next_buffer_shift"
                   else DischargeMethod.SOLVER_UNSAT),
    ) for term in slice_.visible_terms)

    queries = tuple(QueryObligation(
        obligation_id=case.name + "/" + predicate.name,
        history_case=case.name,
        predicate=predicate.name,
        required_result="unsat",
    ) for case in cases for predicate in predicates
      if predicate.discharge is DischargeMethod.SOLVER_UNSAT)
    # Progress and first-outcome exclusivity are complete query obligations,
    # not assumptions embedded in the visible-difference queries.
    queries += tuple(QueryObligation(
        obligation_id=case.name + "/" + name,
        history_case=case.name,
        predicate=name,
        required_result="unsat",
    ) for case in cases for name in (
        "progress:no_outcome_by_bound",
        "progress:multiple_first_outcomes",
        "representation:unsupported_value_or_encoding",
    ))
    structural = tuple(StructuralObligation(
        obligation_id=case.name + "/" + predicate.name,
        history_case=case.name,
        predicate=predicate.name,
        status=ObligationStatus.NOT_RUN,
        evidence="awaiting exact buffer-history lowering and replay",
    ) for case in cases for predicate in predicates
      if predicate.discharge is DischargeMethod.STRUCTURAL_REPLAY)

    premises = (
        ProofPremise("typed_source_extraction", True, ObligationStatus.CHECKED,
                     slice_.ir_sha256),
        ProofPremise("finite_first_outcome_control_interval", True,
                     ObligationStatus.CHECKED, slice_.interval_sha256),
        ProofPremise("relevance_reduction_replay", True, ObligationStatus.CHECKED,
                     fingerprint(slice_)),
        ProofPremise("exact_numeric_operator_lowering", True,
                     ObligationStatus.NOT_RUN, "awaiting native-sort semantic table"),
        ProofPremise("shield_reward_outcome_lowering", True,
                     ObligationStatus.NOT_RUN, "awaiting exact interface equations"),
        ProofPremise("reachability_inclusion", True, ObligationStatus.NOT_RUN,
                     "awaiting reset-prefix and inductive-invariant checks"),
        ProofPremise("runtime_refinement", False, ObligationStatus.NOT_RUN,
                     "not required for a formal-profile-only theorem"),
    )
    return ObligationManifest(
        ir_sha256=slice_.ir_sha256,
        interval_sha256=slice_.interval_sha256,
        slice_sha256=fingerprint(slice_),
        b_obs=b_obs,
        b_act=b_act,
        history_cases=cases,
        difference_predicates=predicates,
        queries=queries,
        structural_obligations=structural,
        premises=premises,
    )


def validate_obligation_manifest(
    manifest: ObligationManifest,
    slice_: TheoremSlice,
    interval: FiniteDecisionInterval,
) -> list[str]:
    errors: list[str] = []
    expected = build_obligation_manifest(
        slice_, interval, b_obs=manifest.b_obs, b_act=manifest.b_act
    )
    if manifest.ir_sha256 != expected.ir_sha256 \
            or manifest.interval_sha256 != expected.interval_sha256 \
            or manifest.slice_sha256 != expected.slice_sha256:
        errors.append("obligation manifest source identity mismatch")
    if manifest.history_cases != expected.history_cases:
        errors.append("history-case coverage mismatch")
    if manifest.difference_predicates != expected.difference_predicates:
        errors.append("visible-difference predicate coverage/equality mismatch")
    expected_queries = {(q.obligation_id, q.history_case, q.predicate, q.required_result)
                        for q in expected.queries}
    actual_queries = {(q.obligation_id, q.history_case, q.predicate, q.required_result)
                      for q in manifest.queries}
    if actual_queries != expected_queries or len(manifest.queries) != len(expected.queries):
        errors.append("solver query obligation coverage mismatch")
    expected_structural = {
        (item.obligation_id, item.history_case, item.predicate,
         item.status, item.evidence) for item in expected.structural_obligations
    }
    actual_structural = {
        (item.obligation_id, item.history_case, item.predicate,
         item.status, item.evidence) for item in manifest.structural_obligations
    }
    if actual_structural != expected_structural \
            or len(manifest.structural_obligations) != len(expected.structural_obligations):
        errors.append("structural obligation coverage/replay mismatch")
    expected_premises = {premise.name: premise for premise in expected.premises}
    actual_premises = {premise.name: premise for premise in manifest.premises}
    if set(actual_premises) != set(expected_premises):
        errors.append("proof-premise coverage mismatch")
    else:
        for name, premise in actual_premises.items():
            baseline = expected_premises[name]
            if (premise.required, premise.evidence) != (baseline.required, baseline.evidence):
                errors.append(f"proof-premise definition mismatch: {name}")
            if baseline.status is ObligationStatus.NOT_RUN \
                    and premise.status is ObligationStatus.CHECKED:
                errors.append(f"proof premise marked checked without evidence: {name}")
    if any(query.required_result != "unsat" for query in manifest.queries):
        errors.append("a proof query does not require UNSAT")
    if any(query.status in {ObligationStatus.SAT, ObligationStatus.UNKNOWN,
                            ObligationStatus.TIMEOUT, ObligationStatus.ERROR}
           for query in manifest.queries) and manifest.certificate_ready:
        errors.append("non-UNSAT result was accepted")
    visible = {term.name for term in slice_.visible_terms}
    covered = {term for predicate in manifest.difference_predicates
               for term in predicate.visible_terms}
    if covered != visible:
        errors.append("visible signature is not exhaustively covered")
    return errors
