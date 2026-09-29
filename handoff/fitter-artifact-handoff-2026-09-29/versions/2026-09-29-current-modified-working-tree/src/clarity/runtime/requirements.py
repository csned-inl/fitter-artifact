"""Lossless per-boundary accounting of the original source requirements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequirementEvent:
    episode_id: int
    sequence: int
    boundary: str
    source: str
    engine_time: float
    statuses: dict[str, dict[str, Any]]


class RequirementLedger:
    """Owned by the simulator producer; batches are transferred only at a pause."""

    def __init__(self, episode_id=0):
        self.episode_id = episode_id
        self.sequence = 0
        self._pending = []

    def record(self, engine, boundary, source=""):
        from clarity.sysml.simulator import ExpressionEvaluator
        statuses = {}
        for req in engine.parser.parsed_requirements:
            kinds = sorted(set(req.metadata) & {"Prohibition", "Obligation"})
            if not kinds:
                continue
            entry = {"kind": kinds[0], "metadata": list(req.metadata),
                     "status": None, "error": None}
            try:
                value = ExpressionEvaluator(
                    engine.state, req.context, engine.parser.ref_bindings,
                    engine.parser.system_part, strict=True,
                ).evaluate(req.expression)
                if type(value) is not bool:
                    raise ValueError("source requirement result is not Boolean")
                entry["status"] = value
            except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
                entry["error"] = str(exc)
            statuses[req.name] = entry
        event = RequirementEvent(self.episode_id, self.sequence, boundary, source,
                                 engine.time, statuses)
        self.sequence += 1
        self._pending.append(event)
        return event

    def drain(self):
        events = tuple(self._pending)
        self._pending.clear()
        return events


def summarize_events(events):
    """A later true observation never overwrites an earlier false or error."""
    result = {}
    for event in events:
        for name, entry in event.statuses.items():
            row = result.setdefault(name, {"kind": entry["kind"], "status": True,
                                          "checks": 0, "errors": []})
            row["checks"] += 1
            if entry["status"] is False:
                row["status"] = False
            if entry["error"] is not None:
                row["errors"].append({"event": event.sequence, "error": entry["error"]})
    return result


@dataclass
class ResetResult:
    observation: Any = None
    outcome: str = "decision"
    events: tuple[RequirementEvent, ...] = ()
    error: str | None = None

    @property
    def statuses(self):
        return summarize_events(self.events)

    @property
    def violations(self):
        return [name for name, row in self.statuses.items() if row["status"] is False]

    @property
    def errors(self):
        return {name: row["errors"] for name, row in self.statuses.items() if row["errors"]}


class ResetUnavailable(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(result.error or f"reset ended with {result.outcome}")
