"""Certificate mutation rejection cases."""

from __future__ import annotations

import copy
from typing import Any, Callable

from clarity.certification.certificate import check_certificate

from .support import Battery


def _target_fact_index(cert: dict[str, Any]) -> int:
    target_keys = set(cert["proof"]["target_facts"])
    for idx, fact in enumerate(cert["proof"]["facts"]):
        if fact["key"] in target_keys:
            return idx
    raise AssertionError("could not find a target fact to mutate")


def _forward_fact_index(cert: dict[str, Any]) -> int:
    for idx, fact in enumerate(cert["proof"]["facts"]):
        if fact["rule"] == "forward_transition" and fact.get("premises"):
            return idx
    raise AssertionError("could not find a forward fact to mutate")


def _equation_forward_fact_index(cert: dict[str, Any]) -> int:
    for idx, fact in enumerate(cert["equation_proof"]["facts"]):
        if fact["rule"] == "equation_forward_transition" and fact.get("premises"):
            return idx
    raise AssertionError("could not find an equation-forward fact to mutate")


def _equation_target_fact_index(cert: dict[str, Any]) -> int:
    target_keys = set(cert["equation_proof"]["target_facts"])
    for idx, fact in enumerate(cert["equation_proof"]["facts"]):
        if fact["key"] in target_keys:
            return idx
    raise AssertionError("could not find an equation target fact to mutate")


