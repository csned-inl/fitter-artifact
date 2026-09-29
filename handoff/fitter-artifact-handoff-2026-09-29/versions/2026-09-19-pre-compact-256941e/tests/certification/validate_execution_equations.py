"""Independent runtime comparisons and numerical/source-transition regressions."""
from copy import deepcopy
from functools import lru_cache
import contextlib
import io
import math
import operator
import unittest
from unittest.mock import patch

import z3

from clarity.models import models_root
from clarity.certification.execution_equations import Configuration, ExecutionEquations
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.symbolic_execution import SymbolicContext, guarded_evaluations
from clarity.certification.transition_equations import ConfigurationShape, compile_region, scalar_expression
from clarity.certification.ordered_execution import expression_record
from clarity.sysml.parser import ExpressionParser
from clarity.sysml.simulator import ExpressionEvaluator, resolve_value
from clarity.sysml.simulator_adapter import SimulatorTwin
from clarity.runtime.shield import SpecShield

NAMES = ('thermostat', 'cruise-controller-model', 'mixing-sysml-model')

@lru_cache(None)
def model(name):
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_equation_model(str(models_root()/name/'model.sysml'))


def compare_configuration(equations, config, twin, response):
    assert config.outcome == response.outcome, (config.outcome, response.outcome)
    if config.outcome == 'error':
        assert config.error == response.error, (config.error, response.error)
    else:
        assert config.inputs == response.state, (config.inputs, response.state)
    expected = {k: resolve_value(twin.engine.state, k) for k in twin.engine.state}
    actual = {k: equations.read(config.state, k) for k in config.state}
    assert actual == expected, [(k, actual.get(k), expected.get(k)) for k in set(actual)|set(expected)
                                if actual.get(k) != expected.get(k)]
    assert config.machines == twin.engine.current_sm_state
    assert config.mailboxes == twin.engine.port_mailboxes
    assert config.engine_time == twin.engine.time
    events = [{'sequence': e.sequence, 'boundary': e.boundary, 'engine_time': e.engine_time,
               'statuses': e.statuses} for e in response.events]
    assert config.events == events, (config.events, events)


class RuntimeEquations(unittest.TestCase):
    def test_complete_rule_controlled_episodes(self):
        for name in NAMES:
            with self.subTest(model=name):
                path = str(models_root()/name/'model.sysml')
                equations = ExecutionEquations(model(name).execution, .1)
                twin = SimulatorTwin(path, .1)
                with contextlib.redirect_stdout(io.StringIO()): shield = SpecShield(path)
                try:
                    twin.prepare(); response = twin.start(); config = equations.advance()
                    count = 0
                    while True:
                        compare_configuration(equations, config, twin, response)
                        if response.outcome != 'decision': break
                        action = shield.action_map[shield.requirement_action(response.state)]
                        response = twin.advance(action); config = equations.advance(config, action=action)
                        count += 1
                    self.assertEqual(response.outcome, 'terminal')
                    self.assertGreater(count, 1)
                finally: twin.stop()

    def test_all_boolean_actions_from_first_decision(self):
        for name in NAMES:
            path = str(models_root()/name/'model.sysml')
            execution = model(name).execution; equations = ExecutionEquations(execution,.1)
            with contextlib.redirect_stdout(io.StringIO()): shield = SpecShield(path)
            for action in shield.action_map.values():
                twin = SimulatorTwin(path,.1)
                try:
                    twin.prepare(); twin.start(); before = equations.advance()
                    response = twin.advance(action)
                    compare_configuration(equations,equations.advance(before,action=action),twin,response)
                finally: twin.stop()

    def test_operator_mutation_is_detected_against_runtime(self):
        source = deepcopy(model('thermostat').execution)
        node = next(n for n in source['decision_transition']['nodes'].values()
                    if n['operation']=='assign' and n['data']['source_target']==['temperatureCelcius'])
        node['data']['expression']['operator'] = '-'
        twin = SimulatorTwin(str(models_root()/'thermostat/model.sysml'),.1)
        try:
            twin.prepare(); response=twin.start()
            equations=ExecutionEquations(source,.1)
            with self.assertRaises(AssertionError): compare_configuration(equations,equations.advance(),twin,response)
        finally:twin.stop()

    def test_region_retains_cycle_edge(self):
        equations=ExecutionEquations(model('mixing-sysml-model').execution,.1)
        config=equations.advance()
        # Empty source cycle is a general graph fixture: the cyclic edge must
        # remain present rather than being unfolded a fixed number of times.
        graph=deepcopy(equations.graph)
        graph['nodes']['cycle/entry']={'operation':'begin_cycle','data':{},
            'successors':{'next':'cycle/entry'},'on_exception':'execution_error'}
        execution=deepcopy(model('mixing-sysml-model').execution)
        execution['decision_transition']=graph
        config.node='cycle/entry'
        result=ExecutionEquations(execution,.1).region(config)
        self.assertEqual(result['source_nodes'],['cycle/entry'])
        self.assertEqual(result['configuration'].node,'cycle/entry')
        self.assertIsNone(result['configuration'].outcome)


