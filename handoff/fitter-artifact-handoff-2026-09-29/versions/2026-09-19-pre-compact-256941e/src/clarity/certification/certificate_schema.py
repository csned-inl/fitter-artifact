"""Shared schema identifiers and storage for Markov certificates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 4
PROOF_PROFILE = "strict_q_syntactic_reconstructibility_v1"
PROFILE_MDP_THEOREM = "strict_q_profile_mdp_v1"
SOLVER_BACKED_MDP_THEOREM = "strict_q_solver_backed_mdp_v1"
PROFILE_OBLIGATIONS_DISCHARGED = (
    "profile_obligations_discharged_solver_gate_required"
)
BLOCKING_DIAGNOSTIC_SEVERITIES = {"warning", "error"}
BLOCKING_DIAGNOSTIC_CODES = {"legacy_dependency_fallback"}


def model_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fact_key(var: str, tau: int) -> str:
    return f"{var}@{tau}"


def write_certificate(certificate: dict[str, Any], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_certificate(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)