def _assert_mutation_rejected(
    battery: Battery,
    base: dict[str, Any],
    name: str,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    cert = copy.deepcopy(base)
    mutator(cert)
    errors = check_certificate(cert)
    battery.require(
        f"negative_certificate/{name}",
        bool(errors),
        f"errors={errors[:3]}",
    )


def run_mutation_cases(battery: Battery, base: dict[str, Any]) -> None:
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("result_fail", lambda c: c.__setitem__("result", "FAIL")),
        (
            "claim_mdp_not_discharged",
            lambda c: c["claim"].__setitem__("mdp_theorem", "not_claimed_by_this_artifact"),
        ),
        (
            "claim_profile_not_discharged",
            lambda c: c["claim"].__setitem__("profile_mdp_theorem", "not_discharged"),
        ),
        (
            "claim_solver_backed_mdp_not_discharged",
            lambda c: c["claim"].__setitem__(
                "solver_backed_mdp_theorem", "not_claimed_by_this_artifact"
            ),
        ),
        (
            "missing_theorem_gate",
            lambda c: c.pop("theorem_gate", None),
        ),
        (
            "theorem_gate_profile_not_discharged",
            lambda c: c["theorem_gate"].__setitem__("profile_mdp_theorem", "not_discharged"),
        ),
        (
            "theorem_gate_obligations_false",
            lambda c: c["theorem_gate"].__setitem__("profile_obligations_discharged", False),
        ),
        (
            "theorem_gate_blocks_full_claim",
            lambda c: c["theorem_gate"].__setitem__("full_mdp_theorem_claim_allowed", False),
        ),
        (
            "theorem_gate_solver_uniqueness_not_discharged",
            lambda c: c["theorem_gate"]["solver_backed_uniqueness"].__setitem__(
                "status", "not_discharged"
            ),
        ),
        (
            "theorem_gate_solver_logic_mismatch",
            lambda c: c["theorem_gate"]["solver_backed_uniqueness"].__setitem__(
                "logic", "QF_BV"
            ),
        ),
        (
            "strict_mode_disabled",
            lambda c: c["proof_mode"].__setitem__("strict_no_legacy_fallback", False),
        ),
        (
            "legacy_transition_kind",
            lambda c: c["equations"]["transitions"][0].__setitem__(
                "kind", "legacy_transition_dependency"
            ),
        ),
        (
            "legacy_dep_expr",
            lambda c: c["equations"]["transitions"][0].__setitem__(
                "expr", {"type": "op", "op": "legacy_dep", "args": []}
            ),
        ),
        (
            "equation_unresolved_raw_ref",
            lambda c: c["equations"]["observations"][0].__setitem__(
                "unresolved_raw_refs", ["unclassifiedSymbol"]
            ),
        ),
        (
            "equation_raw_ref_audit_mismatch",
            lambda c: c["equations"]["observations"][0].__setitem__(
                "raw_refs", ["not_in_expr"]
            ),
        ),
        (
            "missing_ignored_state_reason",
            lambda c: c["sets"]["ignored_state_reasons"].pop(
                c["sets"]["ignored_state"][0], None
            ),
        ),
        (
            "missing_terminal_section",
            lambda c: c["mdp_obligations"].pop("terminal_state", None),
        ),
        (
            "terminal_not_covered",
            lambda c: c["mdp_obligations"]["terminal_state"].__setitem__(
                "status", "not_covered_by_q_and_action"
            ),
        ),
        (
            "terminal_equations_empty",
            lambda c: c["mdp_obligations"]["terminal_state"].__setitem__(
                "equations", []
            ),
        ),
        (
            "truncation_not_discharged",
            lambda c: c["mdp_obligations"]["truncation"].__setitem__(
                "status", "modeled_as_finite_horizon_augmented_state"
            ),
        ),
        (
            "reward_not_discharged",
            lambda c: c["mdp_obligations"]["reward"].__setitem__(
                "status", "not_discharged"
            ),
        ),
        (
            "reward_terminal_uncovered",
            lambda c: c["mdp_obligations"]["reward"]["covered_terms"].__setitem__(
                "all_terminal_state_terms_covered", False
            ),
        ),
        (
            "reward_external_terms_present",
            lambda c: c["mdp_obligations"]["reward"].__setitem__(
                "external_terms_not_yet_in_q", ["unmodeled terminal source"]
            ),
        ),
        (
            "done_not_discharged",
            lambda c: c["mdp_obligations"]["done"].__setitem__(
                "status", "not_discharged"
            ),
        ),
        (
            "done_covered_false",
            lambda c: c["mdp_obligations"]["done"].__setitem__("covered", False),
        ),
        (
            "shield_not_discharged",
            lambda c: c["mdp_obligations"]["shield"].__setitem__(
                "status", "not_discharged"
            ),
        ),
        (
            "shield_bad_semantic_status",
            lambda c: c["mdp_obligations"]["shield"].__setitem__(
                "semantic_status", "interface_recorded_not_semantically_proven"
            ),
        ),
        (
            "shield_missing_input",
            lambda c: c["mdp_obligations"]["shield"].__setitem__(
                "missing_input_params", ["done"]
            ),
        ),
        (
            "shield_missing_output",
            lambda c: c["mdp_obligations"]["shield"].__setitem__(
                "missing_output_params", ["shouldOpenValve1"]
            ),
        ),
        (
            "shield_unknown_ref",
            lambda c: c["mdp_obligations"]["shield"].__setitem__(
                "unknown_requirement_refs", ["hidden"]
            ),
        ),
        (
            "shield_input_uncovered",
            lambda c: c["mdp_obligations"]["shield"]["input_coverage"][0].__setitem__(
                "covered_by_q_and_action", False
            ),
        ),
        (
            "shield_output_not_unique",
            lambda c: c["mdp_obligations"]["shield"]["output_action_mapping"][0].__setitem__(
                "unique_action_var", False
            ),
        ),
        ("missing_proof", lambda c: c.__setitem__("proof", None)),
        ("missing_equation_proof", lambda c: c.__setitem__("equation_proof", None)),
        (
            "equation_proof_not_passes",
            lambda c: c["equation_proof"].__setitem__("passes", False),
        ),
        (
            "equation_target_fact_removed",
            lambda c: c["equation_proof"]["facts"].pop(_equation_target_fact_index(c)),
        ),
        (
            "equation_bad_forward_premise",
            lambda c: c["equation_proof"]["facts"][
                _equation_forward_fact_index(c)
            ].__setitem__("premises", ["not_a_real_equation_fact@0"]),
        ),
        (
            "equation_unsafe_guarded_inversion",
            lambda c: c["equation_proof"]["facts"].append(
                {
                    "key": "fakeSource@-1",
                    "var": "fakeSource",
                    "tau": -1,
                    "rule": "equation_guarded_copy_inversion",
                    "premises": [],
                    "detail": {
                        "equation_target": "fakeTarget",
                        "guard_status": "not_proven_true",
                        "inversion": "guarded_transition_copy",
                    },
                }
            ),
        ),
        (
            "missing_solver_advisory",
            lambda c: c.pop("solver_advisory", None),
        ),
        (
            "solver_advisory_bad_status",
            lambda c: c["solver_advisory"]["one_step_transition_closure"].__setitem__(
                "status", "magically_proven"
            ),
        ),
        (
            "solver_advisory_unknown_claims",
            lambda c: (
                c["solver_advisory"]["one_step_transition_closure"].__setitem__(
                    "status", "unknown"
                ),
                c["solver_advisory"]["one_step_transition_closure"].__setitem__(
                    "claim", "one_step_transition_closure"
                ),
            ),
        ),
        (
            "solver_advisory_unknown_not_claimed",
            lambda c: (
                c["solver_advisory"]["one_step_transition_closure"].__setitem__(
                    "status", "unknown"
                ),
                c["solver_advisory"]["one_step_transition_closure"].__setitem__(
                    "claim", "not_claimed_by_this_artifact"
                ),
            ),
        ),
        (
            "target_fact_removed",
            lambda c: c["proof"]["facts"].pop(_target_fact_index(c)),
        ),
        (
            "bad_forward_premise",
            lambda c: c["proof"]["facts"][_forward_fact_index(c)].__setitem__(
                "premises", ["not_a_real_fact@0"]
            ),
        ),
        (
            "model_hash_changed",
            lambda c: c["model"].__setitem__("sha256", "0" * 64),
        ),
    ]
    for name, mutator in mutations:
        _assert_mutation_rejected(battery, base, name, mutator)
