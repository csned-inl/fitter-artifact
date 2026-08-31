"""Create the lazy factored root for a discretization obligation."""

from __future__ import annotations

from typing import Any

from clarity.certification.equations import Expr

from .expressions import expression_hash, simplify
from .proof_rules import expr_to_dict
from .reduction_types import ReducedCase


def factored_obligation(
    expression: Expr,
    property_id: str,
    obligation: str,
) -> tuple[list[ReducedCase], dict[str, Any]]:
    """Keep one exact obligation root instead of constructing logical cases."""

    root = simplify(expression)
    root_hash = expression_hash(root)
    case_id = f"{property_id}.{obligation}.root"
    case = ReducedCase(
        case_id=case_id,
        expression=root,
        parent_hash=root_hash,
        boolean_assignment=(),
        time_reduction="symbolic_factored_formula",
        obligation=obligation,
        reachability_expression=root,
        factored=True,
    )
    row = {
        "case_id": case_id,
        "obligation": obligation,
        "boolean_assignment": {},
        "conditional_branch": "symbolic",
        "time_reduction": "symbolic_factored_formula",
        "expression": expr_to_dict(root),
        "expression_sha256": root_hash,
        "reachability_expression": expr_to_dict(root),
        "reachability_expression_sha256": root_hash,
        "constraint_reduction": {
            "rule": "canonical_conjunction_reduction_v1",
            "removed_constraints": [],
            "reachability_removed_constraints": [],
        },
        "merged_sources": [],
    }
    return [case], {
        "rule": "factored_obligation_root_v1",
        "obligation": obligation,
        "parent_expression_sha256": root_hash,
        "root_expression": expr_to_dict(root),
        "root_expression_sha256": root_hash,
        "generated_case_count": 1,
        "case_count": 1,
        "containment_merged_case_count": 0,
        "actual_split_count": 0,
        "complete": True,
        "cases": [row],
    }
