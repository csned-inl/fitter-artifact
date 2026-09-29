"""Stage 3, finite buffer and Markov process certification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clarity.pipeline.support import (
    model_order,
    read_json,
    run_command,
    write_command_summary_log,
    write_csv,
    write_json,
)
from clarity.sysml.inputs import SysMLInput
from clarity.sysml.runtime_settings import validate_dt


def run_markov_stage(
    out_dir: Path,
    py: str,
    models: list[SysMLInput],
    dt: float,
) -> dict[str, Any]:
    stage_dir = out_dir / "03_markov_mdp"
    log_dir = out_dir / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    # Preserve the value inventory even when the proof gate stops this stage.
    (stage_dir / "value_semantics").mkdir(parents=True, exist_ok=True)
    from clarity.certification.strict_extract import extract_equation_model
    for model in models:
        equations = extract_equation_model(str(model.path))
        write_json(stage_dir / "value_semantics" / f"{model.key}.json", {
            "model": str(model.path),
            "value_semantics": equations.value_semantics,
            "state_variables": sorted(equations.state),
            "diagnostics": [d.pretty() for d in equations.diagnostics],
            "certification_status": "UNVERIFIED",
        })
    generation_log = log_dir / "03_markov_mdp_z3_generation.txt"
    generation_json = stage_dir / "markov_mdp_generation.json"
    generation_cmd = [
        py,
        "-m",
        "clarity.pipeline.markov_mdp",
        "--out-json",
        str(generation_json),
        "--artifact-dir",
        str(stage_dir),
        "--max-obs",
        "2",
        "--max-act",
        "4",
        "--dt",
        str(dt),
        *[str(model.path) for model in models],
    ]
    generation_run, generation_text = run_command(
        generation_cmd, generation_log, out_dir
    )
    if not generation_json.exists():
        raise RuntimeError(f"Markov/MDP proof generation failed; see {generation_log}")
    generation_summary = read_json(generation_json)
    generation_dt = validate_dt(generation_summary.get("settings", {}).get("dt"))
    if generation_dt != dt:
        raise RuntimeError(
            f"Markov/MDP generation dt does not match run dt: {generation_dt} != {dt}"
        )
    rows = generation_summary.get("rows", [])
    if len(rows) != len(models):
        raise RuntimeError(
            "Markov/MDP proof generation did not produce one result for every SysML file"
        )
    order = model_order(models)
    for row in rows:
        row_dt = validate_dt(row.get("dt"))
        if row_dt != dt:
            raise RuntimeError(
                f"{row.get('model')} Markov/MDP row dt does not match run dt: "
                f"{row_dt} != {dt}"
            )
    rows = sorted(rows, key=lambda row: order[row["model"]])
    write_command_summary_log(
        generation_log,
        generation_run,
        [
            "fresh Markov/MDP proof generation:",
            *[
                f"- {row['model']}: checker={row['checker']}, "
                f"solver_status={row['solver_status']}, "
                f"buffer=b_obs={row['b_obs']}, b_act={row['b_act']}"
                for row in rows
            ],
            "- certificates, reduced specs, SMT-LIB queries, and proof/counterexample files are saved fresh",
            f"- compact summary json: {generation_json.relative_to(out_dir)}",
        ],
        generation_text,
    )
    fields = [
        "model",
        "dt",
        "b_obs",
        "b_act",
        "claim",
        "checker",
        "solver_status",
        "solver",
        "logic",
        "max_polynomial_degree",
        "source",
        "certificate_saved",
        "certificate_path",
        "reduced_mdp_spec_path",
        "smt2_path",
        "proof_or_disproof_path",
    ]
    write_csv(stage_dir / "summary.csv", rows, fields)
    write_json(
        stage_dir / "summary.json",
        {
            "settings": {"dt": dt},
            "result": "PASS" if all(row["checker"] == "passed" for row in rows) else "FAILED",
            "generation_run": generation_run,
            "generation_rows": rows,
            "certificate_policy": generation_summary.get("certificate_policy", ""),
        },
    )
    return {
        "result": "PASS" if all(row["checker"] == "passed" for row in rows) else "FAILED",
        "generation_run": generation_run,
        "rows": rows,
        "fields": fields,
    }