class NativeNumbers(unittest.TestCase):
    def check_value(self, actual, expected):
        expression=scalar_expression(actual)
        target=scalar_expression(expected)
        self.assertEqual(expression.sort(),target.sort())
        solver=z3.Solver(); solver.add(expression != target)
        self.assertEqual(solver.check(),z3.unsat,(expression,expected))

    def test_arithmetic_matches_python_including_rounding(self):
        context=SymbolicContext()
        cases=[(0.1,0.2),(2**53+1,1.0),(2**60+1,3),(-0.0,2.0),
               (1e308,1e308),(float('inf'),-float('inf')),(float('nan'),1.0)]
        for a,b in cases:
            for operation in (operator.add,operator.sub,operator.mul,operator.truediv):
                with self.subTest(a=a,b=b,operation=operation):
                    self.check_value(operation(context.constant(a),context.constant(b)),operation(a,b))

    def test_comparisons_match_python_for_large_int_nan_and_infinity(self):
        context=SymbolicContext()
        for a,b in [(2**53+1,float(2**53)),(2**1025,1e308),(0,float('inf')),
                    (float('nan'),0),(float('inf'),0),(-0.,0),(True,1)]:
            for operation in (operator.eq,operator.ne,operator.lt,operator.le,operator.gt,operator.ge):
                self.check_value(operation(context.constant(a),context.constant(b)),operation(a,b))

    def test_short_circuit_does_not_read_undefined_or_divide_by_zero(self):
        equations=ExecutionEquations(model('thermostat').execution,.1)
        for text in ('false and missing > 0', 'true or 1 / 0 > 1', 'false implies missing == 0'):
            ast=ExpressionParser(text).parse()
            expected=ExpressionEvaluator({},strict=True).evaluate(ast)
            self.assertEqual(equations.evaluate(expression_record(ast),{},strict=True),expected)

    def test_symbolic_guard_has_both_cases_and_unknown_is_retained(self):
        results=list(guarded_evaluations(lambda c: 1 if c.variable('b',bool) else 2))
        self.assertEqual({r['value'] for r in results},{1,2})
        with patch.object(z3.Solver,'check',return_value=z3.unknown):
            results=list(guarded_evaluations(lambda c: bool(c.variable('x',bool))))
        self.assertTrue(results)
        self.assertTrue(all(r['status']=='unknown' for r in results))

    def test_zero_division_and_overflow_are_errors(self):
        context=SymbolicContext()
        with self.assertRaises(ZeroDivisionError): context.constant(1.)/context.constant(-0.)
        with self.assertRaises(OverflowError): context.constant(2**2048)+context.constant(1.)
        with self.assertRaises(OverflowError): context.constant(2**2048)/context.constant(1)


