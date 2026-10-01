"""Checked pre-Z3 native semantics for the thermostat proof profile.

The contract freezes source identities, complete sliced-operation coverage, and
the controller-facing shield/observation/outcome equations.  It does not claim
that the equations have been lowered into solver formulas yet.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping

from .markov_contract import load_controller_step_contract, repository_root
from .markov_ir import fingerprint


SCHEMA = "clarity.thermostat-native-semantics"
VERSION = 1
CONTRACT_NAME = "thermostat_native_semantics_v1.json"

_EXPECTED_OPERATION_RULES = {
    "accept_copy": "consume_present_payload_into_distinct_received_storage",
    "advance_engine_time": "binary64_entry_time_plus_configured_dt",
    "apply_executed_action": "install_shield_selected_boolean_action",
    "assign": "evaluate_entry_expression_then_single_exit_write",
    "branch": "boolean_guard_selects_exactly_one_successor",
    "call_machine": "push_finite_return_site_and_enter_machine",
    "check_all_requirements": "ordered_lossless_false_and_error_accumulation",
    "completion_test": "latched_completion_selects_terminal_or_next_cycle",
    "decision": "emit_raw_inputs_latch_completion_and_pause",
    "enter_machine": "snapshot_mode_and_begin_ordered_transition_scan",
    "finish_machine_transition": "apply_do_action_consume_trigger_and_write_mode",
    "machine_from_state": "compare_saved_entry_mode_to_transition_source",
    "match_trigger": "first_compatible_message_or_absent_transition",
    "outcome": "disjoint_terminal_or_execution_error_constructor",
    "return": "pop_finite_return_site",
    "send_copy": "copy_payload_into_distinct_sent_storage",
    "set_dt": "copy_immutable_configured_dt",
    "solve_source_constraints": "ordered_convergent_constraints_then_single_source_flows",
}
_EXPECTED_SHIELD_ORDER = [
    "setPoint >= temperatureCelcius + tolerance => 1",
    "setPoint <= temperatureCelcius - tolerance => 2",
    "otherwise => 0",
]
_EXPECTED_OUTCOME_PRIORITY = [
    {"condition": "execution_or_requirement_evaluation_error",
     "constructor": "error", "reward": 0.0},
    {"condition": "false_required_property",
     "constructor": "terminal", "reward": -1.0},
    {"condition": "intrinsic_simulator_terminal",
     "constructor": "terminal", "reward": 1.0},
    {"condition": "next_decision",
     "constructor": "continue", "reward": -0.01},
]
_EXPECTED_PRE_TRANSITION_SHIELD_ERROR = {
    "constructor": "error",
    "reward": 0.0,
    "elapsed_ticks": 0,
    "elapsed_time": 0.0,
    "executed_action": "unavailable",
    "next_observation": "unavailable",
    "requirement_events": "unavailable",
}
_EXPECTED_REQUIREMENT_RESULT_AVAILABILITY = (
    "available_iff_cycle_check_reached_after_successful_shield"
)


@dataclass(frozen=True)
class Phase2Outcome:
    constructor: str
    reward: float
    intrinsic_termination: bool
    execution_error: bool
    terminal_class: str | None


def contract_path() -> Path:
    return Path(__file__).with_name("contracts") / CONTRACT_NAME


def load_native_semantics_contract(path: str | Path | None = None) -> dict[str, Any]:
    selected = contract_path() if path is None else Path(path)
    value = json.loads(selected.read_text())
    if not isinstance(value, dict):
        raise ValueError("native-semantics contract must be a JSON object")
    return value


def thermostat_shield_action(
    proposal: int,
    *,
    set_point: float,
    temperature: float,
    tolerance: float,
) -> int:
    """Exact Boolean thermostat requirement action for finite source inputs."""

    if type(proposal) is not int or proposal not in {0, 1, 2, 3}:
        raise ValueError("proposal is outside the four-action source domain")
    # Preserve the source AST's binary64 operation order.  Algebraically
    # moving tolerance across either comparison is not IEEE-754 exact.
    cold = set_point >= temperature + tolerance
    hot = set_point <= temperature - tolerance
    if cold and hot:
        raise ValueError("thermostat requirement has no legal Boolean action")
    required = 1 if cold else 2 if hot else 0
    # SpecShield returns the proposal exactly when it satisfies the complete
    # requirement; otherwise it returns the unique required action.
    return proposal if proposal == required else required


def observation_float32_bits(raw_value: float, scale: float) -> int:
    """Encode ``float32(float(raw_value) / float(scale))`` as IEEE bits."""

    if scale == 0.0:
        raise ZeroDivisionError("observation scale is zero")
    binary64_result = float(raw_value) / float(scale)
    return struct.unpack(">I", struct.pack(">f", binary64_result))[0]


def phase2_outcome(
    *,
    execution_error: bool = False,
    requirement_evaluation_error: bool = False,
    false_required_property: bool = False,
    intrinsic_terminal: bool = False,
) -> Phase2Outcome:
    """Apply the exact source priority before wrapper time-limit truncation."""

    if execution_error or requirement_evaluation_error:
        return Phase2Outcome("error", 0.0, False, True, "execution_error")
    if false_required_property:
        return Phase2Outcome("terminal", -1.0, False, False, "violation")
    if intrinsic_terminal:
        return Phase2Outcome("terminal", 1.0, True, False, "success")
    return Phase2Outcome("continue", -0.01, False, False, None)


def native_semantics_fingerprint(contract: Mapping[str, Any] | None = None) -> str:
    selected = load_native_semantics_contract() if contract is None else dict(contract)
    return fingerprint(selected)


def validate_thermostat_native_semantics(
    contract: Mapping[str, Any] | None = None,
) -> list[str]:
    """Reconstruct source hashes, slice operations, and interface constants."""

    selected = load_native_semantics_contract() if contract is None else dict(contract)
    errors: list[str] = []
    if selected.get("schema") != SCHEMA or selected.get("version") != VERSION:
        errors.append("native-semantics schema/version mismatch")
    if selected.get("claim_scope") != "pre_z3_native_operation_and_interface_contract":
        errors.append("native-semantics claim scope mismatch")

    root = repository_root()
    for relative, expected in selected.get("source_files", {}).items():
        path = root / relative
        if not path.is_file():
            errors.append(f"native-semantics source unavailable: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"native-semantics source hash mismatch: {relative}")

    from .markov_extract import extract_thermostat_markov_ir
    from .markov_interval import derive_thermostat_decision_interval
    from .markov_slice import build_thermostat_relevance_slice

    ir = extract_thermostat_markov_ir()
    interval = derive_thermostat_decision_interval(ir)
    slice_ = build_thermostat_relevance_slice(ir, interval)
    actual_operations = {event.operation for event in slice_.events}
    operation_rules = selected.get("operation_rules", {})
    declared_operations = set(operation_rules)
    if actual_operations != declared_operations:
        errors.append("native-semantics sliced-operation coverage mismatch")
    if operation_rules != _EXPECTED_OPERATION_RULES:
        errors.append("native-semantics operation rule mismatch")

    controller = load_controller_step_contract()
    shield = selected.get("shield", {})
    if shield.get("proposal_domain") != controller["actions"]["proposal_ids"]:
        errors.append("native-semantics proposal domain mismatch")
    if shield.get("action_map") != controller["actions"]["action_map"]:
        errors.append("native-semantics action map mismatch")
    if shield.get("dead_proposals") != controller["actions"]["structurally_dead_proposals"]:
        errors.append("native-semantics dead-proposal mismatch")
    if shield.get("history_records") != "executed_action":
        errors.append("native-semantics action-history convention mismatch")
    if shield.get("required_action_order") != _EXPECTED_SHIELD_ORDER:
        errors.append("native-semantics shield equation mismatch")
    if selected.get("pre_transition_shield_error") != \
            _EXPECTED_PRE_TRANSITION_SHIELD_ERROR:
        errors.append("native-semantics pre-transition shield-error mismatch")

    observation = selected.get("observation", {})
    if observation.get("scale") != controller["observation"]["observation_scale"]:
        errors.append("native-semantics observation scale mismatch")
    if observation.get("fields") != controller["observation"]["policy_fields"]:
        errors.append("native-semantics observation field mismatch")
    if observation.get("operation_order") != "binary64_divide_then_round_to_float32":
        errors.append("native-semantics observation operation-order mismatch")

    if selected.get("phase2_outcome_priority") != _EXPECTED_OUTCOME_PRIORITY:
        errors.append("native-semantics Phase-2 outcome priority mismatch")
    if selected.get("requirement_result_availability") != \
            _EXPECTED_REQUIREMENT_RESULT_AVAILABILITY:
        errors.append("native-semantics requirement-result availability mismatch")
    if selected.get("time_limit_truncation") != "excluded_wrapper_outcome":
        errors.append("native-semantics truncation classification mismatch")
    return errors
