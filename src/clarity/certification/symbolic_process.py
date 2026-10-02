"""Minimal model-independent data used by direct symbolic certificates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReconstructionRequirement:
    """Checked explanation of how one relevant component is recovered."""

    component: str
    source: str
    lag: int
    equation: str

    def __post_init__(self) -> None:
        allowed = {
            "current_observation", "prior_observation", "prior_action",
            "fixed_context", "overwritten_before_use", "proved_irrelevant",
        }
        if self.source not in allowed:
            raise ValueError(f"unsupported reconstruction source {self.source}")
        if self.lag < 0:
            raise ValueError("reconstruction lag must be nonnegative")
        if self.source.startswith("prior_") and self.lag < 1:
            raise ValueError("prior reconstruction requires a positive lag")
        if not self.source.startswith("prior_") and self.lag != 0:
            raise ValueError("non-history reconstruction must have lag zero")


@dataclass(frozen=True, slots=True)
class BufferCandidate:
    """Analytical history-depth proposal; never a certificate by itself."""

    b_obs: int
    b_act: int
    evidence: tuple[ReconstructionRequirement, ...]

    def __post_init__(self) -> None:
        if self.b_obs < 0 or self.b_act < 0:
            raise ValueError("buffer depths must be nonnegative")


def derive_buffer_candidate(
    requirements: tuple[ReconstructionRequirement, ...],
) -> BufferCandidate:
    """Take exact maximum required lags from a model-specific reconstruction proof."""

    if not requirements:
        raise ValueError("candidate derivation requires reconstruction evidence")
    b_obs = max((item.lag for item in requirements
                 if item.source == "prior_observation"), default=0)
    b_act = max((item.lag for item in requirements
                 if item.source == "prior_action"), default=0)
    return BufferCandidate(b_obs=b_obs, b_act=b_act, evidence=requirements)


@dataclass(frozen=True, slots=True)
class FixedProcessContext:
    """Named background conditions fixed for one certified process instance."""

    fields: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        names = self.names()
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("fixed process context field names must be nonempty strings")
        if len(names) != len(set(names)):
            raise ValueError("fixed process context requires unique named fields")

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _value in self.fields)

    def __getitem__(self, name: str) -> Any:
        for field_name, value in self.fields:
            if field_name == name:
                return value
        raise KeyError(name)


__all__ = [
    "BufferCandidate",
    "FixedProcessContext",
    "ReconstructionRequirement",
    "derive_buffer_candidate",
]
