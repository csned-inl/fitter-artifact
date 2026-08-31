"""Stage 3, finite buffer and Markov process certification."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from clarity.pipeline.support import (
    compact_output,
    model_order,
    read_json,
    run_command,
    write_command_summary_log,
    write_csv,
    write_json,
)
from clarity.sysml.inputs import SysMLInput
from clarity.sysml.runtime_settings import validate_dt


def _parse_closure_text(
    text: str,
    models: list[SysMLInput],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: str | None = None
    paths = {str(model.path): model for model in models}
    pattern = re.compile(
        r"last (?P<b_act>\d+) actions \+ current \+ (?P<b_obs>\d+) past obs"
    )
    for line in text.splitlines():
        model = paths.get(line.strip())
        if model is not None:
            current = model.key
            continue
        match = pattern.search(line)
        if match and current:
            rows.append(
                {
                    "model": current,
                    "stage": "markov_mdp",
                    "b_obs": int(match.group("b_obs")),
                    "b_act": int(match.group("b_act")),
                    "claim": "provable Markov/MDP buffer",
                }
            )
            current = None
    order = model_order(models)
    return sorted(rows, key=lambda row: order[row["model"]])


def _write_closure_log(
    log_path: Path,
    run: dict[str, Any],
    rows: list[dict[str, Any]],
    raw_text: str,
) -> None:
    lines = [
        f"command: {run['command']}",
        f"returncode: {run['returncode']}",
        "",
        "buffer results:",
    ]
    for row in rows:
        lines.append(
            f"- {row['model']}: b_obs={row['b_obs']}, b_act={row['b_act']}; "
            f"{row['claim']}"
        )
    if run["returncode"] != 0:
        lines.extend(["", "failure tail:", compact_output(raw_text, max_lines=40)])
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
    closure_log = log_dir / "03_markov_mdp_buffer_check.txt"
    generation_log = log_dir / "03_markov_mdp_z3_generation.txt"
    cmd = [
        py,
        "-m",
        "clarity.certification.reconstruct",
        "--max-obs",
        "2",
        "--max-act",
        "4",
        "--dt",
        str(dt),
        *[str(model.path) for model in models],
    ]
    closure_run, closure_text = run_command(cmd, closure_log, out_dir)
    closure_rows = _parse_closure_text(closure_text, models)
    for row in closure_rows:
        row["dt"] = dt
    if closure_run["returncode"] != 0 or len(closure_rows) != len(models):
        raise RuntimeError(
            "Markov/MDP buffer extraction did not produce one result for every "
            f"SysML file; see {closure_log}"
        )
    _write_closure_log(closure_log, closure_run, closure_rows, closure_text)

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
    if generation_run["returncode"] != 0 or not generation_json.exists():
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
            "closure_run": closure_run,
            "generation_run": generation_run,
            "closure_rows": closure_rows,
            "generation_rows": rows,
            "certificate_policy": generation_summary.get("certificate_policy", ""),
        },
    )
    return {
        "closure_run": closure_run,
        "generation_run": generation_run,
        "closure_rows": closure_rows,
        "rows": rows,
        "fields": fields,
    }
