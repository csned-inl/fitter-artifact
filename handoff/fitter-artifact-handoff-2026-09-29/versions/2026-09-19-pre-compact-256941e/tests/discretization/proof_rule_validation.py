"""Focused arithmetic checker and proof-rule validation."""

from validation_common import (
    Const,
    EquationModel,
    Fraction,
    INTERVAL_TIME,
    LinearInequality,
    Op,
    QuadraticConstraint,
    ReachabilityContext,
    ReducedCase,
    Var,
    _verify_lazy_factored_stage,
    copy,
    expr_to_dict,
    expression_hash,
    expression_is_linear,
    nested_objects,
    patch,
    prove_implication_exact,
    reduced_case,
    replay_serialized_boolean_expression,
    require,
    run_convex_checker,
    run_convex_envelope_checker,
    run_lazy_factored_checker,
    run_linear_checker,
    run_linear_envelope_checker,
    run_reachability_checker,
    run_smt_reachability_checker,
    solve_convex_constraints,
    solve_linear_constraints,
    verify_recorded_convex_certificate,
    verify_recorded_linear_certificate,
    verify_recorded_outer_reduction,
)


def validate_proof_rules() -> None:
    interval_time = Var(INTERVAL_TIME)
    interval_domain = [Op('>=', (interval_time, Const(0))),
                       Op('<=', (interval_time, Const(1)))]
    safe_interval = Op('and', tuple(interval_domain))
    unsafe_interior = Op('or', (
        Op('<=', (interval_time, Const('1/4'))),
        Op('>=', (interval_time, Const('3/4'))),
    ))
    require(prove_implication_exact(interval_domain, safe_interval, set()).get('proved') is True,
            'valid whole-interval control did not prove')
    require(prove_implication_exact(interval_domain, unsafe_interior, set()).get('proved') is False,
            'safe endpoints concealed an unsafe interval interior')
    serialized = expr_to_dict(unsafe_interior)
    for time, expected in [('0', True), ('1', True), ('1/2', False)]:
        require(replay_serialized_boolean_expression(serialized, {INTERVAL_TIME: time}) is expected,
                f'interval control has the wrong value at {time}')
    x = Var("x")
    sound = prove_implication_exact(
        [Op("<=", (x, Const(0)))],
        Op("<=", (x, Const(1))),
        set(),
    )
    require(sound.get("proved") is True, "valid exact implication was rejected")
    unsound = prove_implication_exact(
        [Op("<=", (x, Const(1)))],
        Op("<=", (x, Const(0))),
        set(),
    )
    require(unsound.get("proved") is False, "invalid exact implication was accepted")
    linear, _detail = expression_is_linear(Op("*", (x, x)), set())
    require(linear is False, "variable multiplication was classified as linear")
    exact_replay_expression = {
        "type": "op",
        "op": "and",
        "args": [
            {
                "type": "op",
                "op": "==",
                "args": [
                    {
                        "type": "op",
                        "op": "*",
                        "args": [
                            {"type": "var", "name": "x"},
                            {"type": "const", "value": 2},
                        ],
                    },
                    {"type": "const", "value": 1},
                ],
            },
            {
                "type": "op",
                "op": ">",
                "args": [
                    {"type": "var", "name": "x"},
                    {"type": "const", "value": 0},
                ],
            },
        ],
    }
    require(
        replay_serialized_boolean_expression(
            exact_replay_expression,
            {"x": "1/2"},
        ) is True,
        "exact recorded values did not replay",
    )
    require(
        replay_serialized_boolean_expression(
            exact_replay_expression,
            {"x": "-1/2"},
        ) is False,
        "invalid recorded values replayed",
    )

    linear_problem = [
        LinearInequality.make({"x": Fraction(1)}, Fraction(0)),
        LinearInequality.make({"x": Fraction(-1)}, Fraction(-1)),
    ]
    linear_result = solve_linear_constraints(linear_problem, timeout_ms=250)
    require(linear_result.get("outcome") == "CERTIFIED", "linear proof was rejected")
    linear_certificate = linear_result["proof"]["certificate"]
    require(
        not verify_recorded_linear_certificate(linear_certificate),
        "linear proof certificate did not verify",
    )
    bad_linear_certificate = copy.deepcopy(linear_certificate)
    bad_linear_certificate["multipliers"][0] = "0/1"
    require(
        bool(verify_recorded_linear_certificate(bad_linear_certificate)),
        "invalid linear proof certificate was accepted",
    )
    strict_problem = [
        LinearInequality.make({"x": Fraction(1)}, Fraction(0), False),
        LinearInequality.make({"x": Fraction(-1)}, Fraction(0), True),
    ]
    strict_result = solve_linear_constraints(strict_problem, timeout_ms=250)
    require(
        strict_result.get("outcome") == "CERTIFIED",
        "exact strict linear proof was rejected",
    )

    convex_problem = [
        QuadraticConstraint.make({"x": Fraction(1)}, {}, Fraction(1))
    ]
    convex_result = solve_convex_constraints(convex_problem, timeout_ms=250)
    require(convex_result.get("outcome") == "CERTIFIED", "convex proof was rejected")
    convex_certificate = convex_result["proof"]["certificate"]
    require(
        not verify_recorded_convex_certificate(convex_certificate),
        "convex proof certificate did not verify",
    )
    bad_convex_certificate = copy.deepcopy(convex_certificate)
    bad_convex_certificate["global_lower_bound"] = "0/1"
    require(
        bool(verify_recorded_convex_certificate(bad_convex_certificate)),
        "invalid convex proof certificate was accepted",
    )
    strict_convex_result = solve_convex_constraints(
        [QuadraticConstraint.make({"x": Fraction(1)}, {}, Fraction(0), True)],
        timeout_ms=250,
    )
    require(
        strict_convex_result.get("outcome") == "CERTIFIED",
        "exact strict convex proof was rejected",
    )
    nonlinear_counterexample = Op(
        "<=",
        (Op("+", (Op("*", (x, x)), Const(1))), Const(0)),
    )
    require(
        run_linear_checker(
            reduced_case("nonlinear", nonlinear_counterexample),
            set(),
            timeout_ms=250,
        ).get("outcome")
        == "DEFERRED",
        "nonlinear problem was accepted by the linear checker",
    )
    require(
        run_convex_checker(
            reduced_case("convex", nonlinear_counterexample),
            set(),
            timeout_ms=250,
        ).get("outcome")
        == "CERTIFIED",
        "supported nonlinear problem was not certified by the convex checker",
    )
    bounded_product = Op(
        "and",
        (
            Op(">=", (x, Const(-1))),
            Op("<=", (x, Const(1))),
            Op(">=", (Op("*", (x, x)), Const(2))),
        ),
    )
    bounded_product_result = run_linear_envelope_checker(
        reduced_case("bounded-product", bounded_product),
        timeout_ms=250,
    )
    require(
        bounded_product_result.get("outcome") == "CERTIFIED",
        "bounded product was not certified by the linear outer reduction",
    )
    require(
        not verify_recorded_outer_reduction(
            bounded_product_result["proof"],
            expression_hash(bounded_product),
        ),
        "bounded product reduction certificate did not verify",
    )
    require(
        run_convex_envelope_checker(
            reduced_case("bounded-square", bounded_product),
            timeout_ms=250,
        ).get("outcome") == "CERTIFIED",
        "bounded square was not certified by the convex outer reduction",
    )
    feasible_product = Op(
        "and",
        (
            Op(">=", (x, Const(-1))),
            Op("<=", (x, Const(1))),
            Op(">=", (Op("*", (x, x)), Const(0))),
        ),
    )
    require(
        run_linear_envelope_checker(
            reduced_case("feasible-product", feasible_product),
            timeout_ms=250,
        ).get("outcome") == "DEFERRED",
        "feasible linear outer reduction reported a proof",
    )

    interval_time = Var(INTERVAL_TIME)
    factored_expression = Op("and", (
        Op(">=", (interval_time, Const(0))),
        Op("<=", (interval_time, Const(1))),
        Op(">=", (x, Const(0))),
        Op("<", (Op("*", (x, interval_time)), Const(0))),
    ))
    factored_case = ReducedCase(
        "factored-endpoint",
        factored_expression,
        expression_hash(factored_expression),
        (),
        "test",
        "physical_interval",
        factored_expression,
        True,
    )
    factored_attempt = run_lazy_factored_checker(
        factored_case,
        set(),
        "linear",
        lambda leaf, remaining: run_linear_checker(
            leaf,
            set(),
            timeout_ms=remaining,
        ),
        lambda expression: expression_is_linear(expression, set())[0],
        timeout_ms=250,
        context_sha256="validation",
    )
    require(
        factored_attempt.get("outcome") == "CERTIFIED",
        "factored endpoint proof was rejected",
    )
    factored_stage = {"checker": "linear", **factored_attempt}
    factored_record = {"expression": expr_to_dict(factored_expression)}
    require(
        not _verify_lazy_factored_stage(factored_stage, factored_record),
        "factored endpoint certificate did not verify",
    )
    bad_factored_split = copy.deepcopy(factored_stage)
    split_node = next(
        item
        for item in nested_objects(bad_factored_split)
        if item.get("rule") == "exact_factored_split_v1"
    )
    split_node["children"][0]["expression_sha256"] = split_node[
        "expression_sha256"
    ]
    require(
        bool(_verify_lazy_factored_stage(bad_factored_split, factored_record)),
        "invalid factored split was accepted",
    )
    bad_endpoint = copy.deepcopy(factored_stage)
    endpoint_node = next(
        item
        for item in nested_objects(bad_endpoint)
        if item.get("split_kind") == "affine_interval_endpoints"
    )
    endpoint_node["changing_comparison_sha256"] = endpoint_node[
        "expression_sha256"
    ]
    require(
        bool(_verify_lazy_factored_stage(bad_endpoint, factored_record)),
        "invalid factored endpoint reduction was accepted",
    )

    reachability_context = ReachabilityContext(
        domain=Const(True),
        initial_constraints=(Op("==", (x, Const(0))),),
        initial_variables=("x",),
        post_values=(("x", Op("+", (x, Const(1)))),),
        action_variables=(),
        boolean_variables=(),
        integer_variables=(),
    )
    safe_reachability_case = ReducedCase(
        "safe-reachability",
        Op("<", (x, Const(0))),
        expression_hash(Op("<", (x, Const(0)))),
        (),
        "time_independent",
        "physical_interval",
        Op("<", (x, Const(0))),
    )
    safe_reachability_result = run_reachability_checker(
        safe_reachability_case,
        reachability_context,
        method="linear",
        timeout_ms=250,
    )
    require(
        safe_reachability_result.get("outcome") == "CERTIFIED",
        "linear reachability proof was rejected",
    )
    certified_depth = next(
        item
        for item in safe_reachability_result["proof"]["depth_attempts"]
        if item.get("proved") is True
    )
    for obligation in (
        certified_depth["base_obligations"]
        + certified_depth["induction_obligations"]
    ):
        require(
            not _verify_lazy_factored_stage(
                obligation["attempt"],
                {"expression": obligation["expression"]},
            ),
            "factored reachability certificate did not verify",
        )
    unsafe_reachability_case = ReducedCase(
        "unsafe-reachability",
        Op(">=", (x, Const(0))),
        expression_hash(Op(">=", (x, Const(0)))),
        (),
        "time_independent",
        "physical_interval",
        Op(">=", (x, Const(0))),
    )
    require(
        run_reachability_checker(
            unsafe_reachability_case,
            reachability_context,
            method="linear",
            timeout_ms=250,
        ).get("outcome") == "VIOLATION",
        "exact initial reachability violation was not replayed",
    )
    smt_model = EquationModel(
        "synthetic",
        {"x"},
        set(),
        initial_values={"x": 0},
    )
    require(
        run_smt_reachability_checker(
            smt_model,
            safe_reachability_case,
            reachability_context,
            timeout_ms=250,
        ).get("outcome") == "CERTIFIED",
        "SMT reachability proof was rejected",
    )
    smt_violation = run_smt_reachability_checker(
        smt_model,
        unsafe_reachability_case,
        reachability_context,
        timeout_ms=250,
    )
    require(
        smt_violation.get("outcome") == "VIOLATION"
        and smt_violation.get("proof", {}).get("trace_query", {}).get(
            "exact_replay"
        ) is True,
        "SMT reachability trace was not replayed",
    )
    require(
        run_linear_checker(nonlinear_counterexample, set(), timeout_ms=250).get(
            "reason_code"
        )
        == "UNREDUCED_INPUT",
        "linear checker accepted an unreduced expression",
    )
    require(
        run_convex_checker(nonlinear_counterexample, set(), timeout_ms=250).get(
            "reason_code"
        )
        == "UNREDUCED_INPUT",
        "convex checker accepted an unreduced expression",
    )
    with patch(
        "clarity.discretization.checkers.linear.solve_linear_constraints",
        side_effect=RuntimeError("forced linear failure"),
    ):
        failed_linear = run_linear_checker(
            reduced_case("failed-linear", Op("<=", (x, Const(0)))),
            set(),
            timeout_ms=250,
        )
    require(
        failed_linear.get("outcome") == "DEFERRED"
        and failed_linear.get("reason_code") == "MALFORMED_OUTPUT",
        "linear backend failure did not defer",
    )
    with patch(
        "clarity.discretization.checkers.convex.solve_convex_constraints",
        side_effect=RuntimeError("forced convex failure"),
    ):
        failed_convex = run_convex_checker(
            reduced_case("failed-convex", nonlinear_counterexample),
            set(),
            timeout_ms=250,
        )
    require(
        failed_convex.get("outcome") == "DEFERRED"
        and failed_convex.get("reason_code") == "MALFORMED_OUTPUT",
        "convex backend failure did not defer",
    )
