#!/usr/bin/env python3
"""Build a bounded, sanitized summary of workstation validation checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path


SCHEMA = "clarity.finite-history-workstation-gates"
VERSION = 2
MAX_DIAGNOSTIC_LINES = 80
MAX_DIAGNOSTIC_CHARS = 8_000

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_<])(?:[A-Za-z]:\\|/)(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?:password|secret|token|credential|authorization|api[_-]?key)\s*[:=]"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitize_diagnostic(text: str, *, repo_root: Path, home: Path) -> str:
    """Remove local paths and likely secret assignments from a bounded tail."""

    value = _ANSI.sub("", text.replace(str(repo_root), "<repo>").replace(str(home), "<home>"))
    lines = []
    for line in value.splitlines():
        if _SENSITIVE_ASSIGNMENT.search(line):
            lines.append("[redacted sensitive diagnostic line]")
        else:
            lines.append(_ABSOLUTE_PATH.sub("<absolute-path>", line))
    bounded = "\n".join(lines[-MAX_DIAGNOSTIC_LINES:]).strip()
    if len(bounded) > MAX_DIAGNOSTIC_CHARS:
        bounded = "[diagnostic tail truncated]\n" + bounded[-MAX_DIAGNOSTIC_CHARS:]
    return bounded


def _classification(exit_status: int) -> str:
    if exit_status == 0:
        return "passed"
    if exit_status in {124, 137}:
        return "timeout"
    return "failed"


def _read_index(index_path: Path) -> list[dict[str, object]]:
    rows = []
    for number, line in enumerate(index_path.read_text().splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"malformed check index row {number}")
        name, source_path, exit_text, duration_text = fields
        rows.append({
            "name": name,
            "source_path": source_path,
            "exit_status": int(exit_text),
            "duration_ms": int(duration_text),
        })
    return rows


def build_report(
    index_path: Path,
    result_dir: Path,
    repo_root: Path,
    *,
    z3_version: str | None = None,
    backend_present: bool | None = None,
) -> dict[str, object]:
    """Construct the machine-readable report from one gate run."""

    if z3_version is None:
        try:
            import z3
            z3_version = z3.get_version_string()
        except Exception:
            z3_version = None
    if backend_present is None:
        backend_present = importlib.util.find_spec(
            "clarity.certification.markov_z3"
        ) is not None

    checks = []
    home = Path.home()
    for row in _read_index(index_path):
        name = str(row["name"])
        stdout_path = result_dir / f"{name}.out"
        stderr_path = result_dir / f"{name}.err"
        exit_status = int(row["exit_status"])
        record: dict[str, object] = {
            **row,
            "status": _classification(exit_status),
            "stdout_sha256": _sha256(stdout_path),
            "stderr_sha256": _sha256(stderr_path),
        }
        if exit_status != 0:
            stderr = stderr_path.read_text(errors="replace")
            stdout = stdout_path.read_text(errors="replace")
            record["diagnostic"] = sanitize_diagnostic(
                stderr if stderr.strip() else stdout,
                repo_root=repo_root,
                home=home,
            )
        checks.append(record)

    failures = [check for check in checks if check["status"] != "passed"]
    z3_checks = [
        check for check in checks
        if str(check["name"]).startswith("validate_markov_z3_")
    ]
    if z3_version is None:
        z3_fixture_status = "not_run_z3_unavailable"
    elif any(check["status"] != "passed" for check in z3_checks):
        z3_fixture_status = "failed"
    else:
        z3_fixture_status = "passed"

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "implemented_gate_status": "failed" if failures else "passed",
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failures),
        "failed_check_count": len(failures),
        "timeout_check_count": sum(check["status"] == "timeout" for check in checks),
        "test_results": checks,
        "z3_python_version": z3_version,
        "markov_z3_module_present": bool(backend_present),
        "z3_fixture_status": z3_fixture_status,
        "solver_proof_status": (
            "not_run_production_obligation_lowering_not_implemented"
            if backend_present else "not_run_backend_not_implemented"
        ),
        "certificate_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.index, args.result_dir, args.repo_root)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
