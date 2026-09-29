"""Source-history reconstruction, padding and actual observation encoding."""
import unittest

import numpy as np
import z3

from clarity.certification.lazy_expressions import Value, Status, Term, scalar
from clarity.certification.lazy_graph import compile_graph
from clarity.certification.lazy_solver import LazyTransitionQuery
from clarity.certification.source_history import SourceHistoryQuery, encode_observation, push, push_slots
from clarity.runtime.env import SysMLEnv
from source_equation_fixture import small_source_model
from clarity.certification.lazy_graph import fields


def delayed_boolean_model():
    model = small_source_model()
    graph = model.execution['decision_transition']
    nodes = graph['nodes']
    ref = lambda key: {'kind': 'reference', 'path': ['system', key]}
    nodes['initial/entry']['data']['parameters'][0]['value'] = False
    nodes['initial/entry']['data']['parameters'].append(
        {'qualified_name': 'system::held', 'cli_name': 'held', 'value': False})
    graph['part_attribute_types']['system'].update(x='Boolean', held='Boolean')
    graph['storage']['system::held'] = {'writers': ['initial/entry', 'sample']}
    nodes['cycle/entry']['successors']['next'] = 'sample'
    nodes['sample'] = {'operation': 'assign', 'successors': {'next': 'update'},
        'on_exception': 'execution_error', 'data': {'context': 'system',
        'target': 'system::held', 'source_storage_target': 'system::held',
        'source_target': ['held'], 'expression': ref('x')}}
    nodes['update']['data']['expression'] = ref('go')
    nodes['check']['data']['expressions']['nonnegative'] = {'kind': 'literal', 'value': True}
    nodes['decision']['data']['inputs']['x'] = ref('held')
    model.state.add('held')
    model.value_semantics['decision_updates']['held'] = [{'runtime_key': 'system::held'}]
    return model


