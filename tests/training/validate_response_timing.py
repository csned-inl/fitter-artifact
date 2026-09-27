"""Response obligations run after commands; completion cannot skip that response."""
import unittest
from clarity.models import models_root
from clarity.runtime.env import SysMLEnv
from clarity.runtime.shield import SpecShield

class ResponseTiming(unittest.TestCase):
    def environment(self):
        path = str(models_root() / 'mixing-sysml-model/model.sysml')
        env = SysMLEnv(path, dt=.1, max_steps=5000, phase=2)
        self.addCleanup(env.close)
        shield = SpecShield(path)
        initial = env.reset_with_result(seed=1833613192)
        self.assertEqual(initial.outcome, 'decision')
        self.assertTrue(initial.events)
        self.assertTrue(all(e.boundary == 'initialization' for e in initial.events))
        self.assertEqual(len(initial.events[-1].statuses), 4)
        return env, shield

    def test_unanswered_liveness_is_not_failed_but_wrong_response_is(self):
        env, shield = self.environment()
        _, _, done, info = env.step(0)
        self.assertTrue(done)
        self.assertEqual(info['outcome'], 'VIOLATION')
        self.assertFalse(info['statuses']['Fluid Transfer Liveness']['status'])
        self.assertTrue(any(e.boundary == 'cycle_end' and
                            e.statuses['Fluid Transfer Liveness']['status'] is False
                            for e in info['requirement_events']))

    def finish(self, incorrect_final):
        env, shield = self.environment()
        for _ in range(5000):
            final_response = env.model_inputs['done']
            action = shield.requirement_action(env.model_inputs)
            if final_response and incorrect_final:
                action = next(a for a, outputs in env.action_map.items()
                              if all(outputs.values()))
            _, _, done, info = env.step(action)
            if done:
                self.assertTrue(final_response)
                self.assertTrue(any(e.boundary == 'cycle_end' for e in info['requirement_events']))
                if incorrect_final:
                    self.assertEqual(info['outcome'], 'VIOLATION')
                    self.assertFalse(info['statuses']['Fluid Transfer Termination Safety']['status'])
                else:
                    self.assertEqual(info['outcome'], 'SUCCESS')
                    self.assertTrue(all(v['status'] for v in info['statuses'].values()))
                    for tank in (1, 2):
                        self.assertFalse(env._twin.engine.state[f'system::pump{tank}::isRunning'])
                return
        self.fail('completion was not reached')

    def test_final_pump_off_response_is_applied_before_success(self):
        self.finish(False)

    def test_wrong_final_response_is_counted_as_safety_violation(self):
        self.finish(True)

if __name__ == '__main__':
    unittest.main()
