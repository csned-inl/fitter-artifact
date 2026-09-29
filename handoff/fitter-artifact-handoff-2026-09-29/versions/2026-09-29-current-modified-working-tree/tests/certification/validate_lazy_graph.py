"""Source-sized compilation and exact frame/edge checks, run on the worker."""
import contextlib
import io
import unittest

import z3

from clarity.certification.lazy_graph import compile_graph, fields
from clarity.certification.lazy_expressions import Status
from source_equation_fixture import small_source_model


class Graph(unittest.TestCase):
    def test_fixture_coverage_and_frames(self):
        model = small_source_model()
        program = compile_graph(model.execution, .1)
        self.assertEqual(set(program['nodes']), set(program['graph']['nodes']))
        for name, row in program['nodes'].items():
            compiler, before, after = row['compiler'], row['input'], row['relation'].state
            self.assertEqual([(k, v.sort()) for k, v in fields(compiler, before)],
                             [(k, v.sort()) for k, v in fields(compiler, after)])
            if program['graph']['nodes'][name]['operation'] == 'begin_cycle':
                solver = z3.Solver()
                solver.add(*compiler.eq.equations, z3.Or(*[
                    a != b for (_, a), (_, b) in zip(fields(compiler, before), fields(compiler, after))]))
                self.assertEqual(solver.check(), z3.unsat)

    def test_compile_each_model_once_per_source_node(self):
        from clarity.models import models_root
        from clarity.certification.strict_extract import extract_equation_model
        for name in ('thermostat', 'cruise-controller-model', 'mixing-sysml-model'):
            with contextlib.redirect_stdout(io.StringIO()):
                model = extract_equation_model(str(models_root()/name/'model.sysml'))
            program = compile_graph(model.execution, .1)
            self.assertEqual(set(program['nodes']), set(program['graph']['nodes']))
            print(name, 'nodes', len(program['nodes']), 'equations', program['equations'],
                  'seconds', round(program['seconds'], 3), flush=True)


if __name__ == '__main__':
    unittest.main()