class History(unittest.TestCase):
    def test_omitted_constant_fields_are_reconstructible_from_kept_fields(self):
        from clarity.certification.constant_facts import derive_constant_facts, check_constant_facts, constant_premises
        from clarity.certification.source_layout import ConstantStateLayout
        from clarity.certification.lazy_solver import variables
        from clarity.certification.lazy_substitution import PreparedSubstitution
        program=compile_graph(delayed_boolean_model().execution,.1,specialize=True)
        facts=derive_constant_facts(program)
        check_constant_facts(program,facts)
        layout=ConstantStateLayout(program,facts)
        self.assertGreater(layout.omitted['response'],0)
        self.assertIn('store/system::x/payload',layout.paths['response'])
        self.assertIn('store/system::held/payload',layout.paths['response'])
        for name,row in program['nodes'].items():
            full=[v for _,v in fields(row['compiler'],row['input'])]
            kept=layout.pack(name,row['compiler'],row['input'])
            other=PreparedSubstitution([(v,z3.Const('layout_other_'+str(v.get_id()),v.sort()))
                                       for v in variables(*full)])
            premises=constant_premises(row,facts[name])
            solver=z3.Solver();solver.set(timeout=30000)
            solver.add(*premises,*[other(v) for v in premises],
                       *[v==other(v) for v in kept],z3.Or(*[v!=other(v) for v in full]))
            self.assertEqual(solver.check(),z3.unsat,name)

    def test_record_and_flat_state_have_identical_equality(self):
        program = compile_graph(delayed_boolean_model().execution, .1, specialize=True)
        row = next(iter(program['nodes'].values()))
        signature = fields(row['compiler'], row['input'])
        record = z3.Datatype('HistoryLayoutCorrespondence')
        record.declare('State', *[('field_' + str(i), value.sort())
                                  for i, (_, value) in enumerate(signature)])
        record = record.create()
        left = [z3.Const('layout_left_' + str(i), value.sort())
                for i, (_, value) in enumerate(signature)]
        right = [z3.Const('layout_right_' + str(i), value.sort())
                 for i, (_, value) in enumerate(signature)]
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add((record.State(*left) == record.State(*right)) !=
                   z3.And(*[a == b for a, b in zip(left, right)]))
        self.assertEqual(solver.check(), z3.unsat)

    def test_theorem_gate_requires_source_history(self):
        from clarity.certification.certificate_generation import _theorem_gate
        args = dict(proof={'passes': True}, equation_proof={'passes': True}, blocking=[],
                    profile_obligations_discharged=True,
                    solver_result={'status': 'discharged', 'claim': 'one_step_transition_closure'})
        self.assertFalse(_theorem_gate(**args)['full_mdp_theorem_claim_allowed'])
        for record in ({'status': 'unknown', 'claim': 'source_history_reconstruction'},
                       {'status': 'discharged', 'claim': 'raw_observation_reconstruction'}):
            self.assertFalse(_theorem_gate(**args, source_history_result=record)
                             ['full_mdp_theorem_claim_allowed'])
        self.assertTrue(_theorem_gate(**args, source_history_result={
            'status': 'discharged', 'claim': 'source_history_reconstruction'})
            ['full_mdp_theorem_claim_allowed'])

    def test_successful_decision_installs_exact_evaluated_inputs(self):
        program = compile_graph(delayed_boolean_model().execution, .1, specialize=True)
        for row in program['nodes'].values():
            relation = row['relation']
            if relation.outcome != 'decision':
                continue
            differences = []
            for key, original in relation.decision_inputs:
                present, stored = relation.state.inputs[key]
                differences.extend((z3.Not(present), stored.value != original.value,
                                    stored.status != original.status))
            solver = z3.Solver()
            solver.set(timeout=30000)
            solver.add(*row['compiler'].eq.equations, relation.status == Status.Success,
                       z3.Or(*differences))
            self.assertEqual(solver.check(), z3.unsat)

    def test_normalization_matches_runtime_including_bool_and_rounding(self):
        env = object.__new__(SysMLEnv)
        env._obs_keys = ['x']
        env._obs_scale = 150.0
        for value in (True, False, 0.0, -0.0, 1.0, -7.1, 1e-45, 2**53 + 1,
                      float('inf'), float('-inf'), float('nan')):
            expected = np.float64(env._state_to_obs({'x': value})[0])
            encoded, valid = encode_observation(Term(scalar(value)), 150.0)
            self.assertTrue(z3.is_true(z3.simplify(valid)), repr(value))
            actual = z3.simplify(Value.floating(encoded))
            if np.isnan(expected):
                self.assertTrue(z3.is_true(z3.simplify(z3.fpIsNaN(actual))))
            else:
                bits = z3.simplify(z3.fpToIEEEBV(actual)).as_long()
                self.assertEqual(bits, int(np.asarray(expected).view(np.uint64)), repr(value))

    def test_encoding_error_is_an_obligation(self):
        for term in (Term(Value.Absent), Term(scalar('not numeric')),
                     Term(scalar(1.0), Status.Failure(1, 2)), Term(scalar(10**1000))):
            _, valid = encode_observation(term, 1.0)
            self.assertTrue(z3.is_false(z3.simplify(valid)))

    def test_buffer_shift_retains_exact_lags(self):
        buffer = z3.K(z3.IntSort(), Value.Absent)
        for values in ((1, 2), (3, 4), (5, 6)):
            buffer = push(buffer, [scalar(v) for v in values], 2)
        self.assertEqual([z3.simplify(Value.integer(z3.Select(buffer, i))).as_long()
                          for i in range(4)], [5, 6, 3, 4])
        self.assertTrue(z3.is_true(z3.simplify(push(buffer, [scalar(7)], 0) ==
                                             z3.K(z3.IntSort(), Value.Absent))))

    def test_finite_slots_preserve_array_shift_and_equality(self):
        for depth in range(4):
            for width in range(1, 4):
                left = tuple(z3.Const(f'left_{i}', Value) for i in range(depth * width))
                right = tuple(z3.Const(f'right_{i}', Value) for i in range(depth * width))
                values = tuple(z3.Const(f'new_{i}', Value) for i in range(width))
                def array(slots):
                    result = z3.K(z3.IntSort(), Value.Absent)
                    for i, v in enumerate(slots):
                        result = z3.Store(result, i, v)
                    return result
                shifted = push_slots(left, values, depth)
                solver = z3.Solver()
                solver.set(timeout=30000)
                solver.add(z3.Or(array(shifted) != push(array(left), values, depth),
                    (array(left) == array(right)) != z3.And(*[a == b for a, b in zip(left, right)])))
                self.assertEqual(solver.check(), z3.unsat)

    def test_delayed_observation_and_last_action_reconstruct_both_states(self):
        model = delayed_boolean_model()
        query = LazyTransitionQuery(model, {'x', 'held'}, compile_graph(model.execution, .1, specialize=True))
        result = SourceHistoryQuery(query, 0, 1, scale=1.0).check(30000)
        self.assertEqual(result['status'], 'discharged', result)

    def test_omitting_pending_action_cannot_reconstruct_physical_state(self):
        model = delayed_boolean_model()
        query = LazyTransitionQuery(model, {'x', 'held'}, compile_graph(model.execution, .1, specialize=True))
        result = SourceHistoryQuery(query, 2, 0, scale=1.0).check(30000)
        self.assertEqual(result['z3_check_sat'], 'sat', result)
        self.assertNotEqual(result['status'], 'discharged')


if __name__ == '__main__':
    unittest.main()
