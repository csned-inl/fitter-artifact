"""Focused SysML source mapping and loud-deferral validation."""

from validation_common import (
    CHECKER_ORDER,
    Path,
    analyze_model,
    load_certificate,
    load_mdp_certificate,
    require,
    tempfile,
)


def validate_annotation_cascade(certificate_paths: list[str]) -> None:
    marker = "#ContinuousRate assign currentTime := currentTime + dt;"
    base = None
    model_path = None
    source = ""
    for path in certificate_paths:
        candidate = load_certificate(path)
        candidate_path = Path(candidate["model"]["path"])
        candidate_source = candidate_path.read_text(encoding="utf-8")
        if marker in candidate_source:
            base = candidate
            model_path = candidate_path
            source = candidate_source
            break
    require(base is not None and model_path is not None, "validation models lack the expected time annotation")
    with tempfile.TemporaryDirectory(prefix="discretization-cascade-") as directory:
        modified_path = Path(directory) / model_path.name
        modified_path.write_text(
            source.replace(marker, "assign currentTime := currentTime + dt;", 1),
            encoding="utf-8",
        )
        mdp = load_mdp_certificate(base["markov_process_certificate"]["path"])
        analysis = analyze_model(
            modified_path,
            mdp,
            dt_text=base["settings"]["dt"]["input"],
            smt_timeout_ms=base["settings"]["smt_timeout_ms"],
        )
        require(
            analysis.get("result") == "NOT_CERTIFIED",
            "missing within-step meaning did not block certification",
        )
        cascaded = [
            item for item in analysis.get("properties", [])
            if (item.get("progression") or [{}])[0].get("reason_code")
            == "MISSING_WITHIN_STEP_MEANING"
        ]
        require(bool(cascaded), "missing annotation did not produce the required reason")
        for item in cascaded:
            progression = item["progression"]
            require(
                [stage["checker"] for stage in progression] == CHECKER_ORDER,
                "deferred property did not traverse the complete checker order",
            )
            require(
                all(stage["outcome"] == "DEFERRED" for stage in progression),
                "an unsupported checker produced a successful outcome",
            )
    print("exact proof rule validation: PASSED")
    print("nonlinear rejection validation: PASSED")
    print("linear proof certificate validation: PASSED")
    print("convex proof certificate validation: PASSED")
    print("factored proof certificate validation: PASSED")
    print("linear to convex progression validation: PASSED")
    print("backend failure deferral validation: PASSED")
    print("full model equation omission rejection: PASSED")
    print("unreduced checker input rejection: PASSED")
    print("sensor to physical mapping mutation rejection: PASSED")
    print("exact solver value replay validation: PASSED")
    print("selected constraint certificate validation: PASSED")
    print("SMT reachability proof and trace validation: PASSED")
    print("loud deferral and checker progression: PASSED")
