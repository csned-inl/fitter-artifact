"""Independent structural validator for thermostat ``MarkovIR`` extraction."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from .markov_contract import load_controller_step_contract, repository_root
from .markov_ir import AccessKind, MarkovIR, NativeSort, StorageLayer, canonical_json, native_sort


def _expected_graph_type(key: str, declared: str | None) -> str:
    # Kept separate from the extractor so a mutation cannot validate itself by
    # merely reusing extractor output.
    fixed = {
        "$configured_dt": "Real", "$engine_time": "Real",
        "$executed_action": "ExecutedAction", "$initial_overrides": "RuntimeRecord",
        "$inputs": "RuntimeRecord", "$locals": "RuntimeRecord",
        "$machine_frame": "RuntimeRecord", "$machines": "RuntimeRecord",
        "$mailboxes": "RuntimeRecord", "$pending_completion": "Boolean",
        "$requirement_ledger": "RuntimeRecord", "$stack": "RuntimeStack",
        "$state": "RuntimeRecord",
    }
    if declared:
        return declared
    if key in fixed:
        return fixed[key]
    if key.startswith("$machine:"):
        return "MachineMode"
    if key.startswith("$mailbox:"):
        return "RuntimeQueue"
    lower = key.lower()
    if any(marker in lower for marker in (
        "acon", "heateron", "policycall.acstate", "policycall::acstate",
        "policycall.heaterstate", "policycall::heaterstate",
        "behavior.on", "behavior.reset",
    )):
        return "Boolean"
    return "Real"


def _error(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _source_flow_accesses(parser, inventory, graph) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Independent reconstruction of concrete field copies for SysML flows."""

    universe = set(inventory.value_types).union(graph["storage"])

    def resolve(key: str) -> str:
        visited = set()
        while key in parser.parsed_bindings:
            if key in visited:
                raise ValueError(f"cyclic stored flow alias: {key}")
            visited.add(key)
            key = parser.parsed_bindings[key]
        return key

    sources, destinations = set(), set()
    for flow in parser.flows:
        source_prefix = (parser.system_part + "::" +
                         flow.from_port.replace(".", "::") + "::")
        destination_prefix = (parser.system_part + "::" +
                              flow.to_port.replace(".", "::") + "::")
        fields = [key for key in universe if key.startswith(source_prefix)]
        if not fields:
            raise ValueError(f"untyped flow source: {flow.from_port}")
        for field in fields:
            sources.add(resolve(field))
            destinations.add(resolve(destination_prefix + field[len(source_prefix):]))
    return tuple(sorted(sources)), tuple(sorted(destinations))


