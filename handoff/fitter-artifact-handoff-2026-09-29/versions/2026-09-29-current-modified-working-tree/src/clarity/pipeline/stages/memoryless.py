"""Stage 2, current decision memorylessness check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clarity.pipeline.support import write_csv, write_json
from clarity.runtime.shield import SpecShield, collect_references
from clarity.sysml.inputs import SysMLInput


def run_memoryless_stage(
    out_dir: Path,
    models: list[SysMLInput],
) -> dict[str, Any]:
    stage_dir = out_dir / "02_memoryless"
    log_dir = out_dir / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "02_memoryless_check.txt"
    rows = []
    log_lines = []
    for model in models:
        requirement = SpecShield(str(model.path))
        references = collect_references(requirement.req_ast)
        allowed = (
            set(requirement.in_params)
            | set(requirement.out_params)
            | set(requirement.unchanging)
            | {requirement.subject_var}
        )
        unresolved = sorted(references - allowed)
        if unresolved:
            raise RuntimeError(
                f"{model.key} requirement references values outside its current "
                f"neural inputs and outputs: {unresolved}"
            )
        row = {
            "model": model.key,
            "model_name": model.name,
            "stage": "memoryless",
            "b_obs": 0,
            "b_act": 0,
            "claim": "current neural inputs determine the current controller constraint",
            "source": str(model.path),
            "model_sha256": model.sha256,
            "command_log": str(log_path.relative_to(out_dir)),
        }
        rows.append(row)
        log_lines.append(
            f"- {model.key}: current inputs={list(requirement.in_params)}, "
            f"outputs={list(requirement.out_params)}"
        )
    log_path.write_text(
        "SysML current-decision checks:\n" + "\n".join(log_lines) + "\n",
        encoding="utf-8",
    )
    run = {
        "command": "read #Neural action and #NeuralRequirement from each supplied SysML file",
        "returncode": 0,
        "log": str(log_path.relative_to(out_dir)),
    }
    fields = [
        "model",
        "model_name",
        "stage",
        "b_obs",
        "b_act",
        "claim",
        "source",
        "model_sha256",
        "command_log",
    ]
    write_csv(stage_dir / "summary.csv", rows, fields)
    write_json(stage_dir / "summary.json", {"run": run, "rows": rows})
    return {"run": run, "rows": rows, "fields": fields}
