"""Logical schemas underlying the structural buffered-Markov fast path."""

from __future__ import annotations

from .constraint_logic import (
    LogicSequent,
    LogicSort,
    apply,
    function,
    symbol,
)
from .structural_rule_validation import RuleSchema


MARKOV_RULE_PROFILE = "structural-markov-proof-rules-0.1"


def markov_rule_schemas() -> tuple[RuleSchema, ...]:
    """State the semantic implications used by structural factorization."""

    input_left = symbol("factor_input_left", LogicSort.REAL)
    input_right = symbol("factor_input_right", LogicSort.REAL)
    action_left = symbol("factor_action_left", LogicSort.BOOL)
    action_right = symbol("factor_action_right", LogicSort.BOOL)
    proposal = symbol("execution_proposal", LogicSort.BOOL)
    valid_left = symbol("execution_valid_left", LogicSort.BOOL)
    valid_right = symbol("execution_valid_right", LogicSort.BOOL)
    fallback_left = symbol("execution_fallback_left", LogicSort.BOOL)
    fallback_right = symbol("execution_fallback_right", LogicSort.BOOL)
    definition = symbol("definition_value", LogicSort.BOOL)
    output_a = symbol("definition_output_a", LogicSort.BOOL)
    output_b = symbol("definition_output_b", LogicSort.BOOL)
    completion_left = symbol("completion_left", LogicSort.BOOL)
    completion_right = symbol("completion_right", LogicSort.BOOL)
    successor_equal = symbol("successor_equal", LogicSort.BOOL)

    successor_left = function(
        "generic_successor", LogicSort.REAL, input_left, action_left
    )
    successor_right = function(
        "generic_successor", LogicSort.REAL, input_right, action_right
    )
    completion_function_left = function(
        "generic_completion", LogicSort.BOOL, input_left, action_left
    )
    completion_function_right = function(
        "generic_completion", LogicSort.BOOL, input_right, action_right
    )
    executed_left = apply(
        "ite", valid_left, proposal, fallback_left
    )
    executed_right = apply(
        "ite", valid_right, proposal, fallback_right
    )

    return (
        RuleSchema(
            "functional-definition-is-unique",
            "two outputs equal to the same symbolic definition are equal",
            LogicSequent((
                apply("eq", output_a, definition),
                apply("eq", output_b, definition),
            ), apply("eq", output_a, output_b)),
        ),
        RuleSchema(
            "identity-execution-preserves-proposal",
            "identity execution gives paired runs the same executed action",
            LogicSequent((), apply("eq", proposal, proposal)),
        ),
        RuleSchema(
            "keep-or-replace-execution-is-congruent",
            "equal admissibility decisions and unique replacements give equal execution",
            LogicSequent((
                apply("eq", valid_left, valid_right),
                apply("eq", fallback_left, fallback_right),
            ), apply("eq", executed_left, executed_right)),
        ),
        RuleSchema(
            "successor-function-congruence",
            "equal visible state and executed action give equal symbolic successor",
            LogicSequent((
                apply("eq", input_left, input_right),
                apply("eq", action_left, action_right),
            ), apply("eq", successor_left, successor_right)),
        ),
        RuleSchema(
            "completion-function-congruence",
            "equal visible state and prior action give equal completion",
            LogicSequent((
                apply("eq", input_left, input_right),
                apply("eq", action_left, action_right),
            ), apply("eq", completion_function_left, completion_function_right)),
        ),
        RuleSchema(
            "paired-result-factorization",
            "equal completion and equal nonterminal successors give equal outcomes",
            LogicSequent((
                apply("eq", completion_left, completion_right),
                apply(
                    "implies", apply("not", completion_left), successor_equal
                ),
            ), apply(
                "and",
                apply("eq", completion_left, completion_right),
                apply("or", completion_left, successor_equal),
            )),
        ),
    )


__all__ = ["MARKOV_RULE_PROFILE", "markov_rule_schemas"]
