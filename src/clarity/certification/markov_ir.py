"""Immutable typed IR for finite-history controller-boundary proofs.

``MarkovIR`` is deliberately a source-bound inventory, not an executable
semantics and not a Markov certificate.  It retains every node and graph-level
storage reference until a separately checked reduction proves that an object is
irrelevant to the controller-facing theorem.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any


SCHEMA = "clarity.markov-ir"
VERSION = 1


class NativeSort(str, Enum):
    BOOL = "bool"
    INT = "int"
    FLOAT64 = "float64"
    FLOAT32 = "float32"
    ENUM = "enum"
    PRESENCE = "presence"
    RECORD = "runtime_record"
    QUEUE = "runtime_queue"
    STACK = "runtime_stack"


class StorageLayer(str, Enum):
    SEMANTIC = "semantic"
    GRAPH = "graph"


class AccessKind(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, order=True)
class StorageId:
    """One non-aliasable typed identity in the proof representation."""

    uid: str
    owner: str
    path: tuple[str, ...]
    role: str
    declared_type: str
    native_sort: NativeSort
    layer: StorageLayer

    def __post_init__(self) -> None:
        if not self.uid or not self.owner or not self.path:
            raise ValueError("storage identity is incomplete")
        if any(not component for component in self.path):
            raise ValueError("storage path contains an empty component")
        if not self.role or not self.declared_type:
            raise ValueError("storage role/type is incomplete")


@dataclass(frozen=True)
class IRStorage:
    identity: StorageId
    graph_keys: tuple[str, ...] = ()
    origin: str | None = None


@dataclass(frozen=True, order=True)
class VersionedAccess:
    """Event-local SSA access; entry is read-before-write, exit is post-write."""

    storage_uid: str
    kind: AccessKind
    version: str

    def __post_init__(self) -> None:
        if self.kind is AccessKind.READ and not self.version.endswith(":entry"):
            raise ValueError("reads must use the event entry version")
        if self.kind is AccessKind.WRITE and not self.version.endswith(":exit"):
            raise ValueError("writes must use the event exit version")


@dataclass(frozen=True)
class IREvent:
    node_id: str
    operation: str
    source_event: str | None
    source_kind: str | None
    successors: tuple[tuple[str, str], ...]
    graph_reads: tuple[VersionedAccess, ...]
    graph_writes: tuple[VersionedAccess, ...]
    semantic_reads: tuple[VersionedAccess, ...]
    semantic_writes: tuple[VersionedAccess, ...]
    data_json: str
    frame_rule: str
    on_exception: str

    def decoded_data(self) -> dict[str, Any]:
        value = json.loads(self.data_json)
        if not isinstance(value, dict):
            raise ValueError("event data must decode to an object")
        return value


@dataclass(frozen=True)
class MachineMode:
    owner: str
    storage_uid: str
    states: tuple[str, ...]


@dataclass(frozen=True)
class VisibleOutput:
    name: str
    native_sort: NativeSort
    availability: str


@dataclass(frozen=True)
class DecisionBoundary:
    request_node: str
    resume_node: str
    entry: str
    exits: tuple[str, ...]
    action_convention: str


@dataclass(frozen=True)
class LocalSimulationObligation:
    """Replayable local source-to-IR rule instance.

    A checked local instance is necessary but not sufficient for the composed
    forward-simulation theorem.  Scheduler composition remains a later gate.
    """

    node_id: str
    rule: str
    source_witness: str
    premises: tuple[str, ...]
    conclusion: str
    status: str = "locally_checked"


@dataclass(frozen=True)
class MarkovIR:
    source_path: str
    source_sha256: str
    contract_sha256: str
    storages: tuple[IRStorage, ...]
    events: tuple[IREvent, ...]
    source_events: tuple[tuple[str, str], ...]
    machine_modes: tuple[MachineMode, ...]
    visible_outputs: tuple[VisibleOutput, ...]
    local_obligations: tuple[LocalSimulationObligation, ...]
    required_properties: tuple[str, ...]
    decision_boundary: DecisionBoundary
    schema: str = SCHEMA
    version: int = VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA or self.version != VERSION:
            raise ValueError("unsupported MarkovIR schema/version")
        storage_uids = [storage.identity.uid for storage in self.storages]
        if len(storage_uids) != len(set(storage_uids)):
            raise ValueError("duplicate MarkovIR storage uid")
        node_ids = [event.node_id for event in self.events]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate MarkovIR event node")
        known = set(storage_uids)
        for event in self.events:
            for access in (*event.graph_reads, *event.graph_writes,
                           *event.semantic_reads, *event.semantic_writes):
                if access.storage_uid not in known:
                    raise ValueError(
                        f"event {event.node_id} references unknown storage {access.storage_uid}"
                    )
        declared_sources = dict(self.source_events)
        if len(declared_sources) != len(self.source_events):
            raise ValueError("duplicate source event identity")
        covered = [event.source_event for event in self.events if event.source_event]
        if sorted(covered) != sorted(declared_sources):
            raise ValueError("source events are not covered exactly once")

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        value = _json_value(asdict(self))
        if include_fingerprint:
            value["sha256"] = fingerprint(value)
        return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def native_sort(declared_type: str) -> NativeSort:
    if declared_type == "Boolean":
        return NativeSort.BOOL
    if declared_type == "Integer":
        return NativeSort.INT
    if declared_type == "Real":
        return NativeSort.FLOAT64
    if declared_type == "Float32":
        return NativeSort.FLOAT32
    if declared_type == "Presence":
        return NativeSort.PRESENCE
    if declared_type == "RuntimeRecord":
        return NativeSort.RECORD
    if declared_type == "RuntimeQueue":
        return NativeSort.QUEUE
    if declared_type == "RuntimeStack":
        return NativeSort.STACK
    return NativeSort.ENUM
