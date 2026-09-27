"""One-step solver validation cases."""

from __future__ import annotations

from clarity.certification.equations import Const, Equation, EquationModel, Ite, Op, Var
from clarity.certification.solver import (
    MAX_SOLVER_POLYNOMIAL_DEGREE,
    one_step_transition_closure,
)

from .support import Battery


def run_solver_cases(battery: Battery) -> None:
    def power_expr(name: str, degree: int):
        expr = Var(name)
        for _ in range(degree - 1):
            expr = Op("*", (expr, Var(name)))
        return expr

    closed = EquationModel(
        model_path="<solver-closed>",
        state={"x"},
        actions={"u"},
        transitions={
            "x": Equation(
                target="x",
                expr=Op("+", (Var("x"), Var("u"))),
                kind="transition",
                source="synthetic closed linear transition",
            )
        },
        observations={
            "xObs": Equation(
                target="obs.xObs",
                expr=Var("x"),
                kind="observation",
                source="synthetic observation",
            )
        },
    )
    closed_result = one_step_transition_closure(closed, {"x"})
    battery.require(
        "solver_unit/closed_linear_unsat",
        closed_result["status"] == "discharged",
        f"result={closed_result}",
    )

    hidden = EquationModel(
        model_path="<solver-hidden-dependency>",
        state={"x", "hidden"},
        actions=set(),
        transitions={
            "hidden": Equation("hidden", Var("hidden"), "transition", "synthetic explicit hold"),
            "x": Equation(
                target="x",
                expr=Var("hidden"),
                kind="transition",
                source="synthetic hidden dependency",
            )
        },
        observations={
            "xObs": Equation(
                target="obs.xObs",
                expr=Var("x"),
                kind="observation",
                source="synthetic observation",
            )
        },
    )
    hidden_result = one_step_transition_closure(hidden, {"x"})
    battery.require(
        "solver_unit/hidden_dependency_sat",
        hidden_result["status"] == "counterexample"
        and hidden_result.get("disagreement", {}).get("term") == "next_q.x",
        f"result={hidden_result}",
    )

    boolean_closed = EquationModel(
        model_path="<solver-boolean-ite>",
        state={"safe"},
        actions={"command"},
        transitions={
            "safe": Equation(
                target="safe",
                expr=Ite(Var("command"), Const(True), Const(False)),
                kind="transition",
                source="synthetic boolean transition",
            )
        },
        requirements={
            "status.safe": Equation(
                target="status.safe",
                expr=Var("safe"),
                kind="requirement",
                source="synthetic requirement",
            )
        },
    )
    boolean_result = one_step_transition_closure(boolean_closed, {"safe"})
    battery.require(
        "solver_unit/boolean_ite_unsat",
        boolean_result["status"] == "discharged",
        f"result={boolean_result}",
    )

    quadratic = EquationModel(
        model_path="<solver-quadratic>",
        state={"x"},
        actions=set(),
        transitions={
            "x": Equation(
                target="x",
                expr=Op("*", (Var("x"), Var("x"))),
                kind="transition",
                source="synthetic quadratic transition",
            )
        },
    )
    quadratic_result = one_step_transition_closure(quadratic, {"x"})
    battery.require(
        "solver_unit/quadratic_unsat",
        quadratic_result["status"] == "discharged"
        and quadratic_result.get("logic") == "QF_UFNRA"
        and quadratic_result.get("max_polynomial_degree") == 2,
        f"result={quadratic_result}",
    )

    hidden_quadratic = EquationModel(
        model_path="<solver-hidden-quadratic>",
        state={"x", "hidden"},
        actions=set(),
        transitions={
            "hidden": Equation("hidden", Var("hidden"), "transition", "synthetic explicit hold"),
            "x": Equation(
                target="x",
                expr=Op("*", (Var("x"), Var("hidden"))),
                kind="transition",
                source="synthetic hidden quadratic transition",
            )
        },
    )
    hidden_quadratic_result = one_step_transition_closure(hidden_quadratic, {"x"})
    battery.require(
        "solver_unit/hidden_quadratic_sat",
        hidden_quadratic_result["status"] == "counterexample"
        and hidden_quadratic_result.get("logic") == "QF_UFNRA"
        and hidden_quadratic_result.get("max_polynomial_degree") == 2
        and hidden_quadratic_result.get("disagreement", {}).get("term") == "next_q.x",
        f"result={hidden_quadratic_result}",
    )

    for degree in range(3, MAX_SOLVER_POLYNOMIAL_DEGREE + 1):
        polynomial = EquationModel(
            model_path=f"<solver-degree-{degree}>",
            state={"x"},
            actions=set(),
            transitions={
                "x": Equation(
                    target="x",
                    expr=power_expr("x", degree),
                    kind="transition",
                    source=f"synthetic degree {degree} transition",
                )
            },
        )
        polynomial_result = one_step_transition_closure(polynomial, {"x"})
        battery.require(
            f"solver_unit/degree_{degree}_unsat",
            polynomial_result["status"] == "discharged"
            and polynomial_result.get("logic") == "QF_UFNRA"
            and polynomial_result.get("max_polynomial_degree") == 2,
            f"result={polynomial_result}",
        )

        hidden_polynomial = EquationModel(
            model_path=f"<solver-hidden-degree-{degree}>",
            state={"x", "hidden"},
            actions=set(),
            transitions={
                "hidden": Equation("hidden", Var("hidden"), "transition", "synthetic explicit hold"),
                "x": Equation(
                    target="x",
                    expr=Op("*", (power_expr("x", degree - 1), Var("hidden"))),
                    kind="transition",
                    source=f"synthetic hidden degree {degree} transition",
                )
            },
        )
        hidden_polynomial_result = one_step_transition_closure(hidden_polynomial, {"x"})
        battery.require(
            f"solver_unit/hidden_degree_{degree}_sat",
            hidden_polynomial_result["status"] == "counterexample"
            and hidden_polynomial_result.get("logic") == "QF_UFNRA"
            and hidden_polynomial_result.get("max_polynomial_degree") == 2
            and hidden_polynomial_result.get("disagreement", {}).get("term") == "next_q.x",
            f"result={hidden_polynomial_result}",
        )

    unsupported_degree = MAX_SOLVER_POLYNOMIAL_DEGREE + 1
    too_high_degree = EquationModel(
        model_path=f"<solver-degree-{unsupported_degree}>",
        state={"x"},
        actions=set(),
        transitions={
            "x": Equation(
                target="x",
                expr=power_expr("x", unsupported_degree),
                kind="transition",
                source=f"synthetic degree {unsupported_degree} transition",
            )
        },
    )
    too_high_result = one_step_transition_closure(too_high_degree, {"x"})
    battery.require(
        f"solver_unit/degree_{unsupported_degree}_compact_unsat",
        too_high_result["status"] == "discharged"
        and too_high_result.get("max_polynomial_degree") == 2
        and too_high_result.get("intermediate_constraints", 0) > 0,
        f"result={too_high_result}",
    )

    symbolic_division = EquationModel(
        model_path="<solver-symbolic-division>",
        state={"x", "hidden"},
        actions=set(),
        transitions={
            "hidden": Equation("hidden", Var("hidden"), "transition", "synthetic explicit hold"),
            "x": Equation(
                target="x",
                expr=Op("/", (Var("x"), Var("hidden"))),
                kind="transition",
                source="synthetic symbolic division transition",
            )
        },
    )
    symbolic_division_result = one_step_transition_closure(symbolic_division, {"x"})
    battery.require(
        "solver_unit/symbolic_division_unknown",
        symbolic_division_result["status"] == "unknown"
        and symbolic_division_result.get("reason") == "unsupported_fragment"
        and any("division by symbolic expression" in item for item in symbolic_division_result.get("unsupported", [])),
        f"result={symbolic_division_result}",
    )
