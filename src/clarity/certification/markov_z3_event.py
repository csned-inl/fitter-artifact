"""Checked event-local scalar SSA lowering for the thermostat proof profile.

The lowering gives every retained scalar storage distinct entry and exit
symbols, emits exact update equations for the currently supported operation
classes, and emits a frame equation for every other scalar storage. Enum and
transport operations are deliberately rejected until their profile-specific
constructors and queue semantics are attached.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .markov_ir import IREvent, NativeSort
from .markov_slice import SlicedEvent, TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_expr import SMTTerm, ThermostatExpressionCompiler, quoted_symbol, smt_sort


SCALAR_SORTS = {
    NativeSort.BOOL, NativeSort.PRESENCE, NativeSort.INT,
    NativeSort.FLOAT32, NativeSort.FLOAT64,
}
SUPPORTED_OPERATIONS = {"assign", "branch", "advance_engine_time", "set_dt"}


def _event_token(event: SlicedEvent) -> str:
    return hashlib.sha256(event.node_id.encode()).hexdigest()


@dataclass(frozen=True)
class ScalarEventEncoding:
    node_id: str
    operation: str
    namespace: str
    declarations: tuple[tuple[str, NativeSort], ...]
    updates: tuple[str, ...]
    frames: tuple[str, ...]
    branch_condition: SMTTerm | None

    @property
    def assertions(self) -> tuple[str, ...]:
        return self.updates + self.frames

    def smt2(self, *extra_assertions: str) -> str:
        lines = [
            *(f"(declare-const {name} {smt_sort(sort)})"
              for name, sort in self.declarations),
            *(f"(assert {formula})"
              for formula in self.assertions + tuple(extra_assertions)),
        ]
        return "\n".join(lines) + "\n"


def compile_scalar_event(
    slice_: TheoremSlice,
    source: IREvent,
    event: SlicedEvent,
    *,
    namespace: str = "run",
) -> ScalarEventEncoding:
    """Lower one supported source-bound event with complete scalar framing."""

    if source.node_id != event.node_id or source.operation != event.operation:
        raise UnsupportedLoweringError("source/slice event identity mismatch")
    if event.operation not in SUPPORTED_OPERATIONS:
        raise UnsupportedLoweringError(
            f"event operation has no scalar SSA lowering: {event.operation}"
        )
    if not namespace:
        raise ValueError("event namespace is empty")

    scalar = {
        storage.identity.uid: storage
        for storage in slice_.storages
        if storage.identity.native_sort in SCALAR_SORTS
    }
    writes = set(event.writes)
    if not writes.issubset(scalar):
        unsupported = sorted(writes - set(scalar))
        raise UnsupportedLoweringError(
            f"event writes a non-scalar storage: {unsupported}"
        )

    local_namespace = f"{namespace}::{_event_token(event)}"
    entry = {
        uid: quoted_symbol(local_namespace, "entry", uid) for uid in scalar
    }
    exit_ = {
        uid: quoted_symbol(local_namespace, "exit", uid) for uid in scalar
    }
    declarations = tuple(sorted(
        ((entry[uid], storage.identity.native_sort) for uid, storage in scalar.items()),
        key=lambda item: item[0],
    )) + tuple(sorted(
        ((exit_[uid], storage.identity.native_sort) for uid, storage in scalar.items()),
        key=lambda item: item[0],
    ))

    compiler = ThermostatExpressionCompiler(slice_, namespace=local_namespace)
    data = source.decoded_data()
    updates: list[str] = []
    branch_condition: SMTTerm | None = None

    if event.operation == "assign":
        if len(writes) != 1:
            raise UnsupportedLoweringError("scalar assignment must write exactly one storage")
        target = next(iter(writes))
        term = compiler.compile(data.get("expression"))
        target_sort = scalar[target].identity.native_sort
        if term.sort is not target_sort:
            raise UnsupportedLoweringError(
                f"assignment sort mismatch: {term.sort.value} -> {target_sort.value}"
            )
        updates.append(f"(= {exit_[target]} {term.text})")
    elif event.operation == "branch":
        if writes:
            raise UnsupportedLoweringError("branch unexpectedly writes scalar state")
        branch_condition = compiler.compile(data.get("condition"))
        if branch_condition.sort not in {NativeSort.BOOL, NativeSort.PRESENCE}:
            raise UnsupportedLoweringError("branch guard is not Boolean")
    elif event.operation == "set_dt":
        if writes != {"graph:dt"} or event.reads != ("slice:configured_dt",):
            raise UnsupportedLoweringError("configured-dt copy identity mismatch")
        updates.append(
            f"(= {exit_['graph:dt']} {entry['slice:configured_dt']})"
        )
    elif event.operation == "advance_engine_time":
        if writes != {"semantic:engine_time"} \
                or set(event.reads) != {"semantic:engine_time", "slice:configured_dt"}:
            raise UnsupportedLoweringError("engine-time update identity mismatch")
        updates.append(
            f"(= {exit_['semantic:engine_time']} "
            f"(fp.add RNE {entry['semantic:engine_time']} "
            f"{entry['slice:configured_dt']}))"
        )

    declared_names = {name for name, _ in declarations}
    expression_names = {name for name, _ in compiler.declarations}
    if not expression_names.issubset(declared_names):
        raise UnsupportedLoweringError("expression referenced undeclared scalar storage")
    frames = tuple(
        f"(= {exit_[uid]} {entry[uid]})"
        for uid in sorted(set(scalar) - writes)
    )
    return ScalarEventEncoding(
        node_id=event.node_id,
        operation=event.operation,
        namespace=local_namespace,
        declarations=declarations,
        updates=tuple(updates),
        frames=frames,
        branch_condition=branch_condition,
    )
