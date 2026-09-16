#!/usr/bin/env python3
"""Coordinate the complete CLARITY controller fitting pipeline."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from clarity.models import models_root
from clarity.pipeline.report import write_report
from clarity.pipeline.stages import (
    run_affine_stage,
    run_discretization_stage,
    run_markov_stage,
    run_memoryless_stage,
    run_training_stage,
)
from clarity.pipeline.support import write_json
from clarity.sysml.inputs import SysMLInput, discover_sysml
from clarity.sysml.runtime_settings import DEFAULT_DT, validate_dt


def run_pipeline(
    *,
    models: list[SysMLInput],
    out_dir: Path,
    python_bin: str,
    dt: float,
    dt_text: str,
    preprocessing_only: bool,
    smoke_training: bool,
    training_jobs: int | None,
    override_tolerance: float,
    collection_backend: str,
    collection_workers_per_job: int | None,
    collection_start_method: str,
    discretization_optimization_timeout_ms: int,
    discretization_smt_timeout_ms: int,
) -> dict[str, Any]:
    """Run each selected stage in order and write the complete summary."""
    if out_dir.exists():
        raise FileExistsError(f"fresh artifact directory required: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python_bin": python_bin,
        "settings": {"dt": dt, "dt_input": dt_text},
        "sysml_inputs": [model.to_dict() for model in models],
        "stages": {},
    }
    write_json(out_dir / "sysml_inputs.json", summary["sysml_inputs"])
    try:
        summary["stages"]["affine_rule"] = run_affine_stage(
            out_dir, python_bin, models, dt
        )
        summary["stages"]["memoryless"] = run_memoryless_stage(out_dir, models)
        summary["stages"]["markov_mdp"] = run_markov_stage(
            out_dir, python_bin, models, dt
        )
        if summary["stages"]["markov_mdp"]["result"] != "PASS":
            raise RuntimeError("Stage 3 did not certify every source model; see saved per-model evidence")
        summary["stages"]["discretization_safety"] = run_discretization_stage(
            out_dir,
            python_bin,
            models,
            dt,
            dt_text=dt_text,
            optimization_timeout_ms=discretization_optimization_timeout_ms,
            smt_timeout_ms=discretization_smt_timeout_ms,
        )
        if not preprocessing_only:
            summary["stages"]["reduced_training"] = run_training_stage(
                out_dir,
                python_bin,
                models,
                dt=dt,
                smoke=smoke_training,
                jobs=training_jobs,
                override_tolerance=override_tolerance,
                collection_backend=collection_backend,
                collection_workers_per_job=collection_workers_per_job,
                collection_start_method=collection_start_method,
            )

    except Exception as exc:
        summary["result"] = "FAILED"
        summary["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        summary["unexecuted_stages"] = [name for name in
            ('affine_rule', 'memoryless', 'markov_mdp', 'discretization_safety', 'reduced_training')
            if name not in summary['stages']]
        write_json(out_dir / "fitting_sequence_summary.json", summary)
        write_report(out_dir, summary)
        raise

    write_json(out_dir / "fitting_sequence_summary.json", summary)
    write_report(out_dir, summary)
    return summary


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*", help="SysML file paths")
    parser.add_argument(
        "--models-root",
        default=str(models_root()),
        help="directory searched recursively when no file paths are supplied",
    )
    parser.add_argument("--out-dir", default=str(Path.cwd() / "outputs" / "latest"))
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument(
        "--dt",
        default=str(DEFAULT_DT),
        help=f"simulation time step used by every applicable stage; default={DEFAULT_DT}",
    )
    parser.add_argument(
        "--smoke-training",
        action="store_true",
        help="run a short Stage 5 wiring test instead of full training",
    )
    parser.add_argument(
        "--training-jobs",
        type=int,
        default=None,
        help="parallel Stage 5 training jobs; default=min(6,cpu_count)",
    )
    parser.add_argument(
        "--preprocessing-only",
        action="store_true",
        help="run Stages 1 through 4 and stop before training",
    )
    parser.add_argument(
        "--discretization-optimization-timeout-ms",
        type=int,
        default=250,
        help="solver timeout for the linear and convex checkers in Stage 4",
    )
    parser.add_argument(
        "--discretization-smt-timeout-ms",
        type=int,
        default=30000,
        help="solver timeout for the final fallback in Stage 4",
    )
    parser.add_argument(
        "--collection-backend",
        choices=("auto", "serial", "process"),
        default="auto",
        help="discrete episode collection backend",
    )
    parser.add_argument(
        "--collection-workers-per-job",
        type=int,
        default=None,
        help="simulator workers per discrete training job; default uses the CPU budget",
    )
    parser.add_argument(
        "--collection-start-method",
        default="spawn",
        help="multiprocessing start method for discrete episode workers",
    )
    parser.add_argument(
        "--override-tolerance",
        type=float,
        default=0.01,
        help="absolute override slack for selecting a smaller max-accuracy model",
    )
    return parser


def main() -> int:
    parser = _argument_parser()
    args = parser.parse_args()
    dt_text = str(args.dt)
    args.dt = validate_dt(args.dt)
    if args.discretization_smt_timeout_ms <= 0:
        parser.error("--discretization-smt-timeout-ms must be positive")
    if args.discretization_optimization_timeout_ms <= 0:
        parser.error("--discretization-optimization-timeout-ms must be positive")
    models = discover_sysml(args.models, models_root=args.models_root)
    unsupported = [model.path for model in models if model.action_kind != "discrete"]
    if unsupported:
        parser.error(
            "continuous-action controllers are archived and unsupported by the "
            "active pipeline: " + ", ".join(map(str, unsupported))
        )

    out_dir = Path(args.out_dir).resolve()
    run_pipeline(
        models=models,
        out_dir=out_dir,
        python_bin=args.python_bin,
        dt=args.dt,
        dt_text=dt_text,
        preprocessing_only=args.preprocessing_only,
        smoke_training=args.smoke_training,
        training_jobs=args.training_jobs,
        override_tolerance=args.override_tolerance,
        collection_backend=args.collection_backend,
        collection_workers_per_job=args.collection_workers_per_job,
        collection_start_method=args.collection_start_method,
        discretization_optimization_timeout_ms=(
            args.discretization_optimization_timeout_ms
        ),
        discretization_smt_timeout_ms=args.discretization_smt_timeout_ms,
    )
    print(f"WROTE {out_dir / 'fitting_sequence_report.md'}")
    print(f"WROTE {out_dir / 'fitting_sequence_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