def validate_thermostat_markov_ir(
    ir: MarkovIR,
    *,
    model_path: str | Path | None = None,
    contract_path: str | Path | None = None,
) -> list[str]:
    """Reconstruct source facts and reject any changed or missing witness."""

    from clarity.certification.ordered_execution import build_execution_description
    from clarity.sysml.parser import SysMLParser

    errors: list[str] = []
    contract = load_controller_step_contract(contract_path)
    selected = (Path(model_path) if model_path is not None
                else repository_root() / contract["model"]["path"])
    parser = SysMLParser(str(selected)); parser.parse()
    inventory = build_execution_description(parser)
    graph = inventory.decision_transition
    flow_reads, flow_writes = _source_flow_accesses(parser, inventory, graph)

    source_sha = hashlib.sha256(selected.read_bytes()).hexdigest()
    _error(errors, ir.source_sha256 == source_sha, "source hash mismatch")
    _error(errors, ir.source_path == contract["model"]["path"], "source path mismatch")
    _error(errors, ir.contract_sha256 == hashlib.sha256(
        canonical_json(contract).encode()).hexdigest(), "contract hash mismatch")

    storage = {item.identity.uid: item for item in ir.storages}
    semantic = {uid.removeprefix("semantic:"): item for uid, item in storage.items()
                if item.identity.layer is StorageLayer.SEMANTIC}
    expected_semantic_names = [record["name"] for record in contract["storage"]]
    _error(errors, sorted(semantic) == sorted(expected_semantic_names),
           "semantic storage inventory differs from contract")
    identities = set()
    for record in contract["storage"]:
        item = semantic.get(record["name"])
        if item is None:
            continue
        identity = item.identity
        expected_sort = (NativeSort.PRESENCE if record["role"] == "transport_valid"
                         else native_sort(record["declared_type"]))
        expected_keys = tuple(key for key in (
            record.get("source_storage"), record.get("graph_storage")
        ) if key)
        _error(errors, (identity.owner, identity.path, identity.role,
                        identity.declared_type, identity.native_sort) == (
            record["owner"], tuple(record["path"]), record["role"],
            record["declared_type"], expected_sort),
            f"semantic identity/type mismatch: {record['name']}")
        _error(errors, item.graph_keys == expected_keys,
               f"semantic graph binding mismatch: {record['name']}")
        structural = (identity.owner, identity.path, identity.role, identity.declared_type)
        _error(errors, structural not in identities,
               f"aliased semantic storage identity: {record['name']}")
        identities.add(structural)

    for key, record in graph["storage"].items():
        item = storage.get("graph:" + key)
        if item is None:
            errors.append(f"missing graph storage: {key}")
            continue
        expected_type = _expected_graph_type(key, record["declared_type"])
        _error(errors, item.identity.declared_type == expected_type,
               f"graph storage declared type mismatch: {key}")
        _error(errors, item.identity.native_sort is native_sort(expected_type),
               f"graph storage native sort mismatch: {key}")
    graph_uids = {"graph:" + key for key in graph["storage"]}
    actual_graph_uids = {uid for uid, item in storage.items()
                         if item.identity.layer is StorageLayer.GRAPH}
    _error(errors, graph_uids == actual_graph_uids, "graph storage coverage mismatch")

    events = {event.node_id: event for event in ir.events}
    _error(errors, set(events) == set(graph["nodes"]), "graph node coverage mismatch")
    for node_id, node in graph["nodes"].items():
        event = events.get(node_id)
        if event is None:
            continue
        source_event = node["data"].get("source_event")
        _error(errors, event.operation == node["operation"],
               f"operation mismatch: {node_id}")
        _error(errors, event.source_event == source_event and
               event.source_kind == graph["source_events"].get(source_event),
               f"source witness mismatch: {node_id}")
        _error(errors, event.successors == tuple(sorted(node["successors"].items())),
               f"successor/order mismatch: {node_id}")
        _error(errors, event.data_json == canonical_json(node["data"]),
               f"operation data mismatch: {node_id}")
        _error(errors, event.frame_rule == node["frame"], f"frame mismatch: {node_id}")
        _error(errors, event.on_exception == node["on_exception"],
               f"exception edge mismatch: {node_id}")
        ir_reads = (sorted(set(node["reads"]) | set(flow_reads))
                    if node_id == "cycle/solve" else node["reads"])
        ir_writes = (sorted(set(node["writes"]) | set(flow_writes))
                     if node_id == "cycle/solve" else node["writes"])
        expected_reads = tuple(("graph:" + key, AccessKind.READ, f"{node_id}:entry")
                               for key in ir_reads)
        expected_writes = tuple(("graph:" + key, AccessKind.WRITE, f"{node_id}:exit")
                                for key in ir_writes)
        actual_reads = tuple((a.storage_uid, a.kind, a.version) for a in event.graph_reads)
        actual_writes = tuple((a.storage_uid, a.kind, a.version) for a in event.graph_writes)
        _error(errors, actual_reads == expected_reads, f"graph reads mismatch: {node_id}")
        _error(errors, actual_writes == expected_writes, f"graph writes mismatch: {node_id}")

    _error(errors, ir.source_events == tuple(sorted(graph["source_events"].items())),
           "source event inventory mismatch")
    covered = [event.source_event for event in ir.events if event.source_event]
    _error(errors, sorted(covered) == sorted(graph["source_events"]),
           "source events not covered exactly once")

    boundary = contract["decision_boundary"]
    _error(errors, (ir.decision_boundary.request_node,
                    ir.decision_boundary.resume_node,
                    ir.decision_boundary.entry,
                    ir.decision_boundary.exits,
                    ir.decision_boundary.action_convention) == (
        boundary["request_node"], boundary["resume_node"], boundary["entry"],
        (boundary["request_node"], "terminal", "execution_error"),
        contract["actions"]["history_records"]),
        "decision boundary mismatch")
    request = events.get(boundary["request_node"])
    resume = events.get(boundary["resume_node"])
    _error(errors, request is not None and request.operation == "decision",
           "request is not a decision")
    _error(errors, resume is not None and resume.operation == "apply_executed_action" and
           resume.decoded_data().get("action_convention") == "executed",
           "resume does not install the executed action")
    _error(errors, ir.required_properties == tuple(contract["required_properties"]),
           "required property order mismatch")

    # Explicit separation/copy witnesses needed before any graph-container
    # reduction is permitted.
    distinct = [semantic.get(name) for name in (
        "physical_temperature", "held_temperature", "sent_temperature_payload",
        "received_temperature_payload",
    )]
    _error(errors, all(distinct) and len({item.identity for item in distinct}) == 4,
           "physical/held/sent/received storage identities are not distinct")
    send = events.get("system::thermometer/step/4")
    accept = events.get("system::controller/step/2")
    if send:
        writes = {a.storage_uid for a in send.semantic_writes}
        _error(errors, {"semantic:sent_temperature_payload",
                        "semantic:thermometer_payload_present"}.issubset(writes),
               "thermometer send lacks payload/value-presence witnesses")
    if accept:
        reads = {a.storage_uid for a in accept.semantic_reads}
        writes = {a.storage_uid for a in accept.semantic_writes}
        _error(errors, {"semantic:sent_temperature_payload",
                        "semantic:thermometer_payload_present"}.issubset(reads),
               "controller accept lacks payload/value-presence reads")
        _error(errors, {"semantic:received_temperature_payload",
                        "semantic:thermometer_payload_present"}.issubset(writes),
               "controller accept lacks received/value-presence writes")

    expected_modes = {owner: tuple(states) for owner, states in graph["machine_states"].items()}
    actual_modes = {mode.owner: mode.states for mode in ir.machine_modes}
    _error(errors, actual_modes == expected_modes, "machine-mode inventory mismatch")
    _error(errors, [output.name for output in ir.visible_outputs] ==
           contract["outcomes"]["visible_fields"], "visible output inventory mismatch")

    obligations = {obligation.node_id: obligation for obligation in ir.local_obligations}
    _error(errors, set(obligations) == set(graph["nodes"]),
           "local simulation obligation coverage mismatch")
    for node_id, node in graph["nodes"].items():
        obligation = obligations.get(node_id)
        if obligation is None:
            continue
        ir_reads = (sorted(set(node["reads"]) | set(flow_reads))
                    if node_id == "cycle/solve" else node["reads"])
        ir_writes = (sorted(set(node["writes"]) | set(flow_writes))
                     if node_id == "cycle/solve" else node["writes"])
        expected_premises = (
            "reads=node_entry:" + ",".join(ir_reads),
            "writes=node_exit:" + ",".join(ir_writes),
            "successors=" + canonical_json(node["successors"]),
            "operation_data_sha256=" + hashlib.sha256(
                canonical_json(node["data"]).encode()).hexdigest(),
            "exception=" + node["on_exception"],
        )
        _error(errors, obligation.rule == "thermostat." + node["operation"],
               f"local simulation rule mismatch: {node_id}")
        _error(errors, obligation.source_witness ==
               (node["data"].get("source_event") or "runtime:" + node_id),
               f"local source witness mismatch: {node_id}")
        _error(errors, obligation.premises == expected_premises,
               f"local simulation premises mismatch: {node_id}")
        _error(errors, obligation.status == "locally_checked",
               f"local simulation obligation not checked: {node_id}")
    return errors
