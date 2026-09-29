"""Constructor specialization must be established, not assumed from defaults."""
import unittest

import z3

from clarity.certification.lazy_expressions import Equations, Term, Status, Value, scalar
from clarity.certification.lazy_specialization import propose_fields, check_specialization


class Specialization(unittest.TestCase):
    def test_float_update_and_independent_held_value(self):
        initial = {'physical': 0.0, 'held': 0.0}
        fields = propose_fields(initial, 'state')
        equations = Equations()
        updated = equations.binary('+', fields['physical'].term, Term(scalar(1.0)))
        result = check_specialization(initial, fields, equations,
                                      {'physical': updated, 'held': fields['held'].term}, updated.status)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['queries']['execution_error']['status'], 'unsat')
        self.assertTrue(z3.eq(result['native_outputs']['held'], fields['held'].payload))
        # Replay serialized proof queries through an independently created solver.
        for query in result['queries'].values():
            checker = z3.Solver()
            checker.set(timeout=1000)
            checker.from_string(query['smt2'])
            self.assertEqual(checker.check(), z3.unsat)

    def test_integer_default_cannot_justify_float_storage(self):
        initial = {'value': 0}
        fields = propose_fields(initial, 'state')
        self.assertEqual(fields['value'].kind, 'Integer')
        equations = Equations()
        updated = equations.bind(scalar(1.0))
        result = check_specialization(initial, fields, equations, {'value': updated}, updated.status)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['queries']['successful_preservation']['status'], 'sat')
        self.assertIsNone(result['native_outputs'])

    def test_conditional_type_change_is_not_ignored(self):
        initial = {'value': 0.0}
        fields = propose_fields(initial, 'state')
        equations = Equations()
        updated = equations.conditional(Term(Value.Boolean(z3.Bool('choose_integer'))),
                                         Term(scalar(1)), fields['value'].term)
        result = check_specialization(initial, fields, equations, {'value': updated}, updated.status)
        self.assertFalse(result['accepted'])

    def test_native_projection_does_not_suppress_division_error(self):
        initial = {'value': 0.0}
        fields = propose_fields(initial, 'state')
        equations = Equations()
        updated = equations.binary('/', fields['value'].term, Term(scalar(0.0)))
        result = check_specialization(initial, fields, equations, {'value': updated}, updated.status)
        self.assertTrue(result['accepted'])  # Every successful result has the right kind.
        self.assertEqual(result['queries']['execution_error']['status'], 'sat')

    def test_missing_field_rejected(self):
        initial = {'physical': 0.0, 'held': 0.0}
        fields = propose_fields(initial, 'state')
        with self.assertRaises(ValueError):
            check_specialization(initial, fields, Equations(),
                                 {'physical': fields['physical'].term}, Status.Success)


if __name__ == '__main__':
    unittest.main()
