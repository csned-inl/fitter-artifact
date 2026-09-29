"""Exact equivalence to Z3 substitution, including sorts and AST lifetimes."""
import gc
import unittest
import z3
from clarity.certification.lazy_substitution import PreparedSubstitution
from clarity.certification.lazy_expressions import Value


class Substitution(unittest.TestCase):
    def test_matches_public_z3_operation(self):
        x, y = z3.Ints('sub_x sub_y')
        a, b = z3.FPs('sub_a sub_b', z3.Float64())
        v, w = z3.Consts('sub_v sub_w', Value)
        array = z3.Array('sub_array', z3.IntSort(), Value)
        pairs = [(x, y + 1), (a, b), (v, w)]
        apply = PreparedSubstitution(pairs)
        expressions = [x + x, z3.fpAdd(z3.RNE(), a, a),
                       z3.Store(array, x, v), z3.ForAll([y], x > y),
                       z3.If(x > 0, Value.Float(a), v)]
        for expression in expressions:
            self.assertTrue(apply(expression).eq(z3.substitute(expression, *pairs)))
            self.assertTrue(apply(expression).eq(apply(expression)))

    def test_temporary_expressions_are_retained(self):
        x, y = z3.Ints('temporary_x temporary_y')
        apply = PreparedSubstitution([(x, y)])
        for i in range(100):
            expression = x + i
            self.assertTrue(apply(expression).eq(y + i))
        gc.collect()
        self.assertEqual(len(apply.cache), 100)
        self.assertTrue(all(key == original.get_id() for key, (original, _) in apply.cache.items()))

    def test_rejects_mismatched_sorts_and_contexts(self):
        with self.assertRaises(ValueError):
            PreparedSubstitution([(z3.Int('wrong_sort'), z3.Bool('wrong_sort_bool'))])
        context = z3.Context()
        with self.assertRaises(ValueError):
            PreparedSubstitution([(z3.Int('context_x'), z3.Int('context_y', ctx=context))])
        apply = PreparedSubstitution([(z3.Int('good_x'), z3.Int('good_y'))])
        with self.assertRaises(ValueError):
            apply(z3.Int('foreign', ctx=context))

    def test_wide_boolean_bundles_preserve_numeric_abstraction(self):
        from clarity.certification.lazy_congruence import abstract_numeric
        values = [z3.Int('wide_' + str(i)) for i in range(20)]
        expressions = [z3.And(*[v == i for i, v in enumerate(values)]),
                       z3.Or(*[v == i for i, v in enumerate(values)]), z3.Distinct(*values)]
        result, count = abstract_numeric(expressions, 'wide_test')
        self.assertEqual(count, 0)
        for before, after in zip(expressions, result):
            solver = z3.Solver()
            solver.add(before != after)
            self.assertEqual(solver.check(), z3.unsat)

    def test_numeric_operator_signatures_include_actual_arity(self):
        from clarity.certification.lazy_congruence import abstract_numeric
        x, y, z = z3.Ints('arity_x arity_y arity_z')
        terms = [z3.Sum(x, y), z3.Sum(x, y, z), z3.Sum(y, z)]
        result, count = abstract_numeric(terms, 'arity_test')
        self.assertEqual(count, 2)
        self.assertEqual([term.num_args() for term in result], [2, 3, 2])
        self.assertTrue(result[0].decl().eq(result[2].decl()))
        self.assertFalse(result[0].decl().eq(result[1].decl()))
        # An exact interpretation exists for each abstract signature.
        a, b, c = z3.Ints('arity_a arity_b arity_c')
        solver = z3.Solver()
        solver.add(z3.ForAll([a, b], result[0].decl()(a, b) == a + b),
                   z3.ForAll([a, b, c], result[1].decl()(a, b, c) == a + b + c),
                   z3.Or(*[before != after for before, after in zip(terms, result)]))
        self.assertEqual(solver.check(), z3.unsat)

    def test_constant_reduction_preserves_delayed_and_physical_values(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        delayed, physical = z3.Ints('reduce_delayed reduce_physical')
        expressions = [physical == delayed + 1, physical != delayed]
        equalities = [delayed == 4]
        reduced = reduce_with_equalities(expressions, equalities)
        solver = z3.Solver()
        solver.add(z3.And(*expressions, *equalities) != z3.And(*reduced))
        self.assertEqual(solver.check(), z3.unsat)
        solver = z3.Solver()
        solver.add(*reduced)
        self.assertEqual(solver.check(), z3.sat)
        self.assertEqual(solver.model().eval(physical).as_long(), 5)

    def test_overlapping_equalities_remain_explicit(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        x, y = z3.Ints('reduce_overlap_x reduce_overlap_y')
        equalities = [x == y + 1, y == 2, x + y == 5]
        expressions = [x * y == 6]
        reduced = reduce_with_equalities(expressions, equalities)
        solver = z3.Solver()
        solver.add(z3.And(*expressions, *equalities) != z3.And(*reduced))
        self.assertEqual(solver.check(), z3.unsat)
        with self.assertRaises(ValueError):
            reduce_with_equalities(expressions, [x > y])

    def equivalent(self, before, after):
        solver = z3.Solver()
        solver.set(timeout=30000)
        solver.add(z3.And(*before) != z3.And(*after))
        self.assertEqual(solver.check(), z3.unsat)

    def test_alias_chain_and_reversed_literal(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        x, y, w = z3.Ints('chain_x chain_y chain_w')
        premises = [z3.IntVal(0) == x, y == x, w == y]
        reduced = reduce_with_equalities([w + y + x != 0], premises)
        self.assertTrue(z3.is_false(reduced[-1]))
        self.equivalent([*premises, w + y + x != 0], reduced)
        # Constants elsewhere must stay constants.
        reduced = reduce_with_equalities([w == 0], [z3.IntVal(0) == x])
        self.assertTrue(reduced[-1].eq(w == 0))

    def test_conflicts_cycles_and_compound_sources(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        x, y = z3.Ints('cycle_x cycle_y')
        for premises in ([x == y, y == x], [x == 0, x == 1],
                         [x == y + 1, y == x + 1], [x + 1 == y + 2]):
            formula = [x * x != y + 1]
            self.equivalent([*premises, *formula], reduce_with_equalities(formula, premises))

    def test_guarded_equalities_are_not_assumptions(self):
        from clarity.certification.lazy_substitution import propagate_asserted_equalities
        x, y = z3.Ints('guard_x guard_y')
        guard = z3.Bool('guard')
        formula = [z3.Implies(guard, x == y), z3.Not(guard), x != y]
        after = propagate_asserted_equalities(formula)
        self.equivalent(formula, after)
        solver = z3.Solver()
        solver.add(*after)
        self.assertEqual(solver.check(), z3.sat)

    def test_two_executions_do_not_merge_physical_and_delayed(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        p, d, pr, dr = z3.Ints('physical delayed physical_right delayed_right')
        premises = [p == pr, d == dr]
        reduced = reduce_with_equalities([p != d, pr != dr], premises)
        self.equivalent([*premises, p != d, pr != dr], reduced)
        solver = z3.Solver()
        solver.add(*reduced)
        self.assertEqual(solver.check(), z3.sat)
        # Omitting physical equality must retain a distinguishing execution.
        solver = z3.Solver()
        solver.add(*reduce_with_equalities([p != pr], [d == dr]))
        self.assertEqual(solver.check(), z3.sat)

    def test_arrays_datatypes_and_native_floats(self):
        from clarity.certification.lazy_substitution import reduce_with_equalities
        a, b = z3.FPs('premise_a premise_b', z3.Float64())
        x, y = z3.Consts('premise_value_x premise_value_y', Value)
        aa = z3.Array('premise_array_a', z3.IntSort(), Value)
        ab = z3.Array('premise_array_b', z3.IntSort(), Value)
        premises = [a == b, x == y, aa == ab]
        formula = [z3.Or(z3.fpIsNaN(a) != z3.fpIsNaN(b),
                        z3.Select(aa, 0) != z3.Select(ab, 0), x != y)]
        reduced = reduce_with_equalities(formula, premises)
        self.assertTrue(z3.is_false(reduced[-1]))
        self.equivalent([*premises, *formula], reduced)

    def test_empty_substitution(self):
        x = z3.Int('unchanged')
        self.assertTrue(PreparedSubstitution([])(x).eq(x))


if __name__ == '__main__':
    unittest.main()
