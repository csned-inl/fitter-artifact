"""Normalize checker results and construct recorded progression stages."""

from __future__ import annotations

from typing import Any


def stage_record(
    checker: str,
    outcome: str,
    *,
    reason_code: str = "",
    detail: str = "",
    applicability_checks: dict[str, Any] | None = None,
    proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "checker": checker,
        "checker_version": 1,
        "outcome": outcome,
        "reason_code": reason_code,
        "detail": detail,
        "applicability_checks": applicability_checks or {},
        "proof": proof or {},
    }


def attempt_stage(checker: str, attempt: dict[str, Any]) -> dict[str, Any]:
    return stage_record(
        checker,
        str(attempt.get("outcome", "DEFERRED")),
        reason_code=str(attempt.get("reason_code", "")),
        detail=str(attempt.get("detail", "")),
        applicability_checks=attempt.get("applicability_checks") or {},
        proof=attempt.get("proof") or {},
    )


def validated_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    if attempt.get("outcome") in {"CERTIFIED", "VIOLATION", "DEFERRED"}:
        return attempt
    return {
        "outcome": "DEFERRED",
        "reason_code": "MALFORMED_OUTPUT",
        "detail": "checker returned an invalid outcome",
        "applicability_checks": attempt.get("applicability_checks") or {},
    }
