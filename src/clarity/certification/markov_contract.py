"""Checked Phase-1 controller-step contracts for the finite-history proof.

This module does not lower SysML into ``MarkovIR`` and does not prove that a
buffer is Markov.  It independently reconstructs the source/runtime interface
facts frozen by a checked-in golden contract.  The production extractor may
start only after this narrower contract remains valid.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT_SCHEMA = "clarity.controller-step-contract"
CONTRACT_VERSION = 1
THERMOSTAT_CONTRACT = "thermostat_controller_step_v1.json"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def thermostat_contract_path() -> Path:
    return Path(__file__).with_name("contracts") / THERMOSTAT_CONTRACT


def load_controller_step_contract(path: str | Path | None = None) -> dict[str, Any]:
    selected = thermostat_contract_path() if path is None else Path(path)
    value = json.loads(selected.read_text())
    if not isinstance(value, dict):
        raise ValueError("controller-step contract must be a JSON object")
    return value


def _check(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, found {actual!r}")


def _node(graph: Mapping[str, Any], identity: str, errors: list[str]) -> dict[str, Any]:
    node = graph.get("nodes", {}).get(identity)
    if not isinstance(node, dict):
        errors.append(f"missing decision-transition node {identity}")
        return {}
    return node


def _validate_contract_shape(contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _check(errors, "schema", contract.get("schema"), CONTRACT_SCHEMA)
    _check(errors, "version", contract.get("version"), CONTRACT_VERSION)
    _check(errors, "claim_scope", contract.get("claim_scope"),
           "formal_supported_profile_interface_only")

    model = contract.get("model", {})
    _check(errors, "model name", model.get("name"), "thermostat")
    _check(errors, "model path", model.get("path"),
           "src/clarity/models/thermostat/model.sysml")
    _check(errors, "model dt", model.get("dt"), 0.1)

    boundary = contract.get("decision_boundary", {})
    _check(errors, "boundary entry", boundary.get("entry"),
           "after_executed_action_installation")
    _check(errors, "boundary exit", boundary.get("exit"),
           "first_next_decision_terminal_or_execution_error")
    _check(errors, "completion latch", boundary.get("completion_latched_before_pause"), True)
    _check(errors, "completion handling", boundary.get("terminal_after_response_and_cycle_end"), True)

    observation = contract.get("observation", {})
    _check(errors, "observation dtype", observation.get("dtype"), "float32")
    _check(errors, "observation fields", observation.get("policy_fields"),
           ["setPoint", "temperatureCelcius"])
    _check(errors, "observation sources", observation.get("raw_sources"), {
        "setPoint": "system::controller::setPointCelcius",
        "temperatureCelcius": "system::controller::reading::temperatureCelcius",
    })
    _check(errors, "completion input", observation.get("completion_input"), "done")
    _check(errors, "observation encoding", observation.get("encoding"),
           "float32(float(source_value) / observation_scale)")

    actions = contract.get("actions", {})
    _check(errors, "proposal domain", actions.get("proposal_ids"), [0, 1, 2, 3])
    _check(errors, "action outputs", actions.get("output_fields"),
           ["heaterState", "acState"])
    _check(errors, "action history convention", actions.get("history_records"),
           "executed_action_after_spec_shield")
    _check(errors, "shield input", actions.get("shield_input"),
           "raw_pending_model_inputs")
    _check(errors, "action availability", actions.get("availability"),
           "fixed_full_policy_proposal_domain_before_shield")
    _check(errors, "action execution", actions.get("execution"),
           "SpecShield.__call__(proposal, raw_pending_model_inputs)")
    _check(errors, "structurally dead proposal",
           actions.get("structurally_dead_proposals"), [3])

    buffer = contract.get("buffer", {})
    _check(errors, "buffer order", buffer.get("layout"),
           ["current_observation", "past_observations_newest_first",
            "past_executed_actions_newest_first_one_hot"])
    _check(errors, "observation padding", buffer.get("observation_padding"), "float32_zero")
    _check(errors, "action padding", buffer.get("action_padding"), "float32_zero_vector")
    _check(errors, "action width", buffer.get("action_width"), 4)

    outcomes = contract.get("outcomes", {})
    _check(errors, "outcome constructors", outcomes.get("constructors"),
           ["continue", "terminal", "error"])
    _check(errors, "truncation classification", outcomes.get("time_limit_truncation"),
           "wrapper_outcome_not_intrinsic_termination")

    expected_storage = {
        "physical_temperature": ("physical", "system::environment::temperatureCelcius", None),
        "held_temperature": ("sensor_held", "system::thermometer::lastReadingCelcius", None),
        "sent_temperature_payload": ("payload_sent", None, "$mailbox:system::controller::thermometerPort"),
        "received_temperature_payload": ("payload_received", "system::controller::reading::temperatureCelcius", None),
        "heater_command_flag": ("actuator", "system::controller::heaterOn", None),
        "ac_command_flag": ("actuator", "system::controller::acOn", None),
        "heater_output": ("actuator", "system::heater::heatOut::heat::rateWatts", None),
        "ac_output": ("actuator", "system::ac::heatOut::heat::rateWatts", None),
        "heater_mode": ("machine_mode", None, "$machine:system::heater"),
        "ac_mode": ("machine_mode", None, "$machine:system::ac"),
        "source_time": ("clock_or_phase", "system::currentTime", None),
        "engine_time": ("clock_or_phase", None, "$engine_time"),
        "set_point": ("immutable_parameter", "system::controller::setPointCelcius", None),
        "tolerance": ("immutable_parameter", "system::controller::toleranceCelcius", None),
        "outside_temperature": ("immutable_parameter", "system::environment::outsideTemperatureCelcius", None),
        "thermometer_payload_present": ("transport_valid", None, "$mailbox:system::controller::thermometerPort"),
        "latched_completion": ("intrinsic_terminal", None, "$pending_completion"),
    }
    actual_storage = {
        record.get("name"): (
            record.get("role"), record.get("source_storage"), record.get("graph_storage")
        )
        for record in contract.get("storage", []) if isinstance(record, dict)
    }
    _check(errors, "thermostat storage role/source map", actual_storage, expected_storage)

    identities = []
    for index, record in enumerate(contract.get("storage", [])):
        if not isinstance(record, dict):
            errors.append(f"storage[{index}] is not an object")
            continue
        identity = (
            record.get("owner"), tuple(record.get("path", [])),
            record.get("role"), record.get("declared_type"),
        )
        if not identity[0] or not identity[1] or not identity[2] or not identity[3]:
            errors.append(f"storage[{index}] has an incomplete identity")
        identities.append(identity)
    if len(identities) != len(set(identities)):
        errors.append("controller-step contract aliases distinct storage identities")
    return errors


def validate_thermostat_controller_step_contract(
    contract: Mapping[str, Any] | None = None,
    *,
    model_path: str | Path | None = None,
) -> list[str]:
    """Reconstruct and check the thermostat Phase-1 interface contract.

    The checker intentionally uses the existing parser, ordered execution
    inventory, environment, shield, and buffer wrapper rather than trusting
    values asserted by the JSON artifact.  An empty result means only that the
    interface inventory agrees with those named source/runtime components.
    """

    contract = deepcopy(load_controller_step_contract() if contract is None else dict(contract))
    errors = _validate_contract_shape(contract)
    root = repository_root()
    configured_model = Path(contract.get("model", {}).get("path", ""))
    selected_model = Path(model_path) if model_path is not None else root / configured_model
    if not selected_model.is_file():
        return errors + [f"thermostat model is unavailable: {selected_model}"]

    source_sha256 = hashlib.sha256(selected_model.read_bytes()).hexdigest()
    _check(errors, "model sha256", source_sha256,
           contract.get("model", {}).get("sha256"))

    normalization = json.loads(
        (root / "src/clarity/runtime/normalization.json").read_text()
    )
    normalization_rows = [
        row for row in normalization.get("records", [])
        if row.get("source_sha256") == source_sha256
        and row.get("dt") == contract.get("model", {}).get("dt")
    ]
    if len(normalization_rows) != 1:
        errors.append(
            "source/dt must select exactly one recorded observation normalization"
        )
    else:
        _check(
            errors,
            "recorded observation scale",
            contract.get("observation", {}).get("observation_scale"),
            normalization_rows[0].get("scale"),
        )

    # Imports stay local so loading the artifact itself has no parser/runtime
    # side effects and remains usable by later standalone certificate tooling.
    import numpy as np
    from clarity.certification.markov_reference import StorageId, StorageRole
    from clarity.certification.ordered_execution import build_execution_description
    from clarity.runtime.env import SysMLEnv
    from clarity.runtime.shield import SpecShield
    from clarity.sysml.parser import SysMLParser
    from clarity.training.reduced.buffered_env import BufferedDiscreteEnv

    parser = SysMLParser(str(selected_model))
    parser.parse()
    inventory = build_execution_description(parser)
    graph = inventory.decision_transition
    _check(errors, "execution source sha256", inventory.source_sha256, source_sha256)

    expected_properties = contract.get("required_properties", [])
    actual_properties = [record["name"] for record in inventory.property_inventory]
    _check(errors, "required property order", actual_properties, expected_properties)
    _check(errors, "cycle-end properties",
           graph.get("nodes", {}).get("cycle/check", {}).get("data", {}).get("properties"),
           expected_properties)

    programs = [record for record in inventory.programs if record["kind"] == "step"]
    actual_program_order = [record["context"] for record in programs]
    _check(errors, "step program order", actual_program_order,
           contract.get("event_order", {}).get("step_programs"))
    actual_event_kinds = {
        record["context"]: [event["kind"] for event in record["events"]]
        for record in programs
    }
    _check(errors, "step event kinds", actual_event_kinds,
           contract.get("event_order", {}).get("event_kinds"))

    boundary = contract.get("decision_boundary", {})
    request = boundary.get("request_node", "")
    resume = boundary.get("resume_node", "")
    _check(errors, "decision records", graph.get("decisions"), [{
        "request": request,
        "resume": resume,
        "context": "system::controller",
    }])
    request_node = _node(graph, request, errors)
    resume_node = _node(graph, resume, errors)
    _check(errors, "request operation", request_node.get("operation"), "decision")
    _check(errors, "request resume edge", request_node.get("successors"), {"resume": resume})
    _check(errors, "request stop point", request_node.get("data", {}).get("stop_before_action"), True)
    _check(errors, "request completion input",
           request_node.get("data", {}).get("completion_input"), "done")
    _check(errors, "request input order",
           list(request_node.get("data", {}).get("inputs", {})),
           ["setPoint", "temperatureCelcius", "done"])
    _check(errors, "resume operation", resume_node.get("operation"), "apply_executed_action")
    _check(errors, "resume continuation", resume_node.get("successors"),
           {"next": "system::controller/step/4"})
    _check(errors, "resume convention",
           resume_node.get("data", {}).get("action_convention"), "executed")
    _check(errors, "resume outputs", resume_node.get("data", {}).get("outputs"), {
        "heaterState": "system::controller::policyCall::heaterState",
        "acState": "system::controller::policyCall::acState",
    })

    send_node = _node(graph, "system::thermometer/step/4", errors)
    _check(errors, "sensor send operation", send_node.get("operation"), "send_copy")
    _check(errors, "sensor send destination",
           send_node.get("data", {}).get("destination"),
           "system::controller::thermometerPort")
    _check(errors, "sensor send semantics", send_node.get("data", {}).get("copy"),
           "current local item type and a copy of its current attrs")

    accept_node = _node(graph, "system::controller/step/2", errors)
    _check(errors, "controller accept operation", accept_node.get("operation"), "accept_copy")
    _check(errors, "controller accept port", accept_node.get("data", {}).get("port"),
           "system::controller::thermometerPort::reading")
    _check(errors, "controller accept fallback",
           accept_node.get("data", {}).get("parent_fallback"),
           "only when full-path mailbox is empty")
    if "system::controller::reading::temperatureCelcius" not in accept_node.get("writes", []):
        errors.append("controller accept does not write the received temperature storage")

    _check(errors, "cycle time/check order",
           graph.get("nodes", {}).get("cycle/time", {}).get("successors"),
           {"next": "cycle/check"})
    _check(errors, "cycle check/completion order",
           graph.get("nodes", {}).get("cycle/check", {}).get("successors"),
           {"next": "cycle/terminal"})
    _check(errors, "completion branches",
           graph.get("nodes", {}).get("cycle/terminal", {}).get("successors"),
           {"true": "terminal", "false": "cycle/entry"})

    observation = contract.get("observation", {})
    scale = observation.get("observation_scale")
    dt = contract.get("model", {}).get("dt")
    env = SysMLEnv(str(selected_model), dt=dt, observation_scale=scale)
    buffered = BufferedDiscreteEnv(
        str(selected_model), dt=dt, n_obs=2, n_act=2,
        observation_scale=scale,
    )
    try:
        _check(errors, "runtime observation fields", list(env.observation_keys),
               observation.get("policy_fields"))
        _check(errors, "runtime output fields", [name for name, _ in env.output_parameters],
               contract.get("actions", {}).get("output_fields"))
        _check(errors, "runtime observation scale", env.observation_scale, scale)
        actual_action_map = {str(key): value for key, value in env.action_map.items()}
        _check(errors, "runtime action map", actual_action_map,
               contract.get("actions", {}).get("action_map"))

        buffered._obs_hist = [
            np.array([3.0, 4.0], dtype=np.float32),
            np.array([5.0, 6.0], dtype=np.float32),
        ]
        buffered._act_hist = [buffered._onehot(2), buffered._onehot(1)]
        actual_layout = buffered._augment(np.array([1.0, 2.0], dtype=np.float32))
        expected_layout = np.array(
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 1.0, 0.0, 0.0],
            dtype=np.float32,
        )
        if not np.array_equal(actual_layout, expected_layout):
            errors.append("buffer layout differs from current/newest-first/executed-one-hot contract")
    finally:
        buffered.close()
        env.close()

    shield = SpecShield(str(selected_model))
    actions = contract.get("actions", {})
    _check(errors, "shield inputs", shield.in_params,
           ["setPoint", "temperatureCelcius", "done"])
    _check(errors, "shield outputs", shield.out_params, actions.get("output_fields"))
    _check(errors, "shield action map",
           {str(key): value for key, value in shield.action_map.items()},
           actions.get("action_map"))
    _check(errors, "shield dead actions", sorted(shield.dead_actions),
           actions.get("structurally_dead_proposals"))

    storage_ids = []
    for record in contract.get("storage", []):
        try:
            storage_ids.append(StorageId(
                record["owner"], tuple(record["path"]),
                StorageRole(record["role"]), record["declared_type"],
                record.get("origin"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"invalid storage identity {record!r}: {exc}")
            continue
        source_storage = record.get("source_storage")
        if source_storage is not None:
            _check(errors, f"declared type for {source_storage}",
                   inventory.value_types.get(source_storage), record["declared_type"])
        graph_storage = record.get("graph_storage")
        if graph_storage is not None and graph_storage not in graph.get("storage", {}):
            errors.append(f"missing graph storage {graph_storage}")
    if len(storage_ids) != len(set(storage_ids)):
        errors.append("typed StorageId construction aliases contract locations")
    return errors


def main() -> int:
    errors = validate_thermostat_controller_step_contract()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("thermostat controller-step contract: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONTRACT_SCHEMA",
    "CONTRACT_VERSION",
    "load_controller_step_contract",
    "thermostat_contract_path",
    "validate_thermostat_controller_step_contract",
]
