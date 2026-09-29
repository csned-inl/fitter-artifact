"""Human readable report rendering for a completed pipeline run."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from clarity.pipeline.support import markdown_table


def write_report(out_dir: Path, summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Controller Fitting Sequence Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("This report demonstrates the simplified controller fitting sequence:")
    lines.append("")
    lines.append("```text")
    lines.append("NeuralRequirement affine/rule fit")
    lines.append("  -> memoryless controller check")
    lines.append("    -> provable Markov/MDP controller check")
    lines.append("      -> discretization safety certification")
    if "reduced_training" in summary["stages"]:
        lines.append("        -> reduced feedforward training")
    lines.append("```")
    lines.append("")
    lines.append("The run overwrites generated outputs and rebuilds each selected stage")
    lines.append("from the SysML files supplied to this run.")
    lines.append("")

    affine = summary["stages"]["affine_rule"]
    lines.append("## 1. Affine/Rule Fit")
    lines.append("")
    lines.append("This stage reads each #NeuralRequirement directly from its SysML file.")
    lines.append("A Boolean requirement is evaluated only when it determines one action.")
    lines.append("")
    lines.append(markdown_table(affine["rows"], affine["fields"]))
    lines.append("")
    lines.append("Every row in this stage is generated fresh by this artifact.")
    lines.append("")
    lines.append("No learned parameters are used when the requirement itself gives the action.")
    lines.append("")

    weak = summary["stages"]["memoryless"]
    lines.append("## 2. Memoryless Controller Check")
    lines.append("")
    lines.append("Claim: the current controller inputs are enough for the current decision.")
    lines.append("This supports a memoryless controller, but it does not prove the full")
    lines.append("modeled process is Markov.")
    lines.append("")
    lines.append("Fit status: this stage does not fit weights. It provides a non-recurrent")
    lines.append("input form that can be used by a later feedforward fit.")
    lines.append("")
    lines.append(markdown_table(weak["rows"], weak["fields"]))
    lines.append("")
    lines.append(f"Command log: `{weak['run']['log']}`")
    lines.append("")

    strict = summary["stages"]["markov_mdp"]
    lines.append("## 3. Provable Markov/MDP Controller Check")
    lines.append("")
    lines.append("Claim: a finite buffer is enough to prove the next modeled step is")
    lines.append("determined. This stage generates the proof obligations from the supplied")
    lines.append("SysML files and calls Z3 during the run. It saves certificates,")
    lines.append("reduced-MDP specs, SMT-LIB queries, and proof/counterexample transcripts.")
    lines.append("")
    lines.append("Fit status: this stage does not fit weights. It provides a certified")
    lines.append("augmented state for later feedforward, tabular, or analytical fitting.")
    lines.append("")
    lines.append(markdown_table(strict["rows"], strict["fields"]))
    lines.append("")
    lines.append(f"Reconstructibility log: `{strict['closure_run']['log']}`")
    lines.append(f"Z3 generation log: `{strict['generation_run']['log']}`")
    lines.append("")

    discretization = summary["stages"]["discretization_safety"]
    lines.append("## 4. Discretization Safety Certification")
    lines.append("")
    lines.append("Claim: the checked controller contract implies every extracted safety")
    lines.append("property throughout each interval between controller updates.")
    lines.append("The stage consumes the Stage 3 certificate and the run-wide dt value.")
    lines.append("")
    lines.append(markdown_table(discretization["rows"], discretization["fields"]))
    lines.append("")
    lines.append(f"Certificate log: `{discretization['run']['log']}`")
    lines.append("")

    training = summary["stages"].get("reduced_training")
    if training is not None:
        lines.append("## 5. Reduced Feedforward Training")
        lines.append("")
        lines.append("Claim: the run-local certified specs can be consumed by non-recurrent")
        lines.append("feedforward trainers. For each model, the SysML requirement determines")
        lines.append("one hidden size from its comparison boundaries and outputs.")
        lines.append("The models use the handmade NumPy PPO stack.")
        lines.append("All training runs on CPU and checks actions against the requirement")
        lines.append("read from the current SysML file.")
        lines.append("")
        lines.append("Selection rule: train and evaluate the sole SysML-derived size.")
        lines.append("")
        lines.append("### Selected Models")
        lines.append("")
        lines.append(markdown_table(training["selected_rows"], training["selected_fields"]))
        lines.append("")
        lines.append("### All Training Runs")
        lines.append("")
        lines.append(markdown_table(training["rows"], training["fields"]))
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append("- Stage 1 is the cheapest: no learned parameters when the NeuralRequirement")
    lines.append("  already defines the controller.")
    lines.append("- Stage 2 removes controller recurrence for the current decision.")
    lines.append("- Stage 3 is the stronger provable Markov/MDP claim.")
    lines.append("- Stage 4 certifies safety throughout each discretized interval.")
    if training is not None:
        lines.append("- Stage 5 trains small feedforward policies from the Stage 3 specs.")
        lines.append("")
        lines.append("Training and evaluation check each action against the requirement")
        lines.append("read from the current SysML file.")
    lines.append("")
    (out_dir / "fitting_sequence_report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
