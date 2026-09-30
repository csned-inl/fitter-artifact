#!/usr/bin/env python3
"""Checked scalar-expression lowering and mutation tests."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from clarity.certification.markov_extract import extract_thermostat_markov_ir
from clarity.certification.markov_interval import derive_thermostat_decision_interval
from clarity.certification.markov_ir import NativeSort
from clarity.certification.markov_slice import build_thermostat_relevance_slice
from clarity.certification.markov_z3 import SolverStatus, UnsupportedLoweringError, run_smt2_query
from clarity.certification.markov_z3_expr import ThermostatExpressionCompiler


HAS_Z3 = importlib.util.find_spec("z3") is not None


class ExpressionLoweringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = extract_thermostat_markov_ir()
        cls.interval = derive_thermostat_decision_interval(cls.ir)
        cls.slice = build_thermostat_relevance_slice(cls.ir, cls.interval)
        cls.events = {event.node_id: event for event in cls.ir.events}

    def compiler(self):
        return ThermostatExpressionCompiler(self.slice, namespace="fixture")

    def expression(self, node_id, key="expression"):
        return self.events[node_id].decoded_data()[key]

    def test_every_sliced_scalar_ast_lowers(self):
        compiler = self.compiler()
        compiled = 0
        for sliced in self.slice.events:
            data = self.events[sliced.node_id].decoded_data()
            roots = []
            for key in ("expression", "condition"):
                if isinstance(data.get(key), dict):
                    roots.append(data[key])
            if sliced.operation == "check_all_requirements":
                roots.extend(data["expressions"].values())
            if sliced.operation == "decision":
                roots.extend(value for value in data["inputs"].values()
                             if isinstance(value, dict))
            for root in roots:
                compiler.compile(root)
                compiled += 1
        self.assertEqual(compiled, 20)
        self.assertGreater(len(compiler.declarations), 0)

    def test_environment_update_uses_ieee_operations(self):
        term = self.compiler().compile(
            self.expression("system::environment/step/1")
        )
        self.assertIs(term.sort, NativeSort.FLOAT64)
        for operator in ("fp.add", "fp.sub", "fp.mul", "fp.div"):
            self.assertIn(operator, term.text)
        self.assertIn("RNE", term.text)

    def test_live_expression_resolves_to_held_temperature(self):
        expression = self.events["cycle/check"].decoded_data()["expressions"]["Heat When Cold"]
        compiler = self.compiler()
        term = compiler.compile(expression)
        self.assertIn("semantic:held_temperature", term.text)
        self.assertNotIn("graph:system::lastObservedTemperature", term.text)

    def test_integer_zero_is_promoted_to_binary64_for_time_comparison(self):
        expression = self.events["cycle/check"].decoded_data()["expressions"]["Cool When Hot"]
        text = self.compiler().compile(expression).text
        self.assertIn("fp.gt", text)
        self.assertIn("((_ to_fp 11 53) #x0000000000000000)", text)

    def test_missing_storage_and_unsupported_operator_fail_closed(self):
        compiler = self.compiler()
        with self.assertRaises(UnsupportedLoweringError):
            compiler.compile({"kind": "reference", "storage": "missing"})
        with self.assertRaises(UnsupportedLoweringError):
            compiler.compile({
                "kind": "binary", "operator": "**",
                "left": {"kind": "literal", "type": "Integer", "value": 2},
                "right": {"kind": "literal", "type": "Integer", "value": 3},
            })

    def test_non_ast_value_fails_closed(self):
        with self.assertRaises(UnsupportedLoweringError):
            self.compiler().compile("configured dt")

    @unittest.skipUnless(HAS_Z3, "Z3 bindings are unavailable in this environment")
    def test_compiled_property_is_accepted_by_pinned_z3(self):
        expression = self.events["cycle/check"].decoded_data()["expressions"][
            "No Simultaneous Heating and Cooling"
        ]
        compiler = self.compiler()
        term = compiler.compile(expression)
        query = (
            "(set-logic QF_FP)\n"
            + compiler.declaration_smt2()
            + f"(assert {term.text})\n"
        )
        result = run_smt2_query(query, timeout_ms=30_000)
        self.assertIs(result.status, SolverStatus.SAT, result.reason)


if __name__ == "__main__":
    unittest.main()
