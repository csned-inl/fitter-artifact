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
