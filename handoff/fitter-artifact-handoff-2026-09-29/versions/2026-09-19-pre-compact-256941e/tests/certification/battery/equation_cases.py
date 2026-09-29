"""Equation reconstruction validation cases."""

from __future__ import annotations

from clarity.certification.equation_reconstruct import (
    equation_reconstruction_trace,
    fact_key as equation_fact_key,
)
from clarity.certification.equations import Const, Equation, EquationModel, Ite, Var

from .support import Battery, TEST_DT


def run_equation_cases(battery: Battery) -> None:
    direct = EquationModel(
        model_path="<synthetic-direct-copy>",
        state={"x", "y"},
        actions=set(),
        observations={
            "yObs": Equation(
                target="obs.yObs",
                expr=Var("y"),
                kind="observation",
                source="synthetic direct observation",
            )
        },
        transitions={
            "y": Equation(
                target="y",
                expr=Var("x"),
                kind="transition",
                source="synthetic direct copy",
            )
        },
    )
    direct_trace = equation_reconstruction_trace(
        direct,
        b_obs=0,
        b_act=0,
        dt=TEST_DT,
        horizon=2,
        target={"x"},
        enable_sampled_memory=False,
    )
    battery.require(
        "equation_unit/direct_copy_inversion",
        equation_fact_key("x", -1) in {fact["key"] for fact in direct_trace["facts"]},
        f"facts={[fact['key'] for fact in direct_trace['facts']]}",
    )

    guarded = EquationModel(
        model_path="<synthetic-guarded-copy>",
        state={"guard", "x", "y"},
        actions=set(),
        observations={
            "yObs": Equation(
                target="obs.yObs",
                expr=Var("y"),
                kind="observation",
                source="synthetic direct observation",
            )
        },
        transitions={
            "y": Equation(
                target="y",
                expr=Ite(Var("guard"), Var("x"), Var("y")),
                kind="transition",
                source="synthetic guarded copy",
            )
        },
    )
    guarded_trace = equation_reconstruction_trace(
        guarded,
        b_obs=0,
        b_act=0,
        dt=TEST_DT,
        horizon=2,
        target={"x"},
        enable_sampled_memory=False,
    )
    guarded_facts = {fact["key"] for fact in guarded_trace["facts"]}
    battery.require(
        "equation_unit/guarded_copy_not_inverted",
        equation_fact_key("x", -1) not in guarded_facts
        and bool(guarded_trace["blocked_unsafe_inversions"]),
        (
            f"x@-1_present={equation_fact_key('x', -1) in guarded_facts} "
            f"blocked={guarded_trace['blocked_unsafe_inversions'][:1]}"
        ),
    )

    proven_guarded = EquationModel(
        model_path="<synthetic-proven-guarded-copy>",
        state={"x", "y"},
        actions=set(),
        observations={
            "yObs": Equation(
                target="obs.yObs",
                expr=Var("y"),
                kind="observation",
                source="synthetic direct observation",
            )
        },
        transitions={
            "y": Equation(
                target="y",
                expr=Ite(Const(True), Var("x"), Var("y")),
                kind="transition",
                source="synthetic proven guarded copy",
            )
        },
    )
    proven_trace = equation_reconstruction_trace(
        proven_guarded,
        b_obs=0,
        b_act=0,
        dt=TEST_DT,
        horizon=2,
        target={"x"},
        enable_sampled_memory=False,
    )
    battery.require(
        "equation_unit/proven_guarded_copy_inverted",
        equation_fact_key("x", -1) in {fact["key"] for fact in proven_trace["facts"]},
        f"blocked={proven_trace['blocked_unsafe_inversions']}",
    )
