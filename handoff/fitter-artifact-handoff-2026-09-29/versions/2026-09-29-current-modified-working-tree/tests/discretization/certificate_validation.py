"""Focused certificate integrity and mutation validation."""

from validation_common import (
    certificate_hash,
    check_certificate,
    copy,
    expand_analysis,
    load_certificate,
    require,
)


def validate_certificates(certificate_paths: list[str]) -> None:
    for path in certificate_paths:
        stored_certificate = load_certificate(path)
        errors = check_certificate(stored_certificate)
        require(not errors, f"valid certificate failed: {path}: {errors}")
        subtree_pool = stored_certificate.get("analysis", {}).get(
            "shared_subtrees"
        )
        if subtree_pool:
            corrupted_pool = copy.deepcopy(stored_certificate)
            nodes = corrupted_pool["analysis"]["shared_subtrees"]["nodes"]
            first_key = sorted(nodes)[0]
            if isinstance(nodes[first_key], dict):
                nodes[first_key]["corrupted"] = True
            else:
                nodes[first_key].append("corrupted")
            corrupted_pool["self_sha256"] = certificate_hash(corrupted_pool)
            require(
                any(
                    "shared analysis subtree hash" in error
                    for error in check_certificate(
                        corrupted_pool,
                        check_files=False,
                    )
                ),
                f"checker accepted a corrupted subtree pool: {path}",
            )
        expanded_analysis, expansion_errors = expand_analysis(
            stored_certificate.get("analysis", {})
        )
        require(not expansion_errors, f"certificate compaction failed: {path}: {expansion_errors}")
        certificate = copy.deepcopy(stored_certificate)
        certificate["analysis"] = expanded_analysis
        certificate["self_sha256"] = certificate_hash(certificate)
        errors = check_certificate(certificate)
        require(not errors, f"valid certificate failed: {path}: {errors}")
        mutated = copy.deepcopy(certificate)
        properties = mutated.get("analysis", {}).get("properties", [])
        require(bool(properties), f"certificate has no checked properties: {path}")
        reduction = next(
            (
                item.get("reduction", {})
                for item in properties
                if item.get("reduction", {}).get("trajectories")
            ),
            None,
        )
        require(reduction is not None, f"certificate has no physical trajectory: {path}")
        physical_target = reduction["trajectories"][0]["physical_value"]
        inventory = reduction["equation_inventory"]
        target_row = next(item for item in inventory if item["target"] == physical_target)
        target_row["included"] = False
        mutated["self_sha256"] = certificate_hash(mutated)
        mutation_errors = check_certificate(mutated)
        require(
            any(
                "physical equation" in error or "independent replay" in error
                for error in mutation_errors
            ),
            f"checker accepted an omitted physical equation: {path}",
        )
        del mutated
        all_inventory_targets = {
            item.get("target")
            for property_record in properties
            for item in property_record.get("reduction", {}).get("equation_inventory", [])
        }
        for required_target in ("vehicle_speedMps", "vehicle_gapMeters"):
            if required_target not in all_inventory_targets:
                continue
            missing_physics = copy.deepcopy(certificate)
            changed = False
            for property_record in missing_physics["analysis"]["properties"]:
                for item in property_record.get("reduction", {}).get("equation_inventory", []):
                    if item.get("target") == required_target and item.get("included") is True:
                        item["included"] = False
                        changed = True
            require(changed, f"{required_target} was never included: {path}")
            missing_physics["self_sha256"] = certificate_hash(missing_physics)
            require(
                any(
                    required_target in error
                    for error in check_certificate(missing_physics)
                ),
                f"checker accepted omitted {required_target}: {path}",
            )
            del missing_physics
        mapped_property = next(
            (
                item for item in properties
                if item.get("reduction", {}).get("sensor_to_physical_mappings")
            ),
            None,
        )
        if mapped_property is not None:
            bad_mapping = copy.deepcopy(certificate)
            selected = next(
                item for item in bad_mapping["analysis"]["properties"]
                if item.get("property_id") == mapped_property.get("property_id")
            )
            selected["reduction"]["sensor_to_physical_mappings"][0][
                "physical_value"
            ] += "_corrupted"
            bad_mapping["self_sha256"] = certificate_hash(bad_mapping)
            require(
                any(
                    "independent replay" in error or "sensor mapping equation" in error
                    for error in check_certificate(bad_mapping)
                ),
                f"checker accepted a corrupted sensor mapping: {path}",
            )
            del bad_mapping
        exact_stage = next(
            (
                stage
                for property_record in properties
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if (stage.get("proof") or {}).get("rule")
                == "exact_local_feasibility_replay_v1"
            ),
            None,
        )
        if exact_stage is not None:
            bad_values = copy.deepcopy(certificate)
            selected_stage = next(
                stage
                for property_record in bad_values["analysis"]["properties"]
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if (stage.get("proof") or {}).get("rule")
                == "exact_local_feasibility_replay_v1"
            )
            first_name = sorted(selected_stage["proof"]["exact_values"])[0]
            selected_stage["proof"]["exact_values"][first_name] = None
            bad_values["self_sha256"] = certificate_hash(bad_values)
            require(
                any(
                    "local feasibility" in error
                    for error in check_certificate(bad_values, check_files=False)
                ),
                f"checker accepted corrupted exact values: {path}",
            )
            del bad_values
        subset_stage = next(
            (
                stage
                for property_record in properties
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if stage.get("outcome") == "CERTIFIED"
                and (stage.get("proof") or {}).get("rule")
                == "solver_selected_subset_recertification_v1"
            ),
            None,
        )
        if subset_stage is not None:
            bad_subset = copy.deepcopy(certificate)
            selected_stage = next(
                stage
                for property_record in bad_subset["analysis"]["properties"]
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if stage.get("outcome") == "CERTIFIED"
                and (stage.get("proof") or {}).get("rule")
                == "solver_selected_subset_recertification_v1"
            )
            selected_stage["proof"]["selected_indices"][0] = -1
            bad_subset["self_sha256"] = certificate_hash(bad_subset)
            require(
                any(
                    "selected constraint" in error
                    for error in check_certificate(bad_subset, check_files=False)
                ),
                f"checker accepted corrupted selected constraints: {path}",
            )
            del bad_subset
        smt_reachability_stage = next(
            (
                stage
                for property_record in properties
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if stage.get("checker") == "smt_reachability"
                and stage.get("outcome") == "CERTIFIED"
            ),
            None,
        )
        if smt_reachability_stage is not None:
            bad_smt_proof = copy.deepcopy(certificate)
            selected_stage = next(
                stage
                for property_record in bad_smt_proof["analysis"]["properties"]
                for case in property_record.get("cases", [])
                for stage in case.get("progression", [])
                if stage.get("checker") == "smt_reachability"
                and stage.get("outcome") == "CERTIFIED"
            )
            certified_depth = next(
                item
                for item in selected_stage["proof"]["depth_attempts"]
                if item.get("proved") is True
            )
            certified_depth["induction_query"]["z3_proof_sha256"] = "corrupted"
            bad_smt_proof["self_sha256"] = certificate_hash(bad_smt_proof)
            require(
                any(
                    "SMT reachability no solution proof hash" in error
                    for error in check_certificate(
                        bad_smt_proof,
                        check_files=False,
                    )
                ),
                f"checker accepted a corrupted SMT reachability proof: {path}",
            )
            del bad_smt_proof
        print(f"{path}: VALIDATION PASSED")
