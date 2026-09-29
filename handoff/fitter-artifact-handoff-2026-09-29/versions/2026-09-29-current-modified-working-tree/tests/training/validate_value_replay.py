"""Replay every saved test episode at initialization, decisions and cycle ends.

This evaluates existing weights. It does not authorize a certificate or training.
The saved episode count, seeds, dimensions, buffers, and dt are preserved.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from clarity.models import models_root
from clarity.runtime.oracle import extract_interface
from clarity.sysml.simulator import SimulationEngine, ExpressionEvaluator, resolve_value
from clarity.training.recurrent.io import load_policy
from clarity.training.reduced.policy import MLPActorCritic
from clarity.training.reduced.composite import ProgramShieldComposite
from clarity.training.reduced.buffered_env import BufferedDiscreteEnv
from clarity.training.reduced.collection import make_episode_jobs
from clarity.training.reduced.episode import collect_episode


def episode_fingerprint(episode):
    """Compare behavior exactly; execution timing measurements are excluded."""
    digest = hashlib.sha256()
    for name in ("obs", "actions", "rewards", "values", "log_probs", "dones", "overrides"):
        value = np.asarray(getattr(episode, name))
        digest.update(name.encode())
        digest.update(str((value.shape, value.dtype)).encode())
        digest.update(value.tobytes())
    digest.update(json.dumps({"outcome": episode.outcome, "violations": episode.violations,
                              "checks": episode.requirement_checks, "errors": episode.evaluation_errors}, sort_keys=True).encode())
    return digest.hexdigest()


def replay(saved_root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    original_record = SimulationEngine.record_requirements
    results = []
    for folder in sorted(saved_root.glob("*/h*/seed_0")):
        saved = json.loads((folder / "summary.json").read_text())
        path = Path(models_root()) / Path(saved["model_path"]).parent.name / "model.sysml"
        label = folder.parent.parent.name
        counts = collections.Counter()
        boundaries = collections.Counter()
        failed = {"reset": set(), "episode": set()}
        phase = {"enabled": False, "episode": -1, "phase": "construction"}
        rows = []
        with gzip.open(output / f"{label}-requirements.jsonl.gz", "wt") as ledger:
            def inspect(engine, boundary):
                if not phase["enabled"]:
                    return
                statuses, errors = {}, {}
                for requirement in engine.parser.parsed_requirements:
                    if not {"Prohibition", "Obligation"}.intersection(requirement.metadata):
                        continue
                    try:
                        value = ExpressionEvaluator(engine.state, requirement.context,
                            engine.parser.ref_bindings, engine.parser.system_part,
                            strict=True).evaluate(requirement.expression)
                        if type(value) is not bool:
                            raise ValueError("non-Boolean source requirement")
                        statuses[requirement.name] = value
                        counts[f"{phase['phase']}/{boundary}/{requirement.name}/checks"] += 1
                        if not value:
                            failed[phase["phase"]].add(requirement.name)
                            counts[f"{phase['phase']}/{boundary}/{requirement.name}/false"] += 1
                    except Exception as exc:
                        errors[requirement.name] = str(exc)
                        counts["evaluation_errors"] += 1
                values = {}
                for key in engine.state:
                    try:
                        value = resolve_value(engine.state, key)
                    except ValueError:
                        continue
                    if type(value) in (bool, int, float):
                        values[key] = value
                ledger.write(json.dumps({"episode": phase["episode"],
                    "phase": phase["phase"], "boundary": boundary,
                    "engine_time": engine.time, "requirements": statuses,
                    "evaluation_errors": errors, "state_values": values}) + "\n")

            def record(engine, boundary, source=""):
                if boundary not in {'initialization', 'decision', 'cycle_end'}:
                    raise AssertionError(f'unexpected internal requirement check: {boundary}')
                if phase['enabled']:
                    boundaries[boundary] += 1
                original_record(engine, boundary, source)
                inspect(engine, boundary + (":" + source if source else ""))

            SimulationEngine.record_requirements = record
            interface = extract_interface(str(path))
            policy = MLPActorCritic(saved["obs_dim"], saved["n_actions"],
                                   saved["hidden_dim"], seed=saved["seed"])
            load_policy(policy, folder / "best.npz")
            composite = ProgramShieldComposite(policy, interface["spec_shield"], interface["obs_names"])
            env = BufferedDiscreteEnv(str(path), dt=saved["dt"], max_steps=5000, phase=2,
                n_obs=saved["policy_buffer"]["n_obs"], n_act=saved["policy_buffer"]["n_act"])
            original_reset = env.reset_with_result
            def reset(seed=None):
                phase["phase"] = "reset"
                result = original_reset(seed=seed)
                phase["phase"] = "episode"
                return result
            env.reset_with_result = reset
            try:
                for job in make_episode_jobs(saved["config"]["test_episodes"],
                                             seed_base=20000 + saved["seed"]):
                    phase["enabled"] = True
                    phase["episode"] = job.index
                    for names in failed.values():
                        names.clear()
                    episode = collect_episode(env, composite,
                        np.random.default_rng(job.action_seed), greedy=True, reset_seed=job.env_seed)
                    rows.append({"index": job.index, "seed": job.env_seed,
                        "action_seed": job.action_seed, "steps": len(episode.actions),
                        "reported_failures": episode.violations,
                        "outcome": episode.outcome, "errors": episode.evaluation_errors,
                        "observed_failures": sorted(set.union(*failed.values())),
                        "reset_failures": sorted(failed["reset"]),
                        "controlled_rollout_failures": sorted(failed["episode"]),
                        "behavior_sha256": episode_fingerprint(episode)})
            finally:
                phase["enabled"] = False
                env.close()
                SimulationEngine.record_requirements = original_record
        # Repeat the entire saved evaluation without instrumentation to establish
        # that reading the source requirements did not change the trajectories.
        comparison_env = BufferedDiscreteEnv(str(path), dt=saved["dt"], max_steps=5000, phase=2,
            n_obs=saved["policy_buffer"]["n_obs"], n_act=saved["policy_buffer"]["n_act"])
        mismatches = []
        try:
            for row, job in zip(rows, make_episode_jobs(saved["config"]["test_episodes"],
                                                       seed_base=20000 + saved["seed"]), strict=True):
                episode = collect_episode(comparison_env, composite,
                    np.random.default_rng(job.action_seed), greedy=True, reset_seed=job.env_seed)
                row["uninstrumented_behavior_sha256"] = episode_fingerprint(episode)
                if row["behavior_sha256"] != row["uninstrumented_behavior_sha256"]:
                    mismatches.append(job.index)
        finally:
            comparison_env.close()
        result = {"model": label, "episodes": len(rows), "dt": saved["dt"],
            "checkpoint_sha256": hashlib.sha256((folder / "best.npz").read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "episodes_with_reported_failures": sum(bool(r["reported_failures"]) for r in rows),
            "episodes_with_observed_failures": sum(bool(r["observed_failures"]) for r in rows),
            "episodes_with_reset_failures": sum(bool(r["reset_failures"]) for r in rows),
            "episodes_with_controlled_rollout_failures": sum(bool(r["controlled_rollout_failures"]) for r in rows),
            "instrumentation_behavior_mismatches": mismatches,
            "evaluation_errors": counts["evaluation_errors"],
            "episodes_with_errors": sum(bool(r["errors"]) for r in rows),
            "accounting_mismatches": [r["index"] for r in rows if r["reported_failures"] != r["observed_failures"]],
            "boundary_counts": dict(boundaries),
            "counts": dict(counts), "episodes_detail": rows}
        (output / f"{label}.json").write_text(json.dumps(result, indent=2) + "\n")
        results.append({k: v for k, v in result.items() if k not in {"counts", "episodes_detail"}})
        (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results[-1]), flush=True)
    if not results:
        raise ValueError("no saved evaluation configurations found")
    if any(r["instrumentation_behavior_mismatches"] or r["evaluation_errors"] or r["episodes_with_errors"] or r["accounting_mismatches"] for r in results):
        raise AssertionError("replay had evaluation errors or instrumentation changed behavior")
    if any(r["episodes_with_observed_failures"] for r in results):
        raise AssertionError("source safety requirements were false; see the complete replay ledger")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--saved-runs", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    replay(args.saved_runs, args.out_dir)
