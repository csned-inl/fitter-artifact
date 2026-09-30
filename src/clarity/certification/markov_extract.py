"""Thermostat-only source extraction into immutable typed ``MarkovIR``.

This extractor is intentionally narrow.  It records the complete existing
decision-transition graph and adds semantic storage witnesses from the frozen
controller-step contract.  It does not slice the graph or claim a proof.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .markov_contract import load_controller_step_contract, repository_root
from .markov_ir import (
    AccessKind,
    DecisionBoundary,
    IREvent,
    IRStorage,
    LocalSimulationObligation,
    MachineMode,
    MarkovIR,
    NativeSort,
    StorageId,
    StorageLayer,
    VersionedAccess,
    VisibleOutput,
    canonical_json,
    native_sort,
)


_CONTROL_TYPES = {
    "$configured_dt": "Real",
    "$engine_time": "Real",
    "$executed_action": "ExecutedAction",
    "$initial_overrides": "RuntimeRecord",
    "$inputs": "RuntimeRecord",
    "$locals": "RuntimeRecord",
    "$machine_frame": "RuntimeRecord",
    "$machines": "RuntimeRecord",
    "$mailboxes": "RuntimeRecord",
    "$pending_completion": "Boolean",
    "$requirement_ledger": "RuntimeRecord",
    "$stack": "RuntimeStack",
    "$state": "RuntimeRecord",
}


def _graph_declared_type(key: str, declared: str | None) -> str:
    if declared:
        return declared
    if key in _CONTROL_TYPES:
        return _CONTROL_TYPES[key]
    if key.startswith("$machine:"):
        return "MachineMode"
    if key.startswith("$mailbox:"):
        return "RuntimeQueue"
    lowered = key.lower()
    boolean_markers = (
        "acon", "heateron", "policycall.acstate", "policycall::acstate",
        "policycall.heaterstate", "policycall::heaterstate",
        "behavior.on", "behavior.reset",
    )
    if any(marker in lowered for marker in boolean_markers):
        return "Boolean"
    # Every remaining unresolved thermostat lookup candidate names a Real
    # attribute.  The independent validator reconstructs and checks this rule.
    return "Real"


def _graph_role(key: str, declared: str | None) -> str:
    if key.startswith("$mailbox:"):
        return "runtime_transport_container"
    if key.startswith("$machine:"):
        return "runtime_machine_mode"
    if key.startswith("$"):
        return "runtime_control"
    if declared:
        return "source_storage"
    return "lookup_candidate"


def _semantic_bindings(contract: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for record in contract["storage"]:
        uid = "semantic:" + record["name"]
        for key in (record.get("source_storage"), record.get("graph_storage")):
            if key:
                result.setdefault(key, []).append(uid)
    return {key: tuple(values) for key, values in result.items()}


def _semantic_accesses(
    node_id: str,
    operation: str,
    keys: list[str],
    bindings: Mapping[str, tuple[str, ...]],
    kind: AccessKind,
) -> tuple[VersionedAccess, ...]:
    selected: set[str] = set()
    for key in keys:
        candidates = bindings.get(key, ())
        if key == "$mailbox:system::controller::thermometerPort":
            # Payload value and validity are distinct semantic cells even though
            # the legacy graph stores both in one runtime mailbox object.
            if operation == "send_copy":
                if kind is AccessKind.WRITE:
                    candidates = tuple(uid for uid in candidates if uid.endswith(
                        ("sent_temperature_payload", "thermometer_payload_present")
                    ))
                else:
                    candidates = ()
            elif operation == "accept_copy":
                if kind is AccessKind.READ:
                    candidates = tuple(uid for uid in candidates if uid.endswith(
                        ("sent_temperature_payload", "thermometer_payload_present")
                    ))
                else:
                    candidates = tuple(uid for uid in candidates if uid.endswith(
                        "thermometer_payload_present"
                    ))
        selected.update(candidates)
    # Accept consumes validity; the received value is captured by the ordinary
    # source-storage write in the same node.
    if node_id == "system::controller/step/2" and kind is AccessKind.WRITE:
        selected.add("semantic:thermometer_payload_present")
    suffix = "entry" if kind is AccessKind.READ else "exit"
    return tuple(
        VersionedAccess(uid, kind, f"{node_id}:{suffix}")
        for uid in sorted(selected)
    )


def _local_obligation(node_id: str, node: Mapping[str, Any]) -> LocalSimulationObligation:
    operation = node["operation"]
    supported = {
        "accept_copy", "advance_engine_time", "apply_executed_action", "assign",
        "begin_cycle", "branch", "call_machine", "check_all_requirements",
        "completion_test", "decision", "enter_block", "enter_machine",
        "finish_machine_transition", "initialize_existing_runtime",
        "machine_from_state", "match_trigger", "no_op", "outcome", "return",
        "send_copy", "set_dt", "solve_source_constraints",
    }
    if operation not in supported:
        raise ValueError(f"unsupported thermostat IR operation: {operation}")
    witness = node["data"].get("source_event") or "runtime:" + node_id
    premises = (
        "reads=node_entry:" + ",".join(node["ir_reads"]),
        "writes=node_exit:" + ",".join(node["ir_writes"]),
        "successors=" + canonical_json(node["successors"]),
        "operation_data_sha256=" + hashlib.sha256(
            canonical_json(node["data"]).encode()).hexdigest(),
        "exception=" + node["on_exception"],
    )
    copy_note = (
        "; destination is a distinct event-time copy, never a live alias"
        if operation in {"send_copy", "accept_copy"} else ""
    )
    return LocalSimulationObligation(
        node_id=node_id,
        rule="thermostat." + operation,
        source_witness=witness,
        premises=premises,
        conclusion=(
            "each successful source-rule result is included by this node's "
            "entry/exit relation; all other storage follows the frame rule" + copy_note
        ),
    )


def _flow_accesses(parser, inventory, graph) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Recover typed flow reads omitted by the legacy graph inventory."""

    keys = set(inventory.value_types) | set(graph["storage"])

    def stored_target(key: str) -> str:
        seen = set()
        while key in parser.parsed_bindings:
            if key in seen:
                raise ValueError(f"cyclic stored flow alias: {key}")
            seen.add(key); key = parser.parsed_bindings[key]
        return key

    reads: set[str] = set()
    writes: set[str] = set()
    for flow in parser.flows:
        start = parser.system_part + "::" + flow.from_port.replace(".", "::") + "::"
        end = parser.system_part + "::" + flow.to_port.replace(".", "::") + "::"
        matches = [key for key in keys if key.startswith(start)]
        if not matches:
            raise ValueError(f"flow has no typed source fields: {flow.from_port}")
        for key in matches:
            reads.add(stored_target(key))
            writes.add(stored_target(end + key[len(start):]))
    return tuple(sorted(reads)), tuple(sorted(writes))


