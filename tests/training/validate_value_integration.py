"""Integration regression tests; passing these is not a model safety theorem."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
import numpy as np
from clarity.models import models_root
from clarity.sysml.parser import ExpressionParser, SysMLParser
from clarity.sysml.simulator import ExpressionEvaluator, SimulationEngine
from clarity.runtime.env import SysMLEnv
from clarity.runtime.requirements import RequirementEvent, RequirementLedger, summarize_events, ResetUnavailable
from clarity.certification.ordered_execution import build_execution_description, validate_execution_description
from clarity.certification.equations import EquationModel, Equation, Var, Const, Op
from clarity.certification.solver import infer_sorts, one_step_transition_closure
from clarity.training.reduced.episode import collect_episode, Episode, _record_events, measure_episodes
from clarity.training.recurrent.ppo_update import ppo_update
from clarity.training.reduced.collection import (DiscreteRuntime, CollectionSettings,
    build_episode_collector, make_episode_jobs, generate_oracle_data_with_backend)
from clarity.training.reduced.buffered_env import BufferedDiscreteEnv
from clarity.training.reduced.policy import MLPActorCritic
from clarity.training.reduced.composite import ProgramShieldComposite
from clarity.runtime.oracle import extract_interface

FIXTURE = Path(__file__).parents[1] / 'discretization/fixtures/held_constant.sysml'

class IntegrationTests(unittest.TestCase):
    def test_two_feeders_preserve_the_source_inlet_sum(self):
        path = Path(models_root()) / 'mixing-sysml-model/model.sysml'
        parser = SysMLParser(str(path)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        for index in (1, 2):
            engine.state[f'system::pump{index}::isRunning'] = True
            engine.state[f'system::valve{index}::isOpen'] = True
        engine.solver.solve(engine.current_sm_state)
        self.assertEqual(engine.state['system::fillingTank::inlet::flowRateMl'], 20)
        constraint = next(c for c in parser.parsed_constraints if c.name == 'fillingFillRate')
        self.assertIs(ExpressionEvaluator(engine.state, constraint.context,
            parser.ref_bindings, parser.system_part, strict=True).evaluate(constraint.expression), True)

    def test_stable_overwrite_cannot_masquerade_as_constraint_solution(self):
        from clarity.sysml.parser import Constraint
        from clarity.sysml.simulator import ConstraintSolver
        solver = ConstraintSolver({'system::x': 2}, 'system')
        for index, value in enumerate((1, 2)):
            text = f'x == {value}'
            solver.add_constraint(Constraint(str(index), ExpressionParser(text).parse(), text, 'system'))
        with self.assertRaisesRegex(ValueError, 'source constraints false after propagation'):
            solver.solve({})

    def test_mixing_completed_trace_conserves_total_volume(self):
        from clarity.runtime.shield import SpecShield
        path = Path(models_root()) / 'mixing-sysml-model/model.sysml'
        parser = SysMLParser(str(path)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        shield = SpecShield(str(path))
        decisions = []
        def controller(inputs):
            decisions.append(dict(inputs))
            return shield.action_map[shield.requirement_action(inputs)]
        engine.model = controller
        tanks = ('feederTank1', 'feederTank2', 'fillingTank')
        total = lambda: sum(engine.state[f'system::{name}::currentLevelMl'] for name in tanks)
        initial_total = total()
        for _ in range(5000):
            engine.step(.1)
            self.assertEqual(total(), initial_total)
            if decisions and decisions[-1]['done']:
                break
        else:
            self.fail('source controller did not complete within the existing episode bound')

    def source(self, change=lambda s:s):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'model.sysml'; path.write_text(change(FIXTURE.read_text()))
        return str(path)

    def env(self, path=None, **kwargs):
        env = SysMLEnv(path or str(FIXTURE), dt=.1, observation_scale=1, **kwargs)
        self.addCleanup(env.close)
        return env

    def test_implication_matches_reference_grammar_associativity(self):
        evaluate = ExpressionEvaluator({}, strict=True).evaluate
        # Left: (false => true) => false == false. Right association gives true.
        self.assertIs(evaluate(ExpressionParser('false implies true implies false').parse()), False)
        self.assertIs(evaluate(ExpressionParser('false implies (true implies false)').parse()), True)
        self.assertIs(evaluate(ExpressionParser('(false implies true) implies false').parse()), False)

    def test_shield_keeps_source_precedence(self):
        from clarity.runtime.shield import SpecShield
        from clarity.certification.ordered_execution import expression_record
        path = Path(models_root()) / 'cruise-controller-model/model.sysml'
        parser = SysMLParser(str(path)); parser.parse()
        part = parser.part_defs[parser.part_instances[parser.controller_part].part_type]
        text = next(row[3] for row in part.requirements if 'NeuralRequirement' in row[4])
        shield = SpecShield(str(path))
        self.assertEqual(expression_record(shield.req_ast), expression_record(ExpressionParser(text).parse()))
        # Keep the old ambiguous expression as a negative control: the runtime
        # must never reintroduce its former implicit parenthesis repair.
        old_text = path.read_text().replace(
            '((p.targetSpeed > p.currentSpeedMps + toleranceMps and p.gapMeters >= safeFollowingDistanceMeters) == p.applyThrottle)',
            '(p.targetSpeed > p.currentSpeedMps + toleranceMps and p.gapMeters >= safeFollowingDistanceMeters == p.applyThrottle)')
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        old_path = Path(temporary.name) / 'model.sysml'; old_path.write_text(old_text)
        old_shield = SpecShield(str(old_path))
        self.assertEqual(old_shield.requirement_actions({'targetSpeed':0, 'currentSpeedMps':10, 'gapMeters':100}), [])

    def test_cruise_source_contract_admits_expected_boundary_actions(self):
        from clarity.runtime.shield import SpecShield
        shield = SpecShield(str(Path(models_root()) / 'cruise-controller-model/model.sysml'))
        for target, speed, gap, throttle, brake in (
            (20, 10, 100, True, False),
            (10, 20, 100, False, True),
            (10, 10, 100, False, False),
            (12, 10, 100, False, False),
            (8, 10, 100, False, False),
            (20, 10, 1, False, True),
        ):
            inputs = {'targetSpeed':target, 'currentSpeedMps':speed, 'gapMeters':gap}
            actions = shield.requirement_actions(inputs)
            with self.subTest(inputs=inputs):
                self.assertEqual(len(actions), 1)
                self.assertEqual(shield.action_map[actions[0]],
                    {'applyThrottle':throttle, 'applyBrake':brake})

    def test_reject_partial_expression(self):
        for expr in ('(true', 'true junk', '1 +', '(1 < 2))'):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                ExpressionParser(expr).parse()

    def test_reject_boolean_numeric_mix(self):
        for expr in ('not 1', 'true + 1', 'false == 0', '1 and true'):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                ExpressionParser(expr).parse()

    def test_short_circuit_keeps_defined_boolean_meaning(self):
        evaluate = ExpressionEvaluator({}, strict=True).evaluate
        self.assertIs(evaluate(ExpressionParser('false and missing').parse()), False)
        self.assertIs(evaluate(ExpressionParser('false implies missing').parse()), True)
        with self.assertRaises(ValueError): evaluate(ExpressionParser('true and missing').parse())
        with self.assertRaises(ValueError): evaluate(ExpressionParser('1 / 0').parse())

    def test_initial_failure_is_zero_action_episode(self):
        path = self.source(lambda s: s.replace('s.controller.sampled < 0.0 implies s.controller.active', 'false'))
        env = self.env(path, phase=2)
        episode = collect_episode(env, None, reset_seed=7)
        self.assertEqual(episode.outcome, 'VIOLATION')
        self.assertEqual(len(episode.actions), 0)
        self.assertEqual(len(episode.violations), 1)
        self.assertEqual(measure_episodes([episode]).n_safety, 1)
        self.assertGreater(episode.requirement_checks['Respond to negative reading'], 0)
        self.assertIsNone(env.reset_result.observation)

    def test_terminal_reset_never_requests_an_action(self):
        path = self.source(lambda s: s.replace('in done := false;', 'in done := true;'))
        episode = collect_episode(self.env(path, phase=2), None)
        self.assertEqual(episode.outcome, 'SUCCESS'); self.assertEqual(episode.actions, [])

    def test_missing_input_reset_returns_error(self):
        path = self.source(lambda s: s.replace('in reading := sampled;', 'in reading := missing;'))
        episode = collect_episode(self.env(path, phase=2), None)
        self.assertEqual(episode.outcome, 'ERROR'); self.assertTrue(episode.evaluation_errors)
        self.assertEqual(episode.actions, [])

    def test_compat_reset_exposes_failure(self):
        path = self.source(lambda s: s.replace('in done := false;', 'in done := true;'))
        with self.assertRaises(ResetUnavailable): self.env(path).reset()

    def test_first_decision_has_no_forced_controls(self):
        env = self.env(phase=2)
        first = env.reset_with_result(seed=5)
        self.assertEqual(first.outcome, 'decision')
        self.assertEqual(env.model_inputs['reading'], 1)
        self.assertEqual(env._twin.engine.state['system::controller::active'], False)
        self.assertFalse(any('policyCall::on' in event.source for event in first.events))
        env.step(1)
        self.assertEqual(env._twin.engine.state['system::controller::active'], True)
        second = env.reset_with_result(seed=5)
        self.assertEqual(second.events[0].sequence, 0)
        self.assertNotEqual(second.events[0].episode_id, first.events[0].episode_id)
        self.assertEqual(env._twin.engine.state['system::controller::active'], False)

    def test_live_scenario_precedes_first_sensor(self):
        path = Path(models_root()) / 'mixing-sysml-model/model.sysml'
        env = SysMLEnv(str(path), dt=.1, phase=1); self.addCleanup(env.close)
        initial = env.reset_with_result(seed=123)
        self.assertEqual(initial.outcome, 'decision')
        self.assertEqual([event.boundary for event in initial.events],
                         ['initialization', 'decision'])
        current = env._twin.engine.requirement_statuses()
        self.assertEqual(initial.events[-1].statuses['Fluid Transfer Liveness']['status'],
                         current['Fluid Transfer Liveness']['status'])
        inputs = env.model_inputs
        self.assertEqual(inputs['tank1VolumeMl'], inputs['tank1OriginalMl'])
        self.assertEqual(inputs['tank2VolumeMl'], inputs['tank2OriginalMl'])
        self.assertEqual(env._step_count, 0)

    def test_failure_cannot_be_overwritten_by_later_true(self):
        def event(i, value):
            return RequirementEvent(1, i, 'cycle_end', '', i * .1,
                {'P': {'kind':'Prohibition', 'status':value, 'error':None}})
        events = (event(0, True), event(1, False), event(2, True))
        self.assertIs(summarize_events(events)['P']['status'], False)
        ep = Episode(); failed = set(); _record_events(ep, events, failed)
        self.assertEqual(failed, {'P'})
        with self.assertRaises(ValueError): _record_events(ep, events[-1:], failed)

    def test_error_cannot_become_safety_success(self):
        ep = Episode(outcome='SUCCESS', evaluation_errors=['missing input'])
        data = measure_episodes([ep]); self.assertEqual(data.n_errors, 1)
        self.assertEqual(data.n_succ, 0)

    def test_zero_action_ppo_never_updates_weights(self):
        class NoCalls:
            def __getattr__(self, name): raise AssertionError(name)
        result = ppo_update(NoCalls(), NoCalls(), [Episode(outcome='VIOLATION')], 1)
        self.assertEqual(result['updates'], 0)

    def test_old_integer_declaration_is_not_silently_real(self):
        model = EquationModel('typed', {'i'}, set(), declared_sorts={'i':'Int'})
        model.transitions['i'] = Equation('i', Op('+', (Var('i'), Const(.5))), 'transition')
        sorts, errors = infer_sorts(model)
        self.assertEqual(sorts['i'], 'Int'); self.assertTrue(errors)

    def test_missing_transition_solver_rejects(self):
        model = EquationModel('missing', {'x'}, set())
        result = one_step_transition_closure(model, {'x'}, timeout_ms=1000)
        self.assertNotEqual(result['status'], 'discharged')

    def test_source_inventory_keeps_all_metadata_and_rejects_mutation(self):
        path = self.source(lambda s: s.replace('#Prohibition requirement', '#Audit #Prohibition requirement'))
        parser = SysMLParser(path); parser.parse()
        record = build_execution_description(parser).to_dict()
        self.assertEqual(record['property_inventory'][0]['tags'], ['Audit','Prohibition'])
        self.assertEqual(validate_execution_description(record, path), [])
        record['property_inventory'][0]['expression']['operator'] = 'or'
        self.assertTrue(validate_execution_description(record, path))

    def test_source_type_diagnostics_are_explicit(self):
        path = Path(models_root()) / 'mixing-sysml-model/model.sysml'
        parser = SysMLParser(str(path)); parser.parse()
        codes = [d['code'] for d in build_execution_description(parser).diagnostics]
        self.assertNotIn('source_assignment_type', codes)
        self.assertNotIn('integer_continuous_state', codes)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        invalid = Path(temporary.name) / 'model.sysml'
        invalid.write_text(path.read_text().replace('currentLevelMl : Real;', 'currentLevelMl : Integer;'))
        parser = SysMLParser(str(invalid)); parser.parse()
        codes = [d['code'] for d in build_execution_description(parser).diagnostics]
        self.assertEqual(codes.count('integer_continuous_state'), 3)

    def test_fractional_volume_reaches_controller_without_integer_conversion(self):
        path = Path(models_root()) / 'mixing-sysml-model/model.sysml'
        # A unit-test timestep that makes the source's flow increments fractional.
        # Full artifact and saved-checkpoint evaluations keep their original dt.
        env = SysMLEnv(str(path), dt=.037, phase=1, observation_scale=150)
        self.addCleanup(env.close)
        initial = env.reset_with_result(seed=123)
        self.assertEqual(initial.outcome, 'decision')
        _, _, _, info = env.step(15)
        self.assertNotEqual(info['outcome'], 'ERROR')
        inputs = env.model_inputs
        self.assertNotEqual(inputs['tank1VolumeMl'], int(inputs['tank1VolumeMl']))
        self.assertEqual(inputs['tank1VolumeMl'], env._twin.engine.state['system::controller::volume1Res::response'])
        self.assertEqual(inputs['tank1VolumeMl'], env._twin.engine.state['system::controller::observedLevel1'])

    def test_approved_source_edits_preserve_all_safety_expressions(self):
        from clarity.certification.ordered_execution import source_requirement_inventory
        expected = json.loads((FIXTURE.parent / 'bundled-safety-expressions.json').read_text())
        for model, records in expected.items():
            parser = SysMLParser(str(Path(models_root()) / model / 'model.sysml')); parser.parse()
            actual = [{k:v for k,v in row.items() if k in
                ('name','tags','subject','subject_type','expression_text')}
                for row in source_requirement_inventory(parser)]
            self.assertEqual(actual, records)

    def test_12_original_properties_inventory(self):
        count = 0
        for path in Path(models_root()).glob('*/model.sysml'):
            parser = SysMLParser(str(path)); parser.parse()
            count += len(build_execution_description(parser).property_inventory)
        self.assertEqual(count, 12)

    def test_message_payload_is_snapshot(self):
        parser = SysMLParser(str(FIXTURE)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        parser.connects = [('controller.out', 'controller.input')]
        payload = {'value': 2}; engine._send_item('Msg', payload, 'system::controller', 'out')
        payload['value'] = 9
        self.assertEqual(engine.port_mailboxes['system::controller::input'][0]['attrs']['value'], 2)

    def test_serial_and_process_count_initial_failures_identically(self):
        path = self.source(lambda s: s.replace('s.controller.sampled < 0.0 implies s.controller.active', 'false'))
        interface = extract_interface(path)
        runtime = DiscreteRuntime(path, .1, 3, 2, 0, 0, 1, 2, 4, observation_scale=1)
        policy = MLPActorCritic(1, 2, 4, seed=0)
        composite = ProgramShieldComposite(policy, interface['spec_shield'], interface['obs_names'])
        summaries = []
        for settings in (CollectionSettings(), CollectionSettings('process', 2)):
            with build_episode_collector(settings, runtime, composite) as collector:
                summaries.append(collector.evaluate(policy, make_episode_jobs(4, seed_base=8)))
        for summary in summaries:
            self.assertEqual(summary.n_episodes, 4); self.assertEqual(summary.n_steps, 0)
            self.assertEqual(summary.safety_violation_rate, 1)
            self.assertEqual(summary.evaluation_error_rate, 0)
        self.assertEqual(summaries[0].requirement_checks, summaries[1].requirement_checks)

    def test_missing_preservation_proofs_do_not_authorize_safety(self):
        from clarity.discretization.certificates.verification.preservation import (
            required_preservation_inventory, verify_preservation_inventory)
        record = {'inventory': required_preservation_inventory(FIXTURE), 'evidence': {}}
        report = verify_preservation_inventory(record, FIXTURE)
        self.assertEqual(report['errors'], [])
        self.assertFalse(report['verified'])
        self.assertEqual(len(report['unproved'][0]['obligations']), 6)
        record['inventory'][0]['obligations'].pop()
        self.assertTrue(verify_preservation_inventory(record, FIXTURE)['errors'])

    def test_oracle_errors_keep_serial_and_worker_ledgers(self):
        path = self.source(lambda s: s.replace('(p.reading < 0.0) == p.on',
                                               'p.reading < 0.0 and p.on'))
        interface = extract_interface(path)
        runtime = DiscreteRuntime(path, .1, 3, 1, 0, 0, 1, 2, 4, observation_scale=1)
        for settings, expected_attempts in ((CollectionSettings(), 1),
                                            (CollectionSettings('process', 2), 2)):
            reports = []
            with self.assertRaisesRegex(RuntimeError, 'oracle collection failed'):
                generate_oracle_data_with_backend(runtime, settings, interface, 4,
                    max_resets=2, attempt_reports=reports)
            self.assertEqual(len(reports), expected_attempts)
            for report in reports:
                self.assertEqual(report['outcome'], 'ERROR')
                self.assertEqual(report['actions'], 0)
                self.assertGreater(report['checks']['Respond to negative reading'], 0)
                self.assertIn('determined 0 actions', report['exception'])
                self.assertTrue(report['errors'])

    def test_oracle_positive_serial_worker_dataset_agreement(self):
        runtime = DiscreteRuntime(str(FIXTURE), .1, 3, 1, 0, 0, 1, 2, 4, observation_scale=1)
        interface = extract_interface(str(FIXTURE))
        data = []
        for settings in (CollectionSettings(), CollectionSettings('process', 2)):
            reports = []
            data.append(generate_oracle_data_with_backend(runtime, settings, interface, 6,
                max_resets=2, attempt_reports=reports))
            self.assertEqual(len(reports), 2)
            self.assertFalse(any(row['errors'] for row in reports))
        np.testing.assert_array_equal(data[0][0], data[1][0])
        np.testing.assert_array_equal(data[0][1], data[1][1])

    def test_recurrent_oracle_retains_boundary_failures(self):
        from clarity.training.recurrent.oracle_data import generate_oracle_data
        path = self.source(lambda s: s.replace(
            's.controller.sampled < 0.0 implies s.controller.active',
            's.controller.irrelevantClock < 0.15'))
        env = self.env(path, phase=1)
        data, actions = generate_oracle_data(extract_interface(path), env, 3, max_steps=3)
        self.assertEqual(len(actions), 3)
        report = env.oracle_attempt_reports[0]
        self.assertIn('Respond to negative reading', report['violations'])
        self.assertGreater(report['checks']['Respond to negative reading'], 1)
        self.assertEqual(report['actions'], 2)

    def test_partial_assignments_do_not_reject_but_boundary_failures_do(self):
        for complete in (True, False):
            with self.subTest(complete_update=complete):
                def change(text):
                    text = text.replace('attribute physical : Real := 1.0;',
                        'attribute physical : Real := 1.0;\n'
                        'attribute first : Real := 0.0;\n'
                        'attribute second : Real := 0.0;')
                    text = text.replace('assign sampled := physical;',
                        'assign sampled := physical;\nassign first := first + 1.0;\n'
                        'assign second := ' + ('first' if complete else 'second') + ';')
                    return text.replace('s.controller.sampled < 0.0 implies s.controller.active',
                                        's.controller.first == s.controller.second')
                env = self.env(self.source(change), phase=2)
                result = env.reset_with_result(seed=7)
                self.assertEqual([e.boundary for e in result.events],
                                 ['initialization', 'decision'])
                self.assertEqual(env._twin.engine.state['system::controller::first'], 1)
                self.assertEqual(env._twin.engine.state['system::controller::second'], int(complete))
                self.assertEqual(result.outcome, 'decision' if complete else 'violation')
                self.assertEqual(bool(result.violations), not complete)
                if complete:
                    _, reward, done, info = env.step(0)
                    self.assertEqual(reward, -.01)
                    self.assertFalse(done)
                    self.assertEqual([e.boundary for e in info['requirement_events']],
                                     ['cycle_end', 'decision'])
                    self.assertTrue(all(row['status'] for row in info['statuses'].values()))

    def test_accept_copies_payload_without_requirement_event(self):
        from clarity.sysml.parser import AcceptStmt
        parser = SysMLParser(str(FIXTURE)); parser.parse()
        engine = SimulationEngine(parser); engine.initialize()
        engine.requirement_ledger = RequirementLedger()
        port = 'system::controller::input'
        engine.port_mailboxes[port] = [{'type': 'Msg', 'attrs': {'response': 3.0}}]
        engine._execute_action_stmts([AcceptStmt('packet', 'Msg', 'input')],
                                     'system::controller', {})
        self.assertEqual(engine.state['system::controller::packet::response'], 3.0)
        self.assertEqual(engine.port_mailboxes[port], [])
        self.assertEqual(engine.requirement_ledger.drain(), ())
        engine.record_requirements('cycle_end')
        events = engine.requirement_ledger.drain()
        self.assertEqual([e.boundary for e in events], ['cycle_end'])
        self.assertIn('Respond to negative reading', events[0].statuses)

    def test_smt_text_is_bound_to_the_expression(self):
        from clarity.discretization.certificates.verification.reachability import _recheck_smt_no_solution
        query = {'query_expression': {'type':'const', 'value':False},
                 'query_smt2':'(assert false)'}
        self.assertEqual(_recheck_smt_no_solution(query), [])
        query['query_expression']['value'] = True
        self.assertTrue(any('does not encode' in x for x in _recheck_smt_no_solution(query)))

    def test_factored_child_is_bound_to_expected_parent(self):
        from clarity.discretization.certificates.verification.factored import _verify_lazy_factored_stage
        from clarity.discretization.certificates.verification.expressions import _serialized_expression_hash as digest
        expr = {'type':'const', 'value':False}
        leaf = {'rule':'constant_false_factored_leaf_v1', 'expression':expr,
                'expression_sha256':digest(expr)}
        tree = {'rule':'exact_boolean_normalization_v1', 'expression':expr,
                'expression_sha256':digest(expr), 'normalized_expression':expr,
                'normalized_expression_sha256':digest(expr), 'outcome':'CERTIFIED', 'proof':leaf}
        stage = {'checker':'linear', 'proof':{'rule':'lazy_factored_formula_coverage_v1',
            'source_expression':expr, 'source_expression_sha256':digest(expr),
            'coverage_complete':True, 'boolean_variables':[], 'checker':'linear', 'tree':tree}}
        self.assertEqual(_verify_lazy_factored_stage(stage, {'expression':expr}), [])
        wrong = {'type':'const','value':True}
        leaf['expression'] = wrong; leaf['expression_sha256'] = digest(wrong)
        self.assertTrue(any('expected obligation' in x for x in _verify_lazy_factored_stage(stage, {'expression':expr})))

    def test_direct_training_requires_both_evidence_files(self):
        from clarity.training.authorization import require_training_evidence
        with self.assertRaisesRegex(ValueError, 'requires both'):
            require_training_evidence(str(FIXTURE), .1, None, None)

def validate_value_integration():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError('value-state integration regression tests failed')
    return {'tests': result.testsRun, 'failures': 0, 'errors': 0}


if __name__ == '__main__':
    validate_value_integration()
