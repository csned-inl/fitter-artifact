"""Stage 4, within-step discretization safety certification."""

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


def run_discretization_stage(
    out_dir: Path,
    py: str,
    models: list[SysMLInput],
    dt: float,
    *,
    dt_text: str,
    optimization_timeout_ms: int,
    smt_timeout_ms: int,
) -> dict[str, Any]:
    stage_dir = out_dir / "04_discretization_safety"
    log_dir = out_dir / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "04_discretization_safety.txt"
    summary_path = stage_dir / "generation.json"
    cmd = [
        py,
        "-m",
        "clarity.pipeline.discretization_safety",
        "--out-json",
        str(summary_path),
        "--artifact-dir",
        str(stage_dir),
        "--mdp-dir",
        str(out_dir / "03_markov_mdp"),
        "--dt",
        dt_text,
        "--optimization-timeout-ms",
        str(optimization_timeout_ms),
        "--smt-timeout-ms",
        str(smt_timeout_ms),
        *[str(model.path) for model in models],
    ]
    run, raw_text = run_command(cmd, log_path, out_dir)
    generated = read_json(summary_path) if summary_path.exists() else {"rows": []}
    rows = generated.get("rows", [])
    write_command_summary_log(
        log_path,
        run,
        [
            "discretization-safety certificate generation:",
            *[
                f"- {row['model']}: result={row['result']}, "
                f"checker={row['checker']}, properties="
                f"{row['properties_certified']}/{row['properties_checked']}, "
                f"seconds={float(row['elapsed_seconds']):.6f}"
                for row in rows
            ],
            "- every certificate is replayed by the independent checker",
        ],
        raw_text,
    )
    if run["returncode"] != 0 or not summary_path.exists():
        raise RuntimeError(f"discretization-safety certification failed; see {log_path}")
    generated_dt = validate_dt(generated.get("settings", {}).get("dt"))
    if generated_dt != dt:
        raise RuntimeError(
            f"discretization-safety dt does not match run dt: {generated_dt} != {dt}"
        )
    if len(rows) != len(models):
        raise RuntimeError(
            "discretization-safety generation did not produce one result for every model"
        )
    if any(
        row.get("result") != "CERTIFIED" or row.get("checker") != "passed"
        for row in rows
    ):
        raise RuntimeError(
            f"one or more discretization-safety certificates failed; see {log_path}"
        )
    order = model_order(models)
    rows = sorted(rows, key=lambda row: order[row["model"]])
    fields = [
        "model",
        "dt",
        "result",
        "checker",
        "properties_checked",
        "properties_certified",
        "elapsed_seconds",
        "certificate_path",
    ]
    write_csv(stage_dir / "summary.csv", rows, fields)
    write_json(
        stage_dir / "summary.json",
        {
            "settings": generated.get("settings", {}),
            "run": run,
            "rows": rows,
            "result": generated.get("result"),
        },
    )
    return {"run": run, "rows": rows, "fields": fields}
