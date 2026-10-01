"""Phase-1 validation for the thermostat controller-step contract."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest

from clarity.certification.markov_contract import (
    load_controller_step_contract,
    validate_thermostat_controller_step_contract,
)
from clarity.runtime.env import SysMLEnv
from clarity.runtime.requirements import RequirementEvent


class ThermostatContractValidation(unittest.TestCase):
    def test_checked_in_contract_reconstructs_from_source_and_runtime(self):
        errors = validate_thermostat_controller_step_contract()
        self.assertEqual(errors, [], "\n".join(errors))

    def test_source_hash_mutation_rejects(self):
        contract = load_controller_step_contract()
        contract["model"]["sha256"] = "0" * 64
        errors = validate_thermostat_controller_step_contract(contract)
        self.assertTrue(any("model sha256" in error for error in errors), errors)

    def test_storage_alias_mutation_rejects(self):
        contract = load_controller_step_contract()
        contract["storage"][1] = deepcopy(contract["storage"][0])
        errors = validate_thermostat_controller_step_contract(contract)
        self.assertTrue(any("aliases" in error for error in errors), errors)

    def test_proposed_action_history_mutation_rejects(self):
        contract = load_controller_step_contract()
        contract["actions"]["history_records"] = "proposed_action"
        errors = validate_thermostat_controller_step_contract(contract)
        self.assertTrue(any("action history convention" in error for error in errors), errors)

    def test_buffer_reset_or_shift_rule_mutation_rejects(self):
        for key in ("reset_rule", "step_rule"):
            contract = load_controller_step_contract()
            contract["buffer"][key] = "mutated"
            errors = validate_thermostat_controller_step_contract(contract)
            self.assertTrue(any(
                f"buffer {key.split('_')[0]} rule" in error for error in errors
            ), errors)

    def test_event_order_mutation_rejects(self):
        contract = load_controller_step_contract()
        contract["event_order"]["step_programs"][0:2] = reversed(
            contract["event_order"]["step_programs"][0:2]
        )
        errors = validate_thermostat_controller_step_contract(contract)
        self.assertTrue(any("step program order" in error for error in errors), errors)

    @staticmethod
    def _step_case(*, outcome="decision", events=(), error=None, max_steps=10):
        env = object.__new__(SysMLEnv)
        env._episode_done = False
        env._action_map = {0: {}}
        env._step_count = 0
        env._max_steps = max_steps
        env.phase = 2
        env._twin = SimpleNamespace(advance=lambda action: SimpleNamespace(
            outcome=outcome,
            events=events,
            error=error,
            state=None,
        ))
        return env.step(0)

    def test_phase_two_reward_and_outcome_precedence(self):
        _obs, reward, done, info = self._step_case()
        self.assertEqual((reward, done, info["outcome"]), (-0.01, False, "RUNNING"))

        _obs, reward, done, info = self._step_case(outcome="terminal")
        self.assertEqual((reward, done, info["outcome"]), (1.0, True, "SUCCESS"))

        violation = RequirementEvent(
            episode_id=1,
            sequence=0,
            boundary="cycle_end",
            source="test",
            engine_time=0.1,
            statuses={
                "property": {
                    "kind": "Obligation",
                    "metadata": ["Obligation"],
                    "status": False,
                    "error": None,
                }
            },
        )
        _obs, reward, done, info = self._step_case(events=(violation,))
        self.assertEqual((reward, done, info["outcome"]), (-1.0, True, "VIOLATION"))

        _obs, reward, done, info = self._step_case(error="execution failed")
        self.assertEqual((reward, done, info["outcome"]), (0.0, True, "ERROR"))

        _obs, reward, done, info = self._step_case(max_steps=1)
        self.assertEqual((reward, done, info["outcome"]), (0.0, True, "TRUNCATED"))
        self.assertTrue(info["truncated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
