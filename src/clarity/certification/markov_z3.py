"""Fail-closed Z3 query boundary for the finite-history Markov proof.

This module owns solver invocation, query hashing, result classification, and
same-query replay.  It deliberately does not yet lower a thermostat
``ObligationManifest``: production lowering remains unsupported until buffer
history, paired-run, reachability, and visible-difference lowering are checked.
Small theorem fixtures can use this boundary now without creating a false
certificate path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import importlib
import time
from typing import Any

from .markov_ir import NativeSort


QUERY_SCHEMA = "clarity.markov-z3-query-result"
QUERY_VERSION = 1
SOLVER_PIPELINE = ("simplify", "propagate-values", "solve-eqs", "smt")


class SolverStatus(str, Enum):
    UNSAT = "unsat"
    SAT = "sat"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


class UnsupportedLoweringError(ValueError):
    """The requested semantic construct has no checked lowering yet."""


@dataclass(frozen=True)
class QueryResult:
    query_sha256: str
    status: SolverStatus
    solver_identity: str | None
    duration_seconds: float
    reason: str | None = None
    model_smt2: str | None = None
    schema: str = QUERY_SCHEMA
    version: int = QUERY_VERSION

    def __post_init__(self) -> None:
        if self.schema != QUERY_SCHEMA or self.version != QUERY_VERSION:
            raise ValueError("unsupported Z3 query-result schema/version")
        if len(self.query_sha256) != 64:
            raise ValueError("query hash must be SHA-256")
        if self.duration_seconds < 0:
            raise ValueError("query duration cannot be negative")
        if self.status is SolverStatus.SAT and self.model_smt2 is None:
            raise ValueError("SAT result must retain a model for replay")
        if self.status is not SolverStatus.SAT and self.model_smt2 is not None:
            raise ValueError("only SAT results may retain a model")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


def canonical_smt2(query: str) -> str:
    """Normalize harmless whitespace and reject solver-controlled commands."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("SMT-LIB query must be a nonempty string")
    normalized = "\n".join(line.rstrip() for line in query.strip().splitlines()) + "\n"
    lowered = normalized.lower()
    forbidden = ("(check-sat", "(get-model", "(get-proof", "(exit", "(set-option")
    if any(token in lowered for token in forbidden):
        raise ValueError("query contains a solver-control command")
    return normalized


def query_sha256(query: str) -> str:
    return hashlib.sha256(canonical_smt2(query).encode()).hexdigest()


def _load_z3():
    try:
        return importlib.import_module("z3")
    except Exception as exc:  # pragma: no cover - exercised on the solver host
        raise RuntimeError(f"Z3 Python bindings unavailable: {type(exc).__name__}: {exc}") from exc


def solver_identity(z3=None) -> str:
    z3 = _load_z3() if z3 is None else z3
    if hasattr(z3, "get_full_version"):
        version = str(z3.get_full_version())
    else:
        version = "Z3 " + str(z3.get_version_string())
    return version + "; tactic=" + ">".join(SOLVER_PIPELINE)


def _new_solver(z3):
    """Build the fixed, evidence-visible solver pipeline.

    The first three tactics are exact Z3 preprocessing passes.  In
    particular, ``solve-eqs`` eliminates the large family of definitional
    SSA and state-bridge equalities before the final SMT search.  A solver
    produced from the tactic retains Z3's model converters, so SAT results
    still expose models in the original query vocabulary.
    """

    return z3.Then(*(z3.Tactic(name) for name in SOLVER_PIPELINE)).solver()


def native_z3_sort(native_sort: NativeSort, *, z3=None):
    """Map non-container native sorts to exact Z3 sorts.

    Enum constructors are profile-specific and must be supplied by the later
    checked operation table, so a bare enum request fails closed.
    """

    z3 = _load_z3() if z3 is None else z3
    if native_sort in {NativeSort.BOOL, NativeSort.PRESENCE}:
        return z3.BoolSort()
    if native_sort is NativeSort.INT:
        return z3.IntSort()
    if native_sort is NativeSort.FLOAT32:
        return z3.FPSort(8, 24)
    if native_sort is NativeSort.FLOAT64:
        return z3.FPSort(11, 53)
    if native_sort is NativeSort.ENUM:
        raise UnsupportedLoweringError("enum sort requires checked constructors")
    raise UnsupportedLoweringError(
        f"generic runtime sort is forbidden in the theorem query: {native_sort.value}"
    )


def ieee_bit_difference(left, right, *, z3=None):
    """Bit-pattern inequality for Float32/Float64 theorem-visible values."""

    z3 = _load_z3() if z3 is None else z3
    return z3.fpToIEEEBV(left) != z3.fpToIEEEBV(right)


def run_smt2_query(query: str, *, timeout_ms: int = 300_000) -> QueryResult:
    """Run one assertion-only quantifier-free query and classify every outcome."""

    canonical = canonical_smt2(query)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if type(timeout_ms) is not int or timeout_ms <= 0:
        raise ValueError("timeout_ms must be a positive integer")
    started = time.perf_counter()
    try:
        z3 = _load_z3()
        identity = solver_identity(z3)
        expressions = z3.parse_smt2_string(canonical)
        solver = _new_solver(z3)
        solver.set(timeout=timeout_ms)
        solver.add(*list(expressions))
        answer = solver.check()
        duration = time.perf_counter() - started
        if answer == z3.unsat:
            return QueryResult(digest, SolverStatus.UNSAT, identity, duration)
        if answer == z3.sat:
            return QueryResult(
                digest, SolverStatus.SAT, identity, duration,
                model_smt2=solver.model().sexpr(),
            )
        reason = solver.reason_unknown() or "solver returned unknown"
        status = SolverStatus.TIMEOUT if any(
            marker in reason.lower() for marker in ("timeout", "canceled", "resource")
        ) else SolverStatus.UNKNOWN
        return QueryResult(digest, status, identity, duration, reason=reason)
    except Exception as exc:
        return QueryResult(
            digest, SolverStatus.ERROR, None, time.perf_counter() - started,
            reason=f"{type(exc).__name__}: {exc}",
        )


def replay_query(query: str, expected: QueryResult, *, timeout_ms: int = 300_000) -> QueryResult:
    """Rerun exactly the hashed query; mismatched input fails before solver use."""

    if query_sha256(query) != expected.query_sha256:
        raise ValueError("replay query hash does not match the recorded result")
    if expected.status not in {SolverStatus.SAT, SolverStatus.UNSAT}:
        raise ValueError("unknown, timeout, and error results are not replayable evidence")
    replayed = run_smt2_query(query, timeout_ms=timeout_ms)
    if replayed.status is not expected.status:
        raise ValueError(
            f"replay result mismatch: recorded {expected.status.value}, "
            f"replayed {replayed.status.value}"
        )
    if replayed.solver_identity != expected.solver_identity:
        raise ValueError("replay solver identity does not match the recorded result")
    return replayed


def compile_manifest_obligation(*_args, **_kwargs):
    """Reserved production boundary; no obligation is silently approximated."""

    raise UnsupportedLoweringError(
        "thermostat obligation lowering awaits checked history, paired-run, "
        "reachability, and visible-difference relations"
    )
