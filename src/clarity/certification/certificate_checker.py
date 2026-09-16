"""Independent structural checker for Markov certificate artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .certificate_schema import (
    PROFILE_MDP_THEOREM,
    PROFILE_OBLIGATIONS_DISCHARGED,
    PROOF_PROFILE,
    SCHEMA_VERSION,
    SOLVER_BACKED_MDP_THEOREM,
    fact_key,
    model_hash,
)
from .equation_reconstruct import fact_key as equation_fact_key
from .solver import MAX_SOLVER_POLYNOMIAL_DEGREE
from clarity.sysml.runtime_settings import validate_dt


def _expression_raw_references(expression: dict[str, Any]) -> set[str]:
    if expression.get("type") == "raw_ref":
        return {expression.get("path", "")}
    references: set[str] = set()
    for item in expression.get("args", []):
        references |= _expression_raw_references(item)
    for key in ("cond", "then", "else"):
        item = expression.get(key)
        if isinstance(item, dict):
            references |= _expression_raw_references(item)
    return references


def _contains_legacy_dependency(expression: dict[str, Any]) -> bool:
    if expression.get("type") == "op" and expression.get("op") == "legacy_dep":
        return True
    for item in expression.get("args", []):
        if _contains_legacy_dependency(item):
            return True
    for key in ("cond", "then", "else"):
        item = expression.get(key)
        if isinstance(item, dict) and _contains_legacy_dependency(item):
            return True
    return False


def _check_schema_and_equations(
    certificate: dict[str, Any], *, check_hash: bool
) -> list[str]:
    """Check schema, source identity, claims, diagnostics, and equations."""
    errors: list[str] = []
    if certificate.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version={certificate.get('schema_version')}")
    if certificate.get("proof_profile") != PROOF_PROFILE:
        errors.append(f"unsupported proof_profile={certificate.get('proof_profile')}")
    if certificate.get("result") != "PASS":
        errors.append(f"certificate result is not PASS: {certificate.get('result')}")
    claim = certificate.get("claim", {})
    if claim.get("level") != SOLVER_BACKED_MDP_THEOREM:
        errors.append(f"unsupported claim level={claim.get('level')}")
    if claim.get("profile_mdp_theorem") != "discharged":
        errors.append("profile MDP theorem is not discharged")
    if claim.get("mdp_theorem") != "discharged":
        errors.append("solver-backed MDP theorem is not discharged")
    if claim.get("solver_backed_mdp_theorem") != "discharged":
        errors.append("certificate does not claim discharged solver-backed MDP theorem")

    model_info = certificate.get("model", {})
    model_path = model_info.get("path")
    execution = certificate.get("execution")
    if not isinstance(execution, dict) or execution.get("version") != 1:
        errors.append("missing source-bound ordered execution")
    semantics = certificate.get("value_semantics")
    if not isinstance(semantics, dict) or semantics.get("version") != 1:
        errors.append("missing explicit physical/sampled value semantics")
    elif semantics.get("implicit_sample_to_physical_equality") is not False:
        errors.append("sampled values cannot be implicitly identified with physical values")
    else:
        state_variables = set(certificate.get("sets", {}).get("state", []))
        for pair in semantics.get("state_value_pairs", []):
            physical, sampled = pair.get("physical_value"), pair.get("sampled_value")
            if physical == sampled or not {physical, sampled} <= state_variables:
                errors.append("physical/sampled pair does not name two distinct state variables")
    if check_hash and (not model_path or not Path(model_path).is_file()):
        errors.append("source model is unavailable for semantic verification")
    if check_hash and model_path and Path(model_path).is_file():
        if model_hash(model_path) != model_info.get("sha256"):
            errors.append("model sha256 does not match certificate")
        from .strict_extract import extract_equation_model
        try:
            source_model = extract_equation_model(str(model_path))
            from .ordered_execution import validate_execution_description
            errors.extend(validate_execution_description(execution, model_path))
            from .certificate_generation import _equation_to_dict
            for section in ('definitions', 'observations', 'terminals', 'transitions', 'requirements'):
                expected = [_equation_to_dict(e, source_model.constants) for e in
                            sorted(getattr(source_model, section).values(), key=lambda e: e.target)]
                if certificate.get('equations', {}).get(section) != expected:
                    errors.append(f'{section} equations differ from source reconstruction')
            from .relevance import compute_transition_closed_relevance
            from .solver import one_step_transition_closure
            q = compute_transition_closed_relevance(source_model).q
            if set(certificate.get('sets', {}).get('q', [])) != set(q):
                errors.append('certified state differs from source relevance')
            checked = one_step_transition_closure(source_model, set(q), timeout_ms=1000)
            if checked.get('status') != 'discharged':
                errors.append('source-reconstructed solver obligation is not discharged')
            if semantics != source_model.value_semantics:
                errors.append("physical/sampled value semantics do not match source")
            for diagnostic in source_model.diagnostics:
                if diagnostic.severity in {"warning", "error"}:
                    errors.append(diagnostic.pretty())
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"source value reconstruction failed: {exc}")

    diagnostics = certificate.get("diagnostics", {})
    if diagnostics.get("blocking"):
        errors.append("certificate contains blocking diagnostics")
    if diagnostics.get("legacy_dependency_fallback_transitions"):
        errors.append("certificate uses legacy dependency fallback transitions")
    proof_mode = certificate.get("proof_mode", {})
    if not proof_mode.get("strict_no_legacy_fallback"):
        errors.append("certificate proof mode is not strict no-fallback")
    for section, equations in certificate.get("equations", {}).items():
        for equation in equations:
            if equation.get("kind") == "legacy_transition_dependency":
                errors.append(
                    f"{section} contains legacy transition dependency: "
                    f"{equation.get('target')}"
                )
            if _contains_legacy_dependency(equation.get("expr", {})):
                errors.append(
                    f"{section} contains legacy_dep expression: {equation.get('target')}"
                )
            if equation.get("unresolved_raw_refs"):
                errors.append(
                    f"{section} contains unresolved raw refs: {equation.get('target')}"
                )
            expression_references = _expression_raw_references(equation.get("expr", {}))
            declared_references = set(equation.get("raw_refs", []))
            if expression_references != declared_references:
                errors.append(
                    f"{section} raw-ref audit mismatch: {equation.get('target')}"
                )
            if set(equation.get("unresolved_raw_refs", [])) & set(
                equation.get("constant_refs", [])
            ):
                errors.append(
                    f"{section} raw-ref classification conflict: {equation.get('target')}"
                )

    return errors


def _check_theorem_gate(certificate: dict[str, Any]) -> list[str]:
    """Check theorem gates, noninterference records, and solver evidence."""
    errors: list[str] = []
    obligations = certificate.get("mdp_obligations", {})
    if obligations.get("overall_mdp_theorem_status") != PROFILE_OBLIGATIONS_DISCHARGED:
        errors.append(
            "profile obligations are not discharged or theorem boundary is unclear"
        )
    theorem_gate = certificate.get("theorem_gate", {})
    if theorem_gate.get("profile") != PROFILE_MDP_THEOREM:
        errors.append(f"unsupported theorem gate profile={theorem_gate.get('profile')}")
    if theorem_gate.get("solver_backed_profile") != SOLVER_BACKED_MDP_THEOREM:
        errors.append(
            "unsupported solver-backed theorem profile="
            f"{theorem_gate.get('solver_backed_profile')}"
        )
    if theorem_gate.get("profile_mdp_theorem") != "discharged":
        errors.append("theorem gate profile theorem is not discharged")
    if theorem_gate.get("solver_backed_mdp_theorem") != "discharged":
        errors.append("theorem gate solver-backed MDP theorem is not discharged")
    if theorem_gate.get("profile_obligations_discharged") is not True:
        errors.append("theorem gate profile obligations are not discharged")
    solver_gate = theorem_gate.get("solver_backed_uniqueness", {})
    if solver_gate.get("status") != "discharged":
        errors.append("solver-backed uniqueness status is not discharged")
    if solver_gate.get("source") != "solver_advisory.one_step_transition_closure":
        errors.append("solver-backed uniqueness source is not the recorded solver advisory")
    if solver_gate.get("result_status") != "discharged":
        errors.append("solver-backed uniqueness result status is not discharged")
    if solver_gate.get("claim") != "one_step_transition_closure":
        errors.append("solver-backed uniqueness claim label is wrong")
    if solver_gate.get("solver") != "z3":
        errors.append("solver-backed uniqueness must be discharged by z3")
    if solver_gate.get("logic") not in {"QF_UFLRA", "QF_UFNRA"}:
        errors.append(f"unsupported solver logic={solver_gate.get('logic')}")
    degree = solver_gate.get("max_polynomial_degree")
    if (
        not isinstance(degree, int)
        or degree < 0
        or degree > MAX_SOLVER_POLYNOMIAL_DEGREE
    ):
        errors.append(f"unsupported solver polynomial degree={degree}")
    if not solver_gate.get("visible_terms_checked"):
        errors.append("solver-backed uniqueness records no visible terms checked")
    if theorem_gate.get("full_mdp_theorem_claim_allowed") is not True:
        errors.append("theorem gate does not allow the solver-backed MDP claim")
    if theorem_gate.get("full_mdp_theorem_blockers"):
        errors.append("theorem gate records blockers despite discharged solver-backed claim")

    sets_for_noninterference = certificate.get("sets", {})
    ignored_state = set(sets_for_noninterference.get("ignored_state", []))
    ignored_reasons = sets_for_noninterference.get("ignored_state_reasons", {})
    if ignored_state and not isinstance(ignored_reasons, dict):
        errors.append("ignored-state reasons are not recorded as a mapping")
        ignored_reasons = {}
    for var in sorted(ignored_state):
        reason = ignored_reasons.get(var)
        if not isinstance(reason, str) or "backward cone" not in reason:
            errors.append(f"ignored state lacks noninterference reason: {var}")
    for var in sorted(set(ignored_reasons) - ignored_state):
        errors.append(f"ignored-state reason recorded for non-ignored state: {var}")

    solver_advisory = certificate.get("solver_advisory", {})
    one_step = solver_advisory.get("one_step_transition_closure")
    if one_step is None:
        errors.append("missing solver advisory one-step transition-closure section")
    elif one_step.get("status") not in {
        "discharged",
        "counterexample",
        "unknown",
        "unavailable",
    }:
        errors.append(f"unsupported solver advisory status={one_step.get('status')}")
    elif one_step.get("status") == "discharged":
        if one_step.get("claim") != "one_step_transition_closure":
            errors.append("solver advisory discharged status has wrong claim label")
    elif one_step.get("claim") != "not_claimed_by_this_artifact":
        errors.append("non-discharged solver advisory must not claim theorem support")
    if one_step is not None:
        if one_step.get("status") != "discharged":
            errors.append("solver advisory one-step transition closure is not discharged")
        for key in (
            "claim",
            "solver",
            "logic",
            "max_polynomial_degree",
            "timeout_ms",
            "q_size",
            "actions_size",
            "visible_terms_checked",
        ):
            if solver_gate.get(key) != one_step.get(key):
                errors.append(f"solver theorem gate/advisory mismatch: {key}")
    return errors


def _check_mdp_obligations(certificate: dict[str, Any]) -> list[str]:
    """Check observation, terminal, reward, completion, and shield obligations."""
    errors: list[str] = []
    obligations = certificate.get("mdp_obligations", {})
    for key in (
        "observation",
        "requirement_status",
        "terminal_state",
        "truncation",
        "reward",
        "done",
        "shield",
    ):
        if key not in obligations:
            errors.append(f"missing MDP obligation section: {key}")
    observation = obligations.get("observation", {})
    if observation and observation.get("status") != "covered_by_q":
        errors.append("observation obligations are not covered by q")
    requirement_status = obligations.get("requirement_status", {})
    if requirement_status and requirement_status.get("status") != "covered_by_q_and_action":
        errors.append("requirement-status obligations are not covered by q and action")
    reward = obligations.get("reward", {})
    if reward:
        if reward.get("status") != "discharged_over_augmented_state":
            errors.append("reward obligation is not discharged over augmented state")
        covered_terms = reward.get("covered_terms", {})
        if covered_terms.get("all_safety_statuses_covered") is not True:
            errors.append("reward safety-status terms are not fully covered")
        if covered_terms.get("all_terminal_state_terms_covered") is not True:
            errors.append("reward terminal-state terms are not fully covered")
        if covered_terms.get("finite_horizon_truncation_covered") is not True:
            errors.append("reward truncation term is not covered")
        if reward.get("external_terms_not_yet_in_q"):
            errors.append("reward obligation still has external terms outside q")
    terminal_state = obligations.get("terminal_state", {})
    if terminal_state and terminal_state.get("status") not in {
        "covered_by_q_and_action",
        "not_covered_by_q_and_action",
        "absent_from_simulator_state",
    }:
        errors.append("terminal-state obligation has unknown status")
    if terminal_state:
        if terminal_state.get("status") != "covered_by_q_and_action":
            errors.append("terminal-state obligation is not covered by q and action")
        if not terminal_state.get("equations"):
            errors.append("terminal-state obligation does not record terminal equations")
    truncation = obligations.get("truncation", {})
    if (
        truncation
        and truncation.get("status")
        != "discharged_as_finite_horizon_augmented_state"
    ):
        errors.append(
            "truncation obligation is not discharged as finite-horizon augmented state"
        )
    done = obligations.get("done", {})
    if done:
        if done.get("status") != "discharged_over_augmented_state":
            errors.append("done obligation is not discharged over augmented state")
        if done.get("covered") is not True:
            errors.append("done obligation covered flag is not true")
    shield = obligations.get("shield", {})
    if shield:
        if shield.get("status") != "discharged":
            errors.append("shield obligation is not discharged")
        if shield.get("semantic_status") != "discharged_discrete_exact_ast":
            errors.append("shield semantic status is not discharged")
        if shield.get("runtime_class") != "SpecShield":
            errors.append("shield runtime class is not recognized")
        if shield.get("missing_input_params"):
            errors.append("shield has missing input params")
        if shield.get("missing_output_params"):
            errors.append("shield has missing output params")
        if shield.get("unknown_requirement_refs"):
            errors.append("shield has unknown requirement refs")
        if not shield.get("predicate_ast"):
            errors.append("shield predicate AST is missing")
        if not shield.get("input_coverage"):
            errors.append("shield input coverage is missing")
        for item in shield.get("input_coverage", []):
            if item.get("covered_by_q_and_action") is not True:
                errors.append(f"shield input is not covered: {item.get('param')}")
        if not shield.get("output_action_mapping"):
            errors.append("shield output action mapping is missing")
        for item in shield.get("output_action_mapping", []):
            if item.get("unique_action_var") is not True:
                errors.append(f"shield output is not uniquely mapped: {item.get('param')}")
        if not shield.get("executed_action_history"):
            errors.append("shield does not record executed-action history semantics")
    return errors


def _check_reconstruction_trace(certificate: dict[str, Any]) -> list[str]:
    """Replay the dependency-graph reconstruction trace."""
    errors: list[str] = []
    proof = certificate.get("proof")
    if not proof:
        errors.append("missing proof section")
        return errors
    if not proof.get("passes"):
        errors.append("proof section does not pass")

    settings = certificate.get("settings", {})
    try:
        validate_dt(settings.get("dt"))
    except (TypeError, ValueError):
        errors.append(f"invalid certificate dt={settings.get('dt')}")
    buffer = certificate.get("buffer") or {}
    sets = certificate.get("sets", {})
    dependencies = certificate.get("dependency_model", {})

    b_obs = int(buffer.get("b_obs", -1))
    b_act = int(buffer.get("b_act", -1))
    horizon = int(proof.get("horizon", settings.get("horizon", -1)))
    if proof.get("b_obs") != b_obs or proof.get("b_act") != b_act:
        errors.append("proof buffer does not match certificate buffer")

    state = set(sets.get("state", []))
    actions = set(sets.get("actions", []))
    observations = set(sets.get("observed_state_vars", []))
    time_vars = set(sets.get("time_vars", []))
    initial_values = sets.get("initial_values", {})
    if not isinstance(initial_values, dict):
        errors.append("sets.initial_values is not an object")
        initial_values = {}
    for var in sorted(time_vars):
        if var not in initial_values:
            errors.append(f"known schedule state lacks a SysML initial value: {var}")
    next_support = {
        key: set(value) for key, value in dependencies.get("nsupp", {}).items()
    }
    copies = set(dependencies.get("copies", []))
    sampled_memories = {
        rule["target"]: rule for rule in dependencies.get("sampled_memories", [])
    }
    min_tau = -horizon

    known: set[str] = set()
    for fact in proof.get("facts", []):
        key = fact.get("key")
        var = fact.get("var")
        tau = fact.get("tau")
        rule = fact.get("rule")
        premises = fact.get("premises", [])
        detail = fact.get("detail", {})

        if key != fact_key(var, tau):
            errors.append(f"malformed fact key: {fact}")
            continue
        if key in known:
            errors.append(f"duplicate fact: {key}")
            continue
        if not isinstance(tau, int) or tau < min_tau or tau > 0:
            errors.append(f"fact tau out of proof horizon: {key}")
            continue

        if rule == "deterministic_time_known":
            if var not in time_vars:
                errors.append(f"time fact for non-time variable: {key}")
                continue
        elif rule == "observation_buffer":
            if var not in observations or not (-b_obs <= tau <= 0):
                errors.append(f"invalid observation-buffer fact: {key}")
                continue
        elif rule == "executed_action_history":
            if var not in actions or not (-b_act <= tau < 0):
                errors.append(f"invalid action-history fact: {key}")
                continue
        elif rule == "forward_transition":
            transition_var = detail.get("transition_var")
            from_tau = detail.get("from_tau")
            support = set(detail.get("support", []))
            expected = {fact_key(item, from_tau) for item in support}
            if var != transition_var or tau != from_tau + 1:
                errors.append(f"invalid forward-transition target: {key}")
                continue
            if support != next_support.get(var, set()):
                errors.append(f"forward support mismatch for {key}")
                continue
            if set(premises) != expected or not expected <= known:
                errors.append(f"forward premises not established for {key}")
                continue
        elif rule == "copy_inversion":
            transition_var = detail.get("transition_var")
            known_transition_tau = detail.get("known_transition_tau")
            if transition_var not in copies:
                errors.append(f"copy inversion from non-copy transition: {key}")
                continue
            support = next_support.get(transition_var, set())
            if support != {var}:
                errors.append(f"copy inversion support mismatch for {key}")
                continue
            expected = {fact_key(transition_var, known_transition_tau)}
            if (
                known_transition_tau != tau + 1
                or set(premises) != expected
                or not expected <= known
            ):
                errors.append(f"copy inversion premise not established for {key}")
                continue
        elif rule == "sampled_memory_bound":
            target_var = detail.get("target")
            source = detail.get("source")
            delay = int(detail.get("max_delay", -1))
            if var != target_var or target_var not in sampled_memories:
                errors.append(f"invalid sampled-memory fact: {key}")
                continue
            rule_definition = sampled_memories[target_var]
            if (
                rule_definition.get("source") != source
                or int(rule_definition.get("max_delay")) != delay
            ):
                errors.append(f"sampled-memory rule mismatch for {key}")
                continue
            expected = {
                fact_key(source, source_tau) for source_tau in range(tau - delay, tau)
            }
            if set(premises) != expected or not expected <= known:
                errors.append(f"sampled-memory premises not established for {key}")
                continue
        else:
            errors.append(f"unknown proof rule {rule!r} for {key}")
            continue
        known.add(key)

    for target_key in proof.get("target_facts", []):
        if target_key not in known:
            errors.append(f"target fact not established: {target_key}")
    for missing in proof.get("missing_target_facts", []):
        errors.append(f"proof reports missing target fact: {missing}")

    return errors


def _check_equation_trace(certificate: dict[str, Any]) -> list[str]:
    """Replay the independent equation reconstruction trace."""
    errors: list[str] = []
    settings = certificate.get("settings", {})
    buffer = certificate.get("buffer") or {}
    sets = certificate.get("sets", {})
    b_obs = int(buffer.get("b_obs", -1))
    b_act = int(buffer.get("b_act", -1))
    state = set(sets.get("state", []))
    actions = set(sets.get("actions", []))
    time_vars = set(sets.get("time_vars", []))

    equation_proof = certificate.get("equation_proof")
    if not equation_proof:
        errors.append("missing equation proof section")
        return errors
    if equation_proof.get("proof_engine") != "equation_ir_syntactic_v1":
        errors.append("equation proof engine is unsupported")
    if not equation_proof.get("passes"):
        errors.append("equation proof section does not pass")
    if equation_proof.get("b_obs") != b_obs or equation_proof.get("b_act") != b_act:
        errors.append("equation proof buffer does not match certificate buffer")

    equation_horizon = int(
        equation_proof.get("horizon", settings.get("horizon", -1))
    )
    transition_equations = {
        equation.get("target"): equation
        for equation in certificate.get("equations", {}).get("transitions", [])
    }
    observation_equations = {
        equation.get("target"): equation
        for equation in certificate.get("equations", {}).get("observations", [])
    }
    equation_known: set[str] = set()
    for fact in equation_proof.get("facts", []):
        key = fact.get("key")
        var = fact.get("var")
        tau = fact.get("tau")
        rule = fact.get("rule")
        premises = fact.get("premises", [])
        detail = fact.get("detail", {})

        if key != equation_fact_key(var, tau):
            errors.append(f"malformed equation-proof fact key: {fact}")
            continue
        if key in equation_known:
            errors.append(f"duplicate equation-proof fact: {key}")
            continue
        if not isinstance(tau, int) or tau < -equation_horizon or tau > 0:
            errors.append(f"equation-proof fact tau out of horizon: {key}")
            continue

        if rule == "deterministic_time_known":
            if var not in time_vars:
                errors.append(f"equation proof time fact for non-time variable: {key}")
                continue
        elif rule == "equation_observation_direct_copy":
            if var not in state or not (-b_obs <= tau <= 0):
                errors.append(f"invalid equation observation fact: {key}")
                continue
            equation_target = detail.get("equation_target")
            equation = observation_equations.get(equation_target)
            if equation is None or equation.get("pretty") != detail.get("equation_pretty"):
                errors.append(
                    f"equation observation fact does not cite a known equation: {key}"
                )
                continue
            if detail.get("source") != var:
                errors.append(f"equation observation source mismatch: {key}")
                continue
        elif rule == "executed_action_history":
            if var not in actions or not (-b_act <= tau < 0):
                errors.append(f"invalid equation action-history fact: {key}")
                continue
        elif rule == "equation_forward_transition":
            equation_target = detail.get("equation_target")
            from_tau = detail.get("from_tau")
            support = set(detail.get("support", []))
            expected = {
                equation_fact_key(reference, from_tau) for reference in support
            }
            equation = transition_equations.get(equation_target)
            if equation_target != var or tau != from_tau + 1:
                errors.append(f"invalid equation-forward target: {key}")
                continue
            if equation is None or equation.get("pretty") != detail.get("equation_pretty"):
                errors.append(
                    f"equation-forward fact does not cite a known transition: {key}"
                )
                continue
            if set(premises) != expected or not expected <= equation_known:
                errors.append(f"equation-forward premises not established for {key}")
                continue
        elif rule == "equation_direct_copy_inversion":
            equation_target = detail.get("equation_target")
            known_transition_tau = detail.get("known_transition_tau")
            equation = transition_equations.get(equation_target)
            expected = {equation_fact_key(equation_target, known_transition_tau)}
            if detail.get("source") != var:
                errors.append(f"equation copy-inversion source mismatch: {key}")
                continue
            if detail.get("inversion") != "direct_transition_copy":
                errors.append(f"equation copy-inversion kind is not direct: {key}")
                continue
            if equation is None or equation.get("pretty") != detail.get("equation_pretty"):
                errors.append(
                    f"equation copy-inversion does not cite a known transition: {key}"
                )
                continue
            if (
                known_transition_tau != tau + 1
                or set(premises) != expected
                or not expected <= equation_known
            ):
                errors.append(f"equation copy-inversion premise not established for {key}")
                continue
        elif rule == "equation_guarded_copy_inversion":
            if detail.get("guard_status") != "proven_true":
                errors.append(f"unsafe guarded-copy inversion in equation proof: {key}")
                continue
            if detail.get("inversion") != "guarded_transition_copy":
                errors.append(
                    f"malformed guarded-copy inversion in equation proof: {key}"
                )
                continue
            expected = {
                equation_fact_key(detail.get("equation_target"), tau + 1)
            }
            if set(premises) != expected or not expected <= equation_known:
                errors.append(f"guarded-copy inversion premise not established for {key}")
                continue
        elif rule == "sampled_memory_bound":
            target_var = detail.get("target")
            source = detail.get("source")
            delay = int(detail.get("max_delay", -1))
            if var != target_var or delay < 1:
                errors.append(f"invalid equation sampled-memory fact: {key}")
                continue
            expected = {
                equation_fact_key(source, source_tau)
                for source_tau in range(tau - delay, tau)
            }
            if set(premises) != expected or not expected <= equation_known:
                errors.append(f"equation sampled-memory premises not established for {key}")
                continue
        else:
            errors.append(f"unknown equation-proof rule {rule!r} for {key}")
            continue
        equation_known.add(key)

    for target_key in equation_proof.get("target_facts", []):
        if target_key not in equation_known:
            errors.append(f"equation target fact not established: {target_key}")
    for missing in equation_proof.get("missing_target_facts", []):
        errors.append(f"equation proof reports missing target fact: {missing}")

    return errors


def check_certificate(
    certificate: dict[str, Any], *, check_hash: bool = True
) -> list[str]:
    """Return every structural or replay error found in a certificate."""
    errors = _check_schema_and_equations(
        certificate, check_hash=check_hash
    )
    errors.extend(_check_theorem_gate(certificate))
    errors.extend(_check_mdp_obligations(certificate))
    errors.extend(_check_reconstruction_trace(certificate))
    if not certificate.get("proof"):
        return errors
    errors.extend(_check_equation_trace(certificate))
    return errors
