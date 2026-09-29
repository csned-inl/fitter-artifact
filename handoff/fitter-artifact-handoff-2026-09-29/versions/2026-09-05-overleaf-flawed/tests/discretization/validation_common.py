#!/usr/bin/env python3
"""Validation battery for the discretization-safety proof rules and checker."""

from __future__ import annotations

import copy
import tempfile
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from clarity.certification.certificate import load_certificate as load_mdp_certificate
from clarity.certification.equations import Const, EquationModel, Op, Var

from clarity.discretization.analysis import CHECKER_ORDER, analyze_model
from clarity.discretization.certificates import (
    certificate_hash,
    check_certificate,
    expand_analysis,
    load_certificate,
)
from clarity.discretization.certificates.replay import replay_serialized_boolean_expression
from clarity.discretization.certificates.verification import (
    verify_recorded_convex_certificate,
    verify_recorded_linear_certificate,
    verify_recorded_outer_reduction,
)
from clarity.discretization.certificates.verification.factored import (
    _verify_lazy_factored_stage,
)
from clarity.discretization.checkers.convex import run_convex_checker, solve_convex_constraints
from clarity.discretization.checkers.convex_envelope import run_convex_envelope_checker
from clarity.discretization.checkers.factored import run_lazy_factored_checker
from clarity.discretization.checkers.linear import run_linear_checker, solve_linear_constraints
from clarity.discretization.checkers.linear_envelope import run_linear_envelope_checker
from clarity.discretization.checkers.reachability import run_reachability_checker, run_smt_reachability_checker
from clarity.discretization.model.optimization import QuadraticConstraint
from clarity.discretization.model.proof_rules import (
    LinearInequality,
    expr_to_dict,
    expression_is_linear,
    prove_implication_exact,
)
from clarity.discretization.model.expressions import INTERVAL_TIME, expression_hash
from clarity.discretization.model.reduction_types import ReachabilityContext, ReducedCase


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reduced_case(case_id: str, expression) -> ReducedCase:
    return ReducedCase(
        case_id,
        expression,
        expression_hash(expression),
        (),
        "test",
        "test",
    )


def nested_objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nested_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_objects(item)
