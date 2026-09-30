"""Checked thermostat relevance closure over the finite decision interval.

The slice contains only native scalar/enum/presence state.  Legacy lookup
candidates and runtime containers are either projected to typed identities or
accounted for by replayable reduction records.  This module still does not emit
solver formulas or establish the final Markov theorem.
"""

from __future__ import annotations

from dataclasses import dataclass

from .markov_interval import FiniteDecisionInterval
from .markov_ir import IRStorage, MarkovIR, NativeSort, StorageId, StorageLayer, fingerprint


SLICE_SCHEMA = "clarity.markov-relevance-slice"
SLICE_VERSION = 1

_FORBIDDEN_SORTS = {NativeSort.RECORD, NativeSort.QUEUE, NativeSort.STACK}
_CONTROL_OPERATIONS = {
    "accept_copy", "advance_engine_time", "branch", "call_machine",
    "check_all_requirements", "completion_test", "decision", "enter_machine",
    "finish_machine_transition", "machine_from_state", "match_trigger", "outcome",
    "return", "send_copy", "solve_source_constraints",
}


@dataclass(frozen=True)
class SlicedEvent:
    node_id: str
    operation: str
    source_witness: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class VisibleTerm:
    name: str
    native_sort: NativeSort


@dataclass(frozen=True)
class ReductionRecord:
    rule: str
    source_terms: tuple[str, ...]
    result_terms: tuple[str, ...]
    premises: tuple[str, ...]
    local_check: str
    source_locations: tuple[str, ...]
    direction: str = "source_behaviors_subset_result"


@dataclass(frozen=True)
class TheoremSlice:
    ir_sha256: str
    interval_sha256: str
    storages: tuple[IRStorage, ...]
    events: tuple[SlicedEvent, ...]
    visible_terms: tuple[VisibleTerm, ...]
    reductions: tuple[ReductionRecord, ...]
    seed_terms: tuple[str, ...]
    forbidden_sort_count: int
    schema: str = SLICE_SCHEMA
    version: int = SLICE_VERSION

    def __post_init__(self) -> None:
        if self.schema != SLICE_SCHEMA or self.version != SLICE_VERSION:
            raise ValueError("unsupported relevance-slice schema/version")
        uids = [storage.identity.uid for storage in self.storages]
        if len(uids) != len(set(uids)):
            raise ValueError("duplicate sliced storage uid")
        if any(storage.identity.native_sort in _FORBIDDEN_SORTS for storage in self.storages):
            raise ValueError("generic runtime sort entered theorem slice")
        if self.forbidden_sort_count != 0:
            raise ValueError("theorem slice records a forbidden sort")


