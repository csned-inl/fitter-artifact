"""Shared file, process, and report helpers for pipeline stages."""

from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from clarity.sysml.inputs import SysMLInput


SOURCE_ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    lines = [
        "| " + " | ".join(fieldnames) + " |",
        "| " + " | ".join("---" for _ in fieldnames) + " |",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(format_value(row.get(key, "")) for key in fieldnames) + " |"
        )
    return "\n".join(lines)


def compact_output(text: str, max_lines: int = 80) -> str:
    lines = [
        line
        for line in text.splitlines()
        if "ANTLR runtime and generated code versions disagree" not in line
    ]
    if len(lines) <= max_lines:
        return "\n".join(lines) + ("\n" if lines else "")
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2 :]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... omitted {omitted} lines ..."] + tail) + "\n"


def run_command(
    cmd: list[str], log_path: Path, out_dir: Path
) -> tuple[dict[str, Any], str]:
    env = dict(os.environ)
    env.setdefault("CUDA_VISIBLE_DEVICES", "")
    process = subprocess.run(
        cmd,
        cwd=SOURCE_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path.write_text(compact_output(process.stdout), encoding="utf-8")
    return {
        "command": shlex.join(cmd),
        "returncode": process.returncode,
        "log": str(log_path.relative_to(out_dir)),
        "captured_stdout_bytes": len(process.stdout.encode("utf-8")),
    }, process.stdout


def model_order(models: list[SysMLInput]) -> dict[str, int]:
    return {model.key: index for index, model in enumerate(models)}


def write_command_summary_log(
    log_path: Path,
    run: dict[str, Any],
    summary_lines: list[str],
    raw_text: str,
) -> None:
    lines = [
        f"command: {run['command']}",
        f"returncode: {run['returncode']}",
        "",
        *summary_lines,
    ]
    if run["returncode"] != 0:
        lines.extend(["", "failure tail:", compact_output(raw_text, max_lines=40)])
    log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