class RegionEquations(unittest.TestCase):
    def test_source_solver_proves_complete_small_transition_and_replays_query(self):
        from source_equation_fixture import small_source_model
        from clarity.certification.source_solver import check_source_transition_closure
        result=check_source_transition_closure(small_source_model(),{'x'},dt=.1,include_artifacts=True)
        self.assertEqual(result['status'],'discharged',result)
        self.assertEqual(result['claim'],'one_step_transition_closure')
        self.assertGreater(result['acyclic_projection']['queries'],0)
        for smt in result['acyclic_projection']['artifacts']:
            solver=z3.Solver();solver.from_string(smt)
            self.assertEqual(solver.check(),z3.unsat)

    def test_source_solver_detects_dropped_information(self):
        from source_equation_fixture import small_source_model
        from clarity.certification.source_solver import SourceTransitionQuery
        from clarity.certification.transition_equations import compile_program
        fixture=small_source_model()
        program=compile_program(fixture.execution,dt=.1)
        query=SourceTransitionQuery(fixture,set(),program)
        result=query.acyclic_projection(1000)
        self.assertEqual(result['status'],'sat',result)

    def test_no_progress_claim_for_retained_internal_cycle(self):
        from source_equation_fixture import small_source_model
        from clarity.certification.source_solver import structural_progress
        from clarity.certification.transition_equations import compile_program
        fixture=small_source_model()
        fixture.execution['decision_transition']['nodes']['check']['successors']['next']='cycle/entry'
        program=compile_program(fixture.execution,dt=.1)
        self.assertFalse(structural_progress(program))

    def test_nan_and_container_sharing_survives_symbolization(self):
        value=float('nan'); attrs={'v':value}; c=Configuration('test',state={'x':value,'y':value})
        c.local_items={'message':attrs}; c.mailboxes={'port':[attrs]}
        symbolic,_=ConfigurationShape(c).instantiate(SymbolicContext())
        self.assertIs(symbolic.state['x'],symbolic.state['y'])
        self.assertIs(symbolic.local_items['message'],symbolic.mailboxes['port'][0])
        self.assertEqual(symbolic.state,dict(symbolic.state))

    def test_compiled_response_regions_cover_every_action(self):
        for name in NAMES:
            source=model(name).execution; equations=ExecutionEquations(source,.1)
            config=equations.advance(); config.events=[]
            config.node=equations.graph['nodes'][config.node]['successors']['resume'];config.outcome=None
            shape=ConfigurationShape(config)
            branches=list(compile_region(source,config,dt=.1))
            with contextlib.redirect_stdout(io.StringIO()): shield=SpecShield(str(models_root()/name/'model.sysml'))
            for action in shield.action_map.values():
                direct=equations.region(config,action=action)
                matches=[]
                for branch in branches:
                    self.assertEqual(branch['status'],'equation')
                    subs=[(v.expression,scalar_expression(shape.values[i])) for i,v in branch['value']['input_values'].items()]
                    subs += [(v.expression,scalar_expression(action[k])) for k,v in branch['value']['actions'].items()]
                    guard=z3.simplify(z3.substitute(z3.And(*branch['guards']),*subs))
                    if z3.is_false(guard):continue
                    self.assertTrue(z3.is_true(guard),guard)
                    matches.append(branch)
                    output=ConfigurationShape(direct['configuration'])
                    self.assertEqual(output.layout,branch['output_shape'].layout)
                    self.assertEqual(direct['source_nodes'],branch['value']['source_nodes'])
                    for expected,actual in zip(output.values,branch['output_shape'].values):
                        expression=z3.substitute(scalar_expression(actual),*subs)
                        solver=z3.Solver();solver.add(expression != scalar_expression(expected))
                        self.assertEqual(solver.check(),z3.unsat)
                self.assertEqual(len(matches),1)

if __name__=='__main__':unittest.main()
