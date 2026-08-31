"""Stage 5, reduced feedforward controller training."""

from __future__ import annotations

import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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


def _spec_path(out_dir: Path, name: str) -> Path:
    return (
        out_dir
        / "03_markov_mdp"
        / "reduced_mdp_specs"
        / f"{name}.reduced_mdp_spec.json"
    )


def _feedforward_architecture_from_spec(
    spec_path: Path,
    *,
    dt: float,
) -> dict[str, Any]:
    spec = read_json(spec_path)
    spec_dt = validate_dt(spec.get("model", {}).get("dt"))
    if spec_dt != dt:
        raise RuntimeError(
            f"reduced-MDP spec dt does not match run dt: {spec_dt} != {dt}: "
            f"{spec_path}"
        )
    architecture = spec.get("feedforward_architecture")
    if not isinstance(architecture, dict):
        raise RuntimeError(
            f"reduced-MDP spec lacks a feedforward architecture: {spec_path}"
        )
    hidden_dim = architecture.get("hidden_dim")
    if not isinstance(hidden_dim, int) or hidden_dim <= 0:
        raise RuntimeError(f"reduced-MDP spec has an invalid hidden size: {spec_path}")
    return architecture


def _read_training_summary(path: Path) -> dict[str, Any]:
    data = read_json(path)
    test = data.get("test", {})
    selected = data.get("selected_checkpoint", {})
    buffer = data.get("certified_buffer", {})
    return {
        "dt": data.get("dt", data.get("config", {}).get("dt", "")),
        "hidden_dim": data.get("hidden_dim", ""),
        "params": data.get("parameter_count", ""),
        "b_obs": buffer.get("b_obs", ""),
        "b_act": buffer.get("b_act", ""),
        "device": data.get("device", ""),
        "shield": data.get("shield_type", ""),
        "policy": data.get("policy_type", ""),
        "train_seconds": data.get("train_seconds", ""),
        "peak_rss_mb": data.get("peak_rss_mb", ""),
        "selected_success": selected.get("success_rate", selected.get("acc", "")),
        "selected_override": selected.get(
            "override_rate", selected.get("override", "")
        ),
        "test_safety": test.get("safety_violation_rate", ""),
        "test_success": test.get("success_rate", ""),
        "test_override": test.get("pooled_override_rate", ""),
        "summary_json": "",
        "weights": "",
    }


def _float_or_none(value: Any) -> float | None:
    try:
        if value == "" or value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_training_job(
    job: dict[str, Any], py: str, out_dir: Path
) -> dict[str, Any]:
    name = job["model"]
    hidden_dim = int(job["hidden_dim"])
    run_dir = job["run_dir"]
    log_path = job["log_path"]
    cmd = [
        py,
        "-m",
        "clarity.training.reduced.train_one_seed",
        str(job["model_path"]),
        "--out-dir",
        str(run_dir),
        "--seed",
        str(job["seed"]),
        "--max-obs",
        "2",
        "--max-act",
        "4",
        "--dt",
        str(job["dt"]),
        "--reduced-mdp-spec",
        str(job["spec_path"]),
        "--collection-backend",
        str(job["collection_backend"]),
        "--collection-workers",
        str(job["collection_workers"]),
        "--collection-start-method",
        str(job["collection_start_method"]),
        *job.get("overrides", []),
    ]
    weight_path = run_dir / "best.npz"
    started = time.time()
    run, raw_text = run_command(cmd, log_path, out_dir)
    seconds = time.time() - started
    write_command_summary_log(
        log_path,
        run,
        [
            f"{name} reduced training:",
            f"- dt={job['dt']}",
            f"- elapsed_seconds={seconds:.6f}",
        ],
        raw_text,
    )
    summary_json = run_dir / "summary.json"
    if run["returncode"] != 0 or not summary_json.exists():
        return {
            "model": name,
            "kind": "discrete",
            "status": "failed",
            "dt": job["dt"],
            "hidden_dim": hidden_dim,
            "seconds": seconds,
            "command_log": str(log_path.relative_to(out_dir)),
            "summary_json": "",
            "weights": "",
            "returncode": run["returncode"],
        }
    row = _read_training_summary(summary_json)
    trained_dt = validate_dt(row.get("dt"))
    if trained_dt != job["dt"]:
        raise RuntimeError(
            f"{name} training result dt does not match run dt: "
            f"{trained_dt} != {job['dt']}"
        )
    row.update(
        {
            "model": name,
            "kind": "discrete",
            "status": "trained",
            "seconds": seconds,
            "summary_json": str(summary_json.relative_to(out_dir)),
            "weights": str(weight_path.relative_to(out_dir)),
            "command_log": str(log_path.relative_to(out_dir)),
            "returncode": run["returncode"],
        }
    )
    return row