def extract_thermostat_markov_ir(
    model_path: str | Path | None = None,
    contract_path: str | Path | None = None,
) -> MarkovIR:
    """Extract the checked thermostat profile without applying reductions."""

    from clarity.certification.ordered_execution import build_execution_description
    from clarity.sysml.parser import SysMLParser

    contract = load_controller_step_contract(contract_path)
    root = repository_root()
    selected = Path(model_path) if model_path is not None else root / contract["model"]["path"]
    parser = SysMLParser(str(selected))
    parser.parse()
    inventory = build_execution_description(parser)
    graph = inventory.decision_transition
    source_sha = hashlib.sha256(selected.read_bytes()).hexdigest()
    if source_sha != contract["model"]["sha256"]:
        raise ValueError("thermostat source does not match the frozen controller-step contract")

    storages: list[IRStorage] = []
    for record in contract["storage"]:
        declared_type = record["declared_type"]
        keys = tuple(key for key in (
            record.get("source_storage"), record.get("graph_storage")
        ) if key)
        storages.append(IRStorage(
            identity=StorageId(
                uid="semantic:" + record["name"],
                owner=record["owner"],
                path=tuple(record["path"]),
                role=record["role"],
                declared_type=declared_type,
                native_sort=(NativeSort.PRESENCE if record["role"] == "transport_valid"
                             else native_sort(declared_type)),
                layer=StorageLayer.SEMANTIC,
            ),
            graph_keys=keys,
            origin=record.get("origin"),
        ))

    for key, record in sorted(graph["storage"].items()):
        declared_type = _graph_declared_type(key, record["declared_type"])
        storages.append(IRStorage(
            identity=StorageId(
                uid="graph:" + key,
                owner="formal_graph",
                path=(key,),
                role=_graph_role(key, record["declared_type"]),
                declared_type=declared_type,
                native_sort=native_sort(declared_type),
                layer=StorageLayer.GRAPH,
            ),
            graph_keys=(key,),
        ))

    bindings = _semantic_bindings(contract)
    flow_reads, flow_writes = _flow_accesses(parser, inventory, graph)
    events: list[IREvent] = []
    for node_id, node in graph["nodes"].items():
        source_event = node["data"].get("source_event")
        reads = list(node["reads"])
        writes = list(node["writes"])
        if node_id == "system::thermometer/step/4":
            # The legacy queue node records the mailbox's prior value but omits
            # the local payload field copied into it. Recover that source read
            # explicitly so send-copy lowering cannot invent the payload.
            reads = sorted(set(reads) | {
                "system::thermometer::temperatureReading::temperatureCelcius"
            })
        if node_id == "cycle/solve":
            reads = sorted(set(reads) | set(flow_reads))
            writes = sorted(set(writes) | set(flow_writes))
        events.append(IREvent(
            node_id=node_id,
            operation=node["operation"],
            source_event=source_event,
            source_kind=graph["source_events"].get(source_event),
            successors=tuple(sorted(node["successors"].items())),
            graph_reads=tuple(VersionedAccess(
                "graph:" + key, AccessKind.READ, f"{node_id}:entry"
            ) for key in reads),
            graph_writes=tuple(VersionedAccess(
                "graph:" + key, AccessKind.WRITE, f"{node_id}:exit"
            ) for key in writes),
            semantic_reads=_semantic_accesses(
                node_id, node["operation"], reads, bindings, AccessKind.READ
            ),
            semantic_writes=_semantic_accesses(
                node_id, node["operation"], writes, bindings, AccessKind.WRITE
            ),
            data_json=canonical_json(node["data"]),
            frame_rule=node["frame"],
            on_exception=node["on_exception"],
        ))

    modes = []
    semantic_by_graph = {
        key: storage.identity.uid
        for storage in storages if storage.identity.layer is StorageLayer.SEMANTIC
        for key in storage.graph_keys
    }
    for owner, states in sorted(graph["machine_states"].items()):
        modes.append(MachineMode(
            owner=owner,
            storage_uid=semantic_by_graph["$machine:" + owner],
            states=tuple(states),
        ))

    boundary = contract["decision_boundary"]
    visible_sort = {
        "executed_action": NativeSort.ENUM,
        "outcome_constructor": NativeSort.ENUM,
        "next_buffer_when_continuing": NativeSort.RECORD,
        "reward": NativeSort.FLOAT64,
        "elapsed_simulator_ticks_and_time": NativeSort.RECORD,
        "intrinsic_termination": NativeSort.BOOL,
        "execution_or_evaluation_error": NativeSort.ENUM,
        "ordered_requirement_events": NativeSort.RECORD,
    }
    return MarkovIR(
        source_path=contract["model"]["path"],
        source_sha256=source_sha,
        contract_sha256=hashlib.sha256(canonical_json(contract).encode()).hexdigest(),
        storages=tuple(storages),
        events=tuple(events),
        source_events=tuple(sorted(graph["source_events"].items())),
        machine_modes=tuple(modes),
        visible_outputs=tuple(VisibleOutput(
            name, visible_sort[name],
            "continuing_only" if name == "next_buffer_when_continuing" else "all_outcomes",
        ) for name in contract["outcomes"]["visible_fields"]),
        local_obligations=tuple(_local_obligation(node_id, {
            **node,
            "ir_reads": (
                sorted(set(node["reads"]) | set(flow_reads))
                if node_id == "cycle/solve" else
                sorted(set(node["reads"]) | {
                    "system::thermometer::temperatureReading::temperatureCelcius"
                }) if node_id == "system::thermometer/step/4" else node["reads"]
            ),
            "ir_writes": (sorted(set(node["writes"]) | set(flow_writes))
                          if node_id == "cycle/solve" else node["writes"]),
        }) for node_id, node in graph["nodes"].items()),
        required_properties=tuple(contract["required_properties"]),
        decision_boundary=DecisionBoundary(
            request_node=boundary["request_node"],
            resume_node=boundary["resume_node"],
            entry=boundary["entry"],
            exits=(boundary["request_node"], "terminal", "execution_error"),
            action_convention=contract["actions"]["history_records"],
        ),
    )
