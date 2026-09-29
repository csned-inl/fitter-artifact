"""Buffer selection must consume source proofs, not scalar copy heuristics."""
import unittest
from unittest.mock import patch
from clarity.certification.source_buffer import select_source_buffer


class SourceBuffer(unittest.TestCase):
    def run_search(self, response, **kw):
        with patch('clarity.certification.source_buffer.check_source_history_reconstruction',
                   side_effect=response) as check:
            result = select_source_buffer(object(), {'physical', 'held'}, dt=.1,
                                          max_obs=1, max_act=2, **kw)
            return result, check.call_args_list

    def test_unknown_does_not_stop_search_or_establish_minimality(self):
        def response(model, q, **kw):
            passed = (kw['b_obs'], kw['b_act']) == (0, 2)
            return dict(status='discharged' if passed else 'unknown',
                        claim='source_history_reconstruction' if passed else 'not_claimed',
                        b_obs=kw['b_obs'], b_act=kw['b_act'])
        result, calls = self.run_search(response)
        self.assertEqual(result['selected'], (0, 2))
        self.assertEqual([(c.kwargs['b_obs'], c.kwargs['b_act']) for c in calls], [(0,0),(0,1),(0,2)])
        self.assertIn('unknown is not insufficiency', result['minimality_claim'])

    def test_wrong_claim_and_wrong_buffer_are_rejected(self):
        for claim, act in [('raw_observation_reconstruction', 0), ('source_history_reconstruction', 9)]:
            result, calls = self.run_search(lambda *a, **kw: dict(
                status='discharged', claim=claim, b_obs=kw['b_obs'], b_act=act))
            self.assertIsNone(result['selected'])
            self.assertEqual(len(calls), 6)

    def test_explicit_buffer_is_only_query(self):
        result, calls = self.run_search(lambda *a, **kw: dict(status='unknown'), b_obs=2, b_act=4)
        self.assertIsNone(result['selected'])
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].kwargs['b_obs'], calls[0].kwargs['b_act']), (2,4))

    def test_partial_explicit_buffer_rejected(self):
        with self.assertRaises(ValueError):
            self.run_search(lambda *a, **kw: self.fail('query launched'), b_obs=1)

    def test_source_gate_keeps_correspondence_blockers(self):
        from clarity.certification.certificate_generation import _theorem_gate
        from clarity.certification.equations import Diagnostic
        history = dict(status='discharged', claim='source_history_reconstruction',
                       b_obs=0, b_act=1, graph_sha256='source')
        proof = dict(proof_engine='source_history_v1', passes=True,
                     b_obs=0, b_act=1, graph_sha256='source')
        kwargs = dict(proof=proof, equation_proof=None, profile_obligations_discharged=True,
                      solver_result=dict(status='discharged', claim='one_step_transition_closure'),
                      source_history_result=history)
        self.assertTrue(_theorem_gate(blocking=[], **kwargs)['full_mdp_theorem_claim_allowed'])
        self.assertFalse(_theorem_gate(blocking=[Diagnostic('error', 'missing_transition_equation', 'missing')],
                                      **kwargs)['full_mdp_theorem_claim_allowed'])
        history['b_act'] = 2
        self.assertFalse(_theorem_gate(blocking=[], **kwargs)['full_mdp_theorem_claim_allowed'])

    def test_checker_checks_source_target_and_buffer(self):
        from clarity.certification.certificate_checker import _check_reconstruction_trace
        from copy import deepcopy
        history = dict(status='discharged', claim='source_history_reconstruction',
                       b_obs=0, b_act=1, graph_sha256='source')
        certificate = dict(proof=dict(proof_engine='source_history_v1', passes=True,
            b_obs=0, b_act=1, graph_sha256='source', target=['held', 'physical'],
            evidence_source='solver_advisory.source_history_reconstruction'),
            buffer=dict(b_obs=0, b_act=1), sets=dict(q=['held', 'physical']),
            execution=dict(decision_transition=dict(sha256='source')),
            solver_advisory=dict(source_history_reconstruction=history))
        self.assertEqual(_check_reconstruction_trace(certificate), [])
        for key, value in [('b_act',0), ('target',['physical']), ('graph_sha256','other')]:
            altered = deepcopy(certificate)
            altered['proof'][key] = value
            self.assertTrue(_check_reconstruction_trace(altered), key)

    def test_rejected_certificate_never_launches_source_solver(self):
        from clarity.certification.certificate_checker import check_certificate
        with patch('clarity.certification.certificate_checker._check_source_evidence',
                   side_effect=AssertionError('expensive replay of rejected certificate')):
            errors=check_certificate({'result':'FAIL'},check_hash=False)
        self.assertTrue(errors)

    def test_structural_acceptance_still_requires_independent_source_check(self):
        from contextlib import ExitStack
        from clarity.certification.certificate_checker import check_certificate
        certificate=dict(proof=dict(proof_engine='source_history_v1'))
        with ExitStack() as stack:
            for method in ('_check_schema_and_equations','_check_theorem_gate',
                           '_check_mdp_obligations','_check_reconstruction_trace'):
                stack.enter_context(patch('clarity.certification.certificate_checker.'+method,return_value=[]))
            replay=stack.enter_context(patch('clarity.certification.certificate_checker._check_source_evidence',
                                            return_value=['source replay rejected']))
            self.assertEqual(check_certificate(certificate,check_hash=False),['source replay rejected'])
            self.assertEqual(replay.call_count,1)

    def test_generation_uses_source_search_without_legacy_fallback(self):
        from pathlib import Path
        from clarity.certification.certificate_generation import build_certificate_for_path
        fixture = Path(__file__).parents[1]/'discretization/fixtures/held_constant.sysml'
        def selected(model, q, **kw):
            history = dict(status='discharged', claim='source_history_reconstruction',
                           b_obs=0, b_act=1, graph_sha256=model.execution['decision_transition']['sha256'])
            return dict(selected=(0,1), proof=history, attempts=[], minimality_claim='test fixture')
        with patch('clarity.certification.certificate_generation._strict_search', side_effect=AssertionError('legacy search')), \
             patch('clarity.certification.certificate_generation.equation_search', side_effect=AssertionError('legacy search')), \
             patch('clarity.certification.certificate_generation.one_step_transition_closure',
                   return_value=dict(status='discharged', claim='one_step_transition_closure')), \
             patch('clarity.certification.source_buffer.select_source_buffer', side_effect=selected) as search:
            certificate = build_certificate_for_path(str(fixture), dt=.1)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(certificate['buffer'], dict(b_obs=0,b_act=1))
        self.assertEqual(certificate['proof']['proof_engine'], 'source_history_v1')
        self.assertIsNone(certificate['equation_proof'])
        self.assertTrue(certificate['diagnostics']['blocking'])
        self.assertNotEqual(certificate['result'], 'PASS')

    def test_generation_does_not_search_after_failed_transition_proof(self):
        from pathlib import Path
        from clarity.certification.certificate_generation import build_certificate_for_path
        fixture = Path(__file__).parents[1]/'discretization/fixtures/held_constant.sysml'
        with patch('clarity.certification.certificate_generation.one_step_transition_closure',
                   return_value=dict(status='unknown', claim='not_claimed_by_this_artifact')), \
             patch('clarity.certification.source_buffer.select_source_buffer', side_effect=AssertionError('dependent query')):
            certificate = build_certificate_for_path(str(fixture), dt=.1)
        self.assertEqual(certificate['search']['attempts'], [])
        self.assertNotEqual(certificate['result'], 'PASS')

    def test_source_delayed_boolean_proof_selects_required_action(self):
        from validate_source_history import delayed_boolean_model
        model = delayed_boolean_model()
        # This fixture has an explicit observation contract. Production source
        # contracts still come from the recorded runtime normalization file.
        from clarity.certification.source_solver import check_source_history_reconstruction
        def check(model, q, **kw):
            return check_source_history_reconstruction(model, q, observation_scale=1., **kw)
        with patch('clarity.certification.source_buffer.check_source_history_reconstruction', side_effect=check):
            result = select_source_buffer(model, {'x','held'}, dt=.1, max_obs=0, max_act=1)
        self.assertEqual(result['selected'], (0,1), result)
        self.assertFalse(result['attempts'][0]['passes'])
        self.assertTrue(result['attempts'][1]['passes'])


if __name__ == '__main__':
    unittest.main()
