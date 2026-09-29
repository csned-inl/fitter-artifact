"""Exercise restored parser consumers and distinct physical/sensor storage."""
from pathlib import Path
import unittest

from clarity.models import models_root
from clarity.sysml.parser import (SysMLParser, AttributeDeclStmt, RefExpr)
from clarity.sysml.simulator import SimulationEngine, resolve_value
from clarity.runtime.shield import SpecShield
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.ordered_execution import validate_execution_description
from clarity.sysml.parser_values import assigned_features


class OriginalParserIntegration(unittest.TestCase):
    def parser(self, name):
        p = SysMLParser(str(models_root() / name / 'model.sysml'))
        p.parse()
        return p

    def test_all_model_consumers_and_property_inventories(self):
        for name, requirements in [('cruise-controller-model', 5),
                                   ('mixing-sysml-model', 4), ('thermostat', 3)]:
            with self.subTest(model=name):
                p = self.parser(name)
                shield = SpecShield(p.file_path)
                engine = SimulationEngine(p)
                engine.initialize()
                equations = extract_equation_model(p.file_path)
                self.assertEqual(len(equations.execution['property_inventory']), requirements)
                self.assertTrue(equations.state_value_pairs)
                errors = validate_execution_description(equations.execution, p.file_path)
                self.assertNotIn('ordered execution/property/type inventory does not match source', errors)
                mutable = assigned_features(p)
                prefix = p.controller_part + '::'
                self.assertFalse(any(prefix + name in mutable for name in shield.unchanging))

    def test_clock_updates_with_original_equals_declaration(self):
        p = self.parser('cruise-controller-model')
        engine = SimulationEngine(p)
        engine.initialize()
        shield = SpecShield(p.file_path)
        engine.model = lambda inputs: shield.action_map[shield.requirement_action(inputs)]
        self.assertEqual(resolve_value(engine.state, 'system::currentTime'), 0)
        engine.step(.1)
        self.assertAlmostEqual(resolve_value(engine.state, 'system::currentTime'), .1)

    def test_action_local_value_is_a_copy(self):
        p = self.parser('cruise-controller-model')
        engine = SimulationEngine(p)
        engine.initialize()
        engine._execute_action_stmts([
            AttributeDeclStmt('capturedTime', 'Real', RefExpr(['currentTime']))], 'system')
        engine._assign_value('system::currentTime', 2.0)
        self.assertEqual(resolve_value(engine.state, 'system::capturedTime'), 0)
        self.assertEqual(resolve_value(engine.state, 'system::currentTime'), 2.0)

    def test_physical_and_delayed_values_remain_distinct_during_execution(self):
        p = self.parser('mixing-sysml-model')
        engine = SimulationEngine(p)
        engine.initialize()
        shield = SpecShield(p.file_path)
        engine.model = lambda inputs: shield.action_map[shield.requirement_action(inputs)]
        differences = []
        for _ in range(25):
            engine.step(.1)
            for pair in engine.state_value_pairs():
                physical, sampled = pair['physical'], pair['sampled']
                if physical['available'] and sampled['available'] and physical['value'] != sampled['value']:
                    differences.append((physical['state_variable'], sampled['state_variable']))
        self.assertTrue(differences, 'execution must retain delayed values independently of current physics')
        self.assertTrue(all(a != b for a, b in differences))


if __name__ == '__main__':
    unittest.main()
