"""Stage-1 validation for the finite-history Markov reference checker."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from clarity.certification.markov_reference import (
    BufferSpec,
    CopyEvent,
    DecisionBuffer,
    FiniteDeterministicSystem,
    OutcomeKind,
    ProofStatus,
    StepOutcome,
    StorageId,
    StorageRole,
    TransitionBlocked,
    TypedStore,
    exact_key,
    prove_finite_buffer_markov,
)


def direct_observation_system() -> FiniteDeterministicSystem:
    proposals = ("set0", "set1")

    def step(state, buffer, proposal, executed):
        next_state = executed
        return StepOutcome(
            OutcomeKind.CONTINUE,
            visible=("reward", int(next_state), "duration", 1),
            next_state=next_state,
            next_observation=(next_state,),
        )

    return FiniteDeterministicSystem(
        name="direct_observation",
        state_domain=(False, True),
        reset_states=(False,),
        proposal_domain=proposals,
        executed_action_domain=(False, True),
        buffer_spec=BufferSpec(0, 0, (False,), "absent"),
        observe=lambda state: (state,),
        availability=lambda state, buffer: proposals,
        execute=lambda state, buffer, proposal: proposal == "set1",
        step=step,
    )


@dataclass(frozen=True)
class DelayedState:
    physical: bool
    held: bool


def delayed_boolean_system(b_act: int) -> FiniteDeterministicSystem:
    proposals = ("set0", "set1")
    domain = tuple(DelayedState(physical, held) for physical in (False, True)
                   for held in (False, True))

    def step(state, buffer, proposal, executed):
        # The physical update and the sensor sample remain different events:
        # next physical comes from the new action; next held samples the old
        # physical value.  The held value is never aliased to next physical.
        next_state = DelayedState(physical=executed, held=state.physical)
        return StepOutcome(
            OutcomeKind.CONTINUE,
            visible=("held_after_sample", next_state.held),
            next_state=next_state,
            next_observation=(next_state.held,),
        )

    return FiniteDeterministicSystem(
        name=f"delayed_boolean_action_history_{b_act}",
        state_domain=domain,
        reset_states=(DelayedState(False, False),),
        proposal_domain=proposals,
        executed_action_domain=(False, True),
        buffer_spec=BufferSpec(0, b_act, (False,), "absent"),
        observe=lambda state: (state.held,),
        availability=lambda state, buffer: proposals,
        execute=lambda state, buffer, proposal: proposal == "set1",
        step=step,
    )


class MarkovReferenceValidation(unittest.TestCase):
    def test_storage_identity_and_copy_events_preserve_delayed_values(self):
        origin = "system.vehicle.speed"
        physical = StorageId("vehicle", ("speed",), StorageRole.PHYSICAL, "Real", origin)
        held = StorageId("sensor", ("last_speed",), StorageRole.SENSOR_HELD, "Real", origin)
        sent = StorageId("sensor", ("message", "speed"), StorageRole.PAYLOAD_SENT, "Real", origin)
        received = StorageId("controller", ("reading", "speed"),
                             StorageRole.PAYLOAD_RECEIVED, "Real", origin)
        self.assertEqual(len({physical, held, sent, received}), 4)
        other_origin = StorageId(
            "vehicle", ("speed",), StorageRole.PHYSICAL, "Real", "other provenance"
        )
        self.assertEqual(physical, other_origin)
        self.assertEqual(exact_key(physical), exact_key(other_origin))

        store = TypedStore.from_mapping({physical: 0.0, held: 0.0, sent: 0.0, received: 0.0})
        store = store.write(physical, 1.0)
        store = CopyEvent(physical, held, "sample").apply(store)
        store = CopyEvent(held, sent, "send").apply(store)
        store = store.write(physical, 2.0)
        store = CopyEvent(sent, received, "deliver").apply(store)

        self.assertEqual(store.read(physical), 2.0)
        self.assertEqual(store.read(held), 1.0)
        self.assertEqual(store.read(sent), 1.0)
        self.assertEqual(store.read(received), 1.0)

    def test_copy_event_rejects_identity_alias(self):
        physical = StorageId("vehicle", ("speed",), StorageRole.PHYSICAL, "Real")
        with self.assertRaises(ValueError):
            CopyEvent(physical, physical, "invalid")

    def test_buffer_padding_and_shift_have_exact_lag_order(self):
        spec = BufferSpec(2, 2, (0.0,), "absent")
        buffer = DecisionBuffer.initial(spec, (1.0,))
        self.assertEqual(buffer.past_observations, ((0.0,), (0.0,)))
        self.assertEqual(buffer.past_executed_actions, ("absent", "absent"))

        buffer = buffer.shift(spec, (2.0,), "left")
        buffer = buffer.shift(spec, (3.0,), "right")
        self.assertEqual(buffer.current_observation, (3.0,))
        self.assertEqual(buffer.past_observations, ((2.0,), (1.0,)))
        self.assertEqual(buffer.past_executed_actions, ("right", "left"))

    def test_exact_keys_distinguish_runtime_representations(self):
        self.assertNotEqual(exact_key(True), exact_key(1))
        self.assertNotEqual(exact_key(0.0), exact_key(-0.0))
        nan = float("nan")
        self.assertEqual(exact_key(nan), exact_key(nan))

    def test_direct_observation_is_exhaustively_proved_markov(self):
        result = prove_finite_buffer_markov(direct_observation_system())
        self.assertEqual(result.status, ProofStatus.PROVED, result)
        self.assertGreaterEqual(result.reachable_augmented_states, 2)
        self.assertGreater(result.checked_pairs, 0)

    def test_missing_action_history_has_reachable_counterexample(self):
        result = prove_finite_buffer_markov(delayed_boolean_system(0))
        self.assertEqual(result.status, ProofStatus.COUNTEREXAMPLE, result)
        self.assertEqual(result.witness.reason, "visible_outcome_difference")
        self.assertNotEqual(result.witness.left_state, result.witness.right_state)
        self.assertEqual(result.witness.left_state.held, result.witness.right_state.held)
        self.assertNotEqual(result.witness.left_state.physical,
                            result.witness.right_state.physical)

    def test_one_executed_action_reconstructs_delayed_physical_state(self):
        result = prove_finite_buffer_markov(delayed_boolean_system(1))
        self.assertEqual(result.status, ProofStatus.PROVED, result)

    def test_hidden_shield_dependency_is_a_counterexample(self):
        proposals = ("go",)
        system = FiniteDeterministicSystem(
            name="hidden_shield_dependency",
            state_domain=(False, True),
            reset_states=(False, True),
            proposal_domain=proposals,
            executed_action_domain=("left", "right"),
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: "right" if state else "left",
            step=lambda state, buffer, proposal, executed: StepOutcome(
                OutcomeKind.CONTINUE, next_state=state, next_observation=(0,)
            ),
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.COUNTEREXAMPLE, result)
        self.assertEqual(result.witness.reason, "visible_outcome_difference")

    def test_hidden_action_availability_is_a_counterexample(self):
        proposals = ("left", "right")
        system = FiniteDeterministicSystem(
            name="hidden_action_availability",
            state_domain=(False, True),
            reset_states=(False, True),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals if state else ("left",),
            execute=lambda state, buffer, proposal: proposal,
            step=lambda state, buffer, proposal, executed: StepOutcome(
                OutcomeKind.CONTINUE, next_state=state, next_observation=(0,)
            ),
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.COUNTEREXAMPLE, result)
        self.assertEqual(result.witness.reason, "action_availability_difference")

    def test_hidden_duration_is_a_counterexample(self):
        proposals = ("hold",)
        system = FiniteDeterministicSystem(
            name="hidden_duration",
            state_domain=(False, True),
            reset_states=(False, True),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: proposal,
            step=lambda state, buffer, proposal, executed: StepOutcome(
                OutcomeKind.CONTINUE,
                visible=("elapsed_ticks", 2 if state else 1),
                next_state=state,
                next_observation=(0,),
            ),
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.COUNTEREXAMPLE, result)

    def test_terminal_and_continuing_outcomes_cannot_be_conflated(self):
        proposals = ("act",)

        def step(state, buffer, proposal, executed):
            if state:
                return StepOutcome(OutcomeKind.CONTINUE,
                                   next_state=True, next_observation=(0,))
            return StepOutcome(OutcomeKind.TERMINAL, visible=("done", True))

        system = FiniteDeterministicSystem(
            name="hidden_terminal",
            state_domain=(False, True),
            reset_states=(False, True),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: proposal,
            step=step,
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.COUNTEREXAMPLE, result)

    def test_blocked_transition_fails_closed(self):
        proposals = ("act",)

        def blocked(state, buffer, proposal, executed):
            raise TransitionBlocked("no message available")

        system = FiniteDeterministicSystem(
            name="blocked",
            state_domain=(False,),
            reset_states=(False,),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: proposal,
            step=blocked,
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.INVALID, result)
        self.assertIn("blocked/non-total", result.diagnostic)

    def test_incorrect_next_observation_fails_correspondence_check(self):
        proposals = ("act",)
        system = FiniteDeterministicSystem(
            name="bad_observation",
            state_domain=(False,),
            reset_states=(False,),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: proposal,
            step=lambda state, buffer, proposal, executed: StepOutcome(
                OutcomeKind.CONTINUE, next_state=False, next_observation=(1,)
            ),
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.INVALID, result)
        self.assertIn("observation disagrees", result.diagnostic)

    def test_resource_limit_is_incomplete_not_proved(self):
        result = prove_finite_buffer_markov(
            direct_observation_system(), max_augmented_states=1
        )
        self.assertEqual(result.status, ProofStatus.INCOMPLETE, result)
        self.assertFalse(result.proved)

    def test_impure_transition_is_rejected_as_nondeterministic(self):
        proposals = ("act",)
        calls = iter(range(100))

        def impure_step(state, buffer, proposal, executed):
            return StepOutcome(
                OutcomeKind.CONTINUE,
                visible=("call", next(calls)),
                next_state=False,
                next_observation=(0,),
            )

        system = FiniteDeterministicSystem(
            name="impure_step",
            state_domain=(False,),
            reset_states=(False,),
            proposal_domain=proposals,
            executed_action_domain=proposals,
            buffer_spec=BufferSpec(0, 0, (0,), "absent"),
            observe=lambda state: (0,),
            availability=lambda state, buffer: proposals,
            execute=lambda state, buffer, proposal: proposal,
            step=impure_step,
        )
        result = prove_finite_buffer_markov(system)
        self.assertEqual(result.status, ProofStatus.INVALID, result)
        self.assertIn("step is not deterministic", result.diagnostic)


if __name__ == "__main__":
    unittest.main()