def _refs(value) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("storage"), str):
            result.add(value["storage"])
        for child in value.values():
            result.update(_refs(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_refs(child))
    return result


def _synthetic_storage(uid: str, role: str, declared: str, sort: NativeSort) -> IRStorage:
    return IRStorage(StorageId(
        uid=uid,
        owner="controller_interface",
        path=tuple(uid.removeprefix("slice:").split(":")),
        role=role,
        declared_type=declared,
        native_sort=sort,
        layer=StorageLayer.SEMANTIC,
    ))


def _interface_storages(ir: MarkovIR) -> tuple[IRStorage, ...]:
    values = [
        _synthetic_storage("slice:configured_dt", "immutable_parameter", "Real", NativeSort.FLOAT64),
        _synthetic_storage("slice:policy_proposal", "controller_memory", "PolicyProposal", NativeSort.ENUM),
        _synthetic_storage("slice:executed_action", "controller_memory", "ExecutedAction", NativeSort.ENUM),
        _synthetic_storage("slice:shield:setPoint", "controller_memory", "Real", NativeSort.FLOAT64),
        _synthetic_storage("slice:shield:temperatureCelcius", "controller_memory", "Real", NativeSort.FLOAT64),
        _synthetic_storage("slice:shield:done", "intrinsic_terminal", "Boolean", NativeSort.BOOL),
    ]
    for name in ir.required_properties:
        slug = name.replace(" ", "_").lower()
        values.extend((
            _synthetic_storage(f"slice:property:{slug}:status", "controller_memory",
                               "PropertyStatus", NativeSort.ENUM),
            _synthetic_storage(f"slice:property:{slug}:error", "controller_memory",
                               "PropertyError", NativeSort.ENUM),
        ))
    return tuple(values)


def _visible_terms(ir: MarkovIR) -> tuple[VisibleTerm, ...]:
    terms = [
        VisibleTerm("executed_action", NativeSort.ENUM),
        VisibleTerm("outcome_constructor", NativeSort.ENUM),
        VisibleTerm("next_observation.setPoint.float32_bits", NativeSort.FLOAT32),
        VisibleTerm("next_observation.temperatureCelcius.float32_bits", NativeSort.FLOAT32),
        VisibleTerm("next_buffer_shift", NativeSort.ENUM),
        VisibleTerm("reward", NativeSort.FLOAT64),
        VisibleTerm("elapsed_ticks", NativeSort.INT),
        VisibleTerm("elapsed_time", NativeSort.FLOAT64),
        VisibleTerm("intrinsic_termination", NativeSort.BOOL),
        VisibleTerm("execution_error", NativeSort.ENUM),
        VisibleTerm("action_availability_mask", NativeSort.INT),
    ]
    for name in ir.required_properties:
        terms.append(VisibleTerm("property_status:" + name, NativeSort.ENUM))
        terms.append(VisibleTerm("property_error:" + name, NativeSort.ENUM))
    return tuple(terms)


def _projection(ir: MarkovIR):
    storage = {item.identity.uid: item for item in ir.storages}
    semantic_by_key: dict[str, list[str]] = {}
    for item in ir.storages:
        if item.identity.layer is StorageLayer.SEMANTIC:
            for key in item.graph_keys:
                semantic_by_key.setdefault(key, []).append(item.identity.uid)
    direct_source = {
        item.graph_keys[0]: item.identity.uid for item in ir.storages
        if item.identity.layer is StorageLayer.GRAPH
        and item.identity.role == "source_storage"
    }
    special = {
        "$configured_dt": ("slice:configured_dt",),
        "$executed_action": ("slice:executed_action",),
        "$engine_time": ("semantic:engine_time",),
        "$pending_completion": ("semantic:latched_completion",),
        "$machine:system::ac": ("semantic:ac_mode",),
        "$machine:system::heater": ("semantic:heater_mode",),
        "$mailbox:system::controller::thermometerPort": (
            "semantic:sent_temperature_payload", "semantic:thermometer_payload_present",
        ),
        "$mailbox:system::controller::thermometerPort::reading": (
            "semantic:sent_temperature_payload", "semantic:thermometer_payload_present",
        ),
    }

    def map_key(key: str) -> tuple[str, ...]:
        if key in special:
            return special[key]
        if key in semantic_by_key:
            return tuple(semantic_by_key[key])
        if key in direct_source:
            return (direct_source[key],)
        return ()

    return storage, semantic_by_key, direct_source, special, map_key


def _event_dependencies(ir: MarkovIR, interval: FiniteDecisionInterval, map_key):
    events = {event.node_id: event for event in ir.events}
    interval_nodes = {state.node_id for state in interval.states}
    result = {}
    for node_id in interval_nodes:
        event = events[node_id]
        exact_reads = _refs(event.decoded_data())
        exact_reads.update(
            access.storage_uid.removeprefix("graph:")
            for access in event.graph_reads
            if access.storage_uid.removeprefix("graph:").startswith("$")
            or access.storage_uid.removeprefix("graph:").startswith("system::")
        )
        exact_writes = {
            access.storage_uid.removeprefix("graph:") for access in event.graph_writes
            if access.storage_uid.removeprefix("graph:").startswith("$")
            or access.storage_uid.removeprefix("graph:").startswith("system::")
            or access.storage_uid == "graph:dt"
        }
        reads = {uid for key in exact_reads for uid in map_key(key)}
        writes = {uid for key in exact_writes for uid in map_key(key)}
        reads.update(access.storage_uid for access in event.semantic_reads)
        writes.update(access.storage_uid for access in event.semantic_writes)
        if event.operation == "check_all_requirements":
            for name in ir.required_properties:
                slug = name.replace(" ", "_").lower()
                writes.update((f"slice:property:{slug}:status",
                               f"slice:property:{slug}:error"))
        result[node_id] = (reads, writes)
    return events, interval_nodes, result


def build_thermostat_relevance_slice(
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
) -> TheoremSlice:
    storage, semantic_by_key, direct_source, special, map_key = _projection(ir)
    synthetic = _interface_storages(ir)
    all_storage = dict(storage)
    all_storage.update((item.identity.uid, item) for item in synthetic)
    events, interval_nodes, dependencies = _event_dependencies(ir, interval, map_key)

    request = events[ir.decision_boundary.request_node]
    seeds = {uid for key in _refs(request.decoded_data()) for uid in map_key(key)}
    seeds.update({
        "slice:configured_dt", "slice:policy_proposal", "slice:executed_action",
        "slice:shield:setPoint", "slice:shield:temperatureCelcius", "slice:shield:done",
        "semantic:latched_completion", "semantic:engine_time",
    })
    for name in ir.required_properties:
        slug = name.replace(" ", "_").lower()
        seeds.update((f"slice:property:{slug}:status", f"slice:property:{slug}:error"))

    relevant = set(seeds)
    kept = {node_id for node_id in interval_nodes
            if events[node_id].operation in _CONTROL_OPERATIONS}
    for node_id in kept:
        relevant.update(dependencies[node_id][0])
    changed = True
    while changed:
        changed = False
        for node_id, (reads, writes) in dependencies.items():
            if writes.intersection(relevant):
                if node_id not in kept:
                    kept.add(node_id); changed = True
                old = len(relevant); relevant.update(reads)
                if len(relevant) != old:
                    changed = True

    missing = relevant - set(all_storage)
    if missing:
        raise ValueError(f"relevance closure produced undeclared terms: {sorted(missing)}")
    retained_storage = tuple(sorted(
        (all_storage[uid] for uid in relevant), key=lambda item: item.identity.uid
    ))
    if any(item.identity.native_sort in _FORBIDDEN_SORTS for item in retained_storage):
        raise ValueError("generic runtime storage survived relevance closure")

    sliced_events = tuple(sorted((
        SlicedEvent(
            node_id=node_id,
            operation=events[node_id].operation,
            source_witness=events[node_id].source_event or "runtime:" + node_id,
            reads=tuple(sorted(dependencies[node_id][0].intersection(relevant))),
            writes=tuple(sorted(dependencies[node_id][1].intersection(relevant))),
            reason=("control_or_outcome_dependency" if events[node_id].operation in _CONTROL_OPERATIONS
                    else "backward_data_dependency"),
        ) for node_id in kept
    ), key=lambda event: event.node_id))

    reductions: list[ReductionRecord] = []
    retained_uids = {item.identity.uid for item in retained_storage}
    for item in ir.storages:
        uid = item.identity.uid
        if uid in retained_uids:
            continue
        targets = tuple(sorted({mapped for key in item.graph_keys for mapped in map_key(key)}
                               .intersection(retained_uids)))
        if item.identity.layer is StorageLayer.SEMANTIC:
            rule = "dead_semantic_storage"
        elif item.identity.role == "lookup_candidate":
            rule = "resolve_lookup_candidate_or_remove"
        elif item.identity.native_sort in _FORBIDDEN_SORTS:
            rule = "lower_runtime_container"
        elif targets:
            rule = "exact_storage_projection"
        else:
            rule = "dead_native_storage"
        reductions.append(ReductionRecord(
            rule=rule,
            source_terms=(uid,),
            result_terms=targets,
            premises=("complete backward data/control closure",),
            local_check="replayed_against_interval_event_reads_and_writes",
            source_locations=item.graph_keys or (uid,),
        ))
    for node_id in sorted(interval_nodes - kept):
        reductions.append(ReductionRecord(
            rule="dead_event_after_dependency_closure",
            source_terms=(node_id,),
            result_terms=(),
            premises=("writes no relevant term", "not a control/outcome operation"),
            local_check="event_write_set_disjoint_from_fixed_point",
            source_locations=((events[node_id].source_event or "runtime:" + node_id),),
        ))

    return TheoremSlice(
        ir_sha256=fingerprint(ir.to_dict(include_fingerprint=False)),
        interval_sha256=fingerprint(interval),
        storages=retained_storage,
        events=sliced_events,
        visible_terms=_visible_terms(ir),
        reductions=tuple(reductions),
        seed_terms=tuple(sorted(seeds)),
        forbidden_sort_count=0,
    )


def slice_metrics(slice_: TheoremSlice, ir: MarkovIR,
                  interval: FiniteDecisionInterval) -> dict[str, int]:
    return {
        "ir_storages": len(ir.storages),
        "interval_control_states": len(interval.states),
        "interval_source_nodes": len({state.node_id for state in interval.states}),
        "retained_storages": len(slice_.storages),
        "retained_events": len(slice_.events),
        "visible_terms": len(slice_.visible_terms),
        "reduction_records": len(slice_.reductions),
        "forbidden_sorts": slice_.forbidden_sort_count,
    }


def validate_thermostat_relevance_slice(
    slice_: TheoremSlice,
    ir: MarkovIR,
    interval: FiniteDecisionInterval,
) -> list[str]:
    """Replay closure and coverage without trusting recorded reductions."""

    errors: list[str] = []
    expected_ir_sha = fingerprint(ir.to_dict(include_fingerprint=False))
    expected_interval_sha = fingerprint(interval)
    if slice_.ir_sha256 != expected_ir_sha:
        errors.append("slice IR identity mismatch")
    if slice_.interval_sha256 != expected_interval_sha:
        errors.append("slice interval identity mismatch")

    # Reconstruct the fixed point without invoking the slice builder.
    source_storage, _, _, _, map_key = _projection(ir)
    interface = _interface_storages(ir)
    universe = dict(source_storage)
    universe.update((item.identity.uid, item) for item in interface)
    source_events, interval_nodes, dependency = _event_dependencies(ir, interval, map_key)
    request = source_events[ir.decision_boundary.request_node]
    seed = {uid for key in _refs(request.decoded_data()) for uid in map_key(key)}
    seed.update({
        "slice:configured_dt", "slice:policy_proposal", "slice:executed_action",
        "slice:shield:setPoint", "slice:shield:temperatureCelcius", "slice:shield:done",
        "semantic:latched_completion", "semantic:engine_time",
    })
    for name in ir.required_properties:
        slug = name.replace(" ", "_").lower()
        seed.update((f"slice:property:{slug}:status", f"slice:property:{slug}:error"))
    relevant = set(seed)
    required_events = {node_id for node_id in interval_nodes
                       if source_events[node_id].operation in _CONTROL_OPERATIONS}
    for node_id in required_events:
        relevant.update(dependency[node_id][0])
    while True:
        old_state = (frozenset(relevant), frozenset(required_events))
        for node_id in sorted(interval_nodes):
            reads, writes = dependency[node_id]
            if writes & relevant:
                relevant.update(reads); required_events.add(node_id)
        if old_state == (frozenset(relevant), frozenset(required_events)):
            break

    expected_storage = tuple(sorted(
        (universe[uid] for uid in relevant), key=lambda item: item.identity.uid
    ))
    if slice_.storages != expected_storage:
        errors.append("slice storage relevance closure mismatch")
    actual_event_map = {event.node_id: event for event in slice_.events}
    if set(actual_event_map) != required_events:
        errors.append("slice event relevance closure mismatch")
    else:
        for node_id in sorted(required_events):
            event = actual_event_map[node_id]
            source = source_events[node_id]
            expected = (
                source.operation,
                source.source_event or "runtime:" + node_id,
                tuple(sorted(dependency[node_id][0] & relevant)),
                tuple(sorted(dependency[node_id][1] & relevant)),
                ("control_or_outcome_dependency"
                 if source.operation in _CONTROL_OPERATIONS else "backward_data_dependency"),
            )
            actual = (event.operation, event.source_witness, event.reads,
                      event.writes, event.reason)
            if actual != expected:
                errors.append(f"slice event replay mismatch: {node_id}")
    if slice_.visible_terms != _visible_terms(ir):
        errors.append("slice visible signature mismatch")
    if slice_.seed_terms != tuple(sorted(seed)):
        errors.append("slice observable seeds mismatch")

    retained_uids = {item.identity.uid for item in slice_.storages}
    expected_reduction_sources = (
        {item.identity.uid for item in ir.storages if item.identity.uid not in retained_uids}
        | (interval_nodes - required_events)
    )
    reduction_sources = [term for record in slice_.reductions for term in record.source_terms]
    if set(reduction_sources) != expected_reduction_sources or \
            len(reduction_sources) != len(expected_reduction_sources):
        errors.append("slice reduction coverage/replay mismatch")
    for record in slice_.reductions:
        source = record.source_terms[0] if len(record.source_terms) == 1 else None
        if source in interval_nodes:
            reads, writes = dependency[source]
            valid = (
                record.rule == "dead_event_after_dependency_closure"
                and source not in required_events
                and source_events[source].operation not in _CONTROL_OPERATIONS
                and not (writes & relevant)
                and not record.result_terms
            )
        elif source in source_storage:
            item = source_storage[source]
            mapped = tuple(sorted({target for key in item.graph_keys for target in map_key(key)}
                                  & retained_uids))
            valid = (
                source not in retained_uids
                and record.result_terms == mapped
                and set(record.result_terms).issubset(retained_uids)
            )
        else:
            valid = False
        if not valid:
            errors.append(f"slice reduction replay failed: {source}")
    if any(record.direction != "source_behaviors_subset_result"
           for record in slice_.reductions):
        errors.append("slice reduction has an unsound implication direction")
    forbidden = sum(item.identity.native_sort in _FORBIDDEN_SORTS
                    for item in slice_.storages)
    if forbidden or slice_.forbidden_sort_count != 0:
        errors.append("slice contains generic runtime sorts")
    return errors