def _select_models(
    rows: list[dict[str, Any]],
    *,
    override_tolerance: float,
    models: list[SysMLInput],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    order = model_order(models)
    for model in sorted({row["model"] for row in rows}, key=order.__getitem__):
        candidates = [
            row
            for row in rows
            if row["model"] == model
            and row.get("status") == "trained"
            and _float_or_none(row.get("test_safety")) == 0.0
        ]
        if not candidates:
            selected.append(
                {"model": model, "selection_status": "no_safe_trained_candidate"}
            )
            continue
        best_acc = max(
            _float_or_none(row.get("test_success")) or 0.0 for row in candidates
        )
        best_acc_rows = [
            row
            for row in candidates
            if (_float_or_none(row.get("test_success")) or 0.0) == best_acc
        ]
        best_override = min(
            _float_or_none(row.get("test_override")) or float("inf")
            for row in best_acc_rows
        )
        eligible = [
            row
            for row in best_acc_rows
            if (_float_or_none(row.get("test_override")) or float("inf"))
            <= best_override + override_tolerance
        ]
        choice = sorted(
            eligible,
            key=lambda row: (
                int(row.get("params") or 10**18),
                int(row.get("hidden_dim") or 10**18),
                _float_or_none(row.get("test_override")) or float("inf"),
            ),
        )[0]
        selected.append(
            {
                "model": model,
                "selection_status": "selected",
                "selection_method": "sysml_derived_single_architecture",
                "hidden_dim": choice.get("hidden_dim", ""),
                "params": choice.get("params", ""),
                "test_safety": choice.get("test_safety", ""),
                "test_success": choice.get("test_success", ""),
                "test_override": choice.get("test_override", ""),
                "best_success": best_acc,
                "best_override_at_best_success": best_override,
                "override_tolerance": override_tolerance,
                "summary_json": choice.get("summary_json", ""),
                "weights": choice.get("weights", ""),
            }
        )
    return selected


def run_training_stage(
    out_dir: Path,
    py: str,
    models: list[SysMLInput],
    *,
    dt: float,
    smoke: bool = False,
    jobs: int | None = None,
    override_tolerance: float = 0.01,
    collection_backend: str = "auto",
    collection_workers_per_job: int | None = None,
    collection_start_method: str = "spawn",
) -> dict[str, Any]:
    stage_dir = out_dir / "05_reduced_training"
    log_dir = out_dir / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    job_count = max(1, jobs if jobs is not None else min(6, os.cpu_count() or 1))
    discrete_overrides: list[str] = []
    if smoke:
        discrete_overrides = [
            "--oracle-samples", "96",
            "--oracle-epochs", "4",
            "--oracle-batch-size", "64",
            "--ensure-class-coverage", "2",
            "--ppo-episodes", "16",
            "--episodes-per-update", "4",
            "--eval-interval", "4",
            "--eval-episodes", "4",
            "--test-episodes", "4",
            "--n-ppo-epochs", "1",
            "--minibatch-size", "4",
            "--max-steps", "300",
        ]
        job_count = min(job_count, 4)

    cpu_count = max(1, os.cpu_count() or 1)
    if collection_workers_per_job is None:
        collection_workers_per_job = max(1, min(8, cpu_count // max(job_count, 1)))
    else:
        collection_workers_per_job = max(1, collection_workers_per_job)
    if collection_backend == "serial":
        collection_workers_per_job = 1

    training_jobs: list[dict[str, Any]] = []
    architectures: dict[str, dict[str, Any]] = {}
    for model in models:
        name = model.key
        spec_path = _spec_path(out_dir, name)
        if not spec_path.exists():
            raise RuntimeError(
                f"current run did not generate a reduced-MDP spec for {name}"
            )
        architecture = _feedforward_architecture_from_spec(spec_path, dt=dt)
        architectures[name] = architecture
        hidden_dim = int(architecture["hidden_dim"])
        run_dir = stage_dir / "runs" / name / f"h{hidden_dim}" / "seed_0"
        training_jobs.append(
            {
                "model": name,
                "model_path": model.path,
                "kind": "discrete",
                "dt": dt,
                "hidden_dim": hidden_dim,
                "seed": 0,
                "run_dir": run_dir,
                "log_path": log_dir / f"05_train_{name}_h{hidden_dim}.txt",
                "spec_path": spec_path,
                "overrides": discrete_overrides,
                "collection_backend": collection_backend,
                "collection_workers": collection_workers_per_job,
                "collection_start_method": collection_start_method,
            }
        )

    manifest = {
        "dt": dt,
        "mode": "smoke" if smoke else "full",
        "architecture_selection": "SysML-derived; one architecture per model",
        "architectures": architectures,
        "seed": 0,
        "jobs": job_count,
        "collection_backend": collection_backend,
        "collection_workers_per_discrete_job": collection_workers_per_job,
        "collection_start_method": collection_start_method,
        "discrete_training_defaults": (
            "train_one_seed defaults: 2000 PPO episodes, 100 episodes per update, "
            "100-episode eval/checkpoint interval, 100 eval episodes per checkpoint, "
            "200 final test episodes, 2000 oracle samples, 100 oracle epochs"
            if not smoke
            else "smoke overrides"
        ),
        "selection_rule": (
            "each model uses the sole SysML-derived architecture; a trained model is "
            "selected only from a zero-violation checkpoint, using test success and "
            "then override rate"
        ),
    }
    write_json(stage_dir / "training_manifest.json", manifest)

    runs: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=job_count) as pool:
        pending = {
            pool.submit(_run_training_job, job, py, out_dir): job
            for job in training_jobs
        }
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                job = pending.pop(future)
                row = future.result()
                rows.append(row)
                runs.append(
                    {
                        "model": job["model"],
                        "hidden_dim": job["hidden_dim"],
                        "status": row.get("status"),
                        "command_log": row.get("command_log", ""),
                        "returncode": row.get("returncode", ""),
                    }
                )
                write_csv(
                    stage_dir / "all_runs.csv",
                    rows,
                    [
                        "model", "kind", "status", "dt", "hidden_dim", "params",
                        "b_obs", "b_act", "device", "shield", "policy",
                        "train_seconds", "peak_rss_mb", "test_safety",
                        "test_success", "test_override", "summary_json",
                        "weights", "command_log",
                    ],
                )

    order = model_order(models)
    rows = sorted(rows, key=lambda row: order[row["model"]])
    failed = [row for row in rows if row.get("status") != "trained"]
    if failed:
        details = ", ".join(
            f"{row['model']} ({row.get('command_log', 'no log')})" for row in failed
        )
        raise RuntimeError(f"reduced training failed: {details}")
    selected_rows = _select_models(
        rows, override_tolerance=override_tolerance, models=models
    )
    fields = [
        "model", "kind", "status", "dt", "hidden_dim", "params", "b_obs",
        "b_act", "device", "shield", "policy", "test_safety", "test_success",
        "test_override", "summary_json", "weights", "command_log",
    ]
    write_csv(stage_dir / "summary.csv", rows, fields)
    selected_fields = [
        "model", "selection_status", "selection_method", "hidden_dim", "params",
        "test_safety", "test_success", "test_override", "best_success",
        "best_override_at_best_success", "override_tolerance", "summary_json", "weights",
    ]
    write_csv(stage_dir / "selected_models.csv", selected_rows, selected_fields)
    write_json(
        stage_dir / "summary.json",
        {
            "manifest": manifest,
            "runs": runs,
            "rows": rows,
            "selected_models": selected_rows,
        },
    )
    return {
        "runs": runs,
        "rows": rows,
        "fields": fields,
        "selected_rows": selected_rows,
        "selected_fields": selected_fields,
        "manifest": manifest,
    }
