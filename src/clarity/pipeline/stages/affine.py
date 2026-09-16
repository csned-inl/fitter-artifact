"""Stage 1, direct neural requirement evaluation."""

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


def run_affine_stage(
    out_dir: Path,
    py: str,
    models: list[SysMLInput],
    dt: float,
) -> dict[str, Any]:
    stage_dir = out_dir / "01_affine_rule"
    log_dir = out_dir / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    rule_runs: list[dict[str, Any]] = []
    for model in models:
        report_path = stage_dir / "models" / f"{model.key}.json"
        log_path = log_dir / f"01_rule_{model.key}.txt"
        cmd = [
            py,
            "-m",
            "clarity.pipeline.affine_rule",
            str(model.path),
            "--episodes",
            "20",
            "--dt",
            str(dt),
            "--out-json",
            str(report_path),
        ]
        run, raw_text = run_command(cmd, log_path, out_dir)
        rule_runs.append({"model": model.key, "run": run})
        if run["returncode"] != 0 or not report_path.exists():
            raise RuntimeError(f"{model.key} rule extraction failed; see {log_path}")
        report = read_json(report_path)
        report_dt = validate_dt(report.get("dt"))
        if report_dt != dt:
            raise RuntimeError(
                f"{model.key} affine-stage dt does not match run dt: "
                f"{report_dt} != {dt}"
            )
        row = {
            "model": model.key,
            "model_name": model.name,
            "action_kind": model.action_kind,
            "dt": report_dt,
            "status": report.get("status", ""),
            "method": report.get("method", ""),
            "learned_params": report.get("learned_params", ""),
            "recurrent_state": report.get("recurrent_state", ""),
            "episodes": report.get("episodes", ""),
            "success_rate": report.get("success_rate", ""),
            "safety_violation_rate": report.get("safety_violation_rate", ""),
            "evaluation_error_rate": report.get("evaluation_error_rate", ""),
            "override_rate": report.get("override_rate", ""),
            "pointwise_agreement": report.get("pointwise_agreement", ""),
            "source": str(report_path.relative_to(out_dir)),
        }
        rows.append(row)
        metric_text = (
            f"episodes={row['episodes']}, success={float(row['success_rate']):.3f}, "
            f"safety={float(row['safety_violation_rate']):.3f}, "
            f"override={float(row['override_rate']):.3f}"
            if row["status"] == "evaluated"
            else f"status={row['status']}"
        )
        write_command_summary_log(
            log_path,
            run,
            [
                f"{model.key} #NeuralRequirement extraction:",
                f"- {metric_text}",
                f"- metrics json: {report_path.relative_to(out_dir)}",
            ],
            raw_text,
        )
    order = model_order(models)
    rows = sorted(rows, key=lambda row: order[row["model"]])
    fields = [
        "model",
        "model_name",
        "action_kind",
        "dt",
        "status",
        "method",
        "learned_params",
        "recurrent_state",
        "episodes",
        "success_rate",
        "safety_violation_rate",
        "evaluation_error_rate",
        "override_rate",
        "pointwise_agreement",
        "source",
    ]
    write_csv(stage_dir / "summary.csv", rows, fields)
    write_json(
        stage_dir / "summary.json",
        {"settings": {"dt": dt}, "rule_runs": rule_runs, "rows": rows},
    )
    return {"rule_runs": rule_runs, "rows": rows, "fields": fields}
