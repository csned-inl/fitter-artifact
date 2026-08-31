"""Data carried by the full SysML interval reduction."""

from __future__ import annotations

from dataclasses import dataclass

from clarity.certification.equations import Expr


@dataclass(frozen=True)
class ReducedCase:
    """One completely recorded arithmetic case produced by the reducer."""

    case_id: str
    expression: Expr
    parent_hash: str
    boolean_assignment: tuple[tuple[str, bool], ...]
    time_reduction: str
    obligation: str
    reachability_expression: Expr | None = None
    factored: bool = False


def _factored_obligation(
    expression: Expr,
    property_id: str,
    obligation: str,
) -> tuple[list[ReducedCase], dict[str, Any]]:
    """Keep one exact obligation root instead of constructing all logical cases."""

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


@dataclass(frozen=True)
class ReachabilityContext:
    """Exact inputs used to prove that a reduced case is unreachable."""

    domain: Expr
    initial_constraints: tuple[Expr, ...]
    initial_variables: tuple[str, ...]
    post_values: tuple[tuple[str, Expr], ...]
    action_variables: tuple[str, ...]
    boolean_variables: tuple[str, ...]
    integer_variables: tuple[str, ...]

    def post_dict(self) -> dict[str, Expr]:
        return dict(self.post_values)
