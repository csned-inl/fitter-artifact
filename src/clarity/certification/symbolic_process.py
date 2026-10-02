"""Minimal model-independent data used by direct symbolic certificates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


__all__ = ["FixedProcessContext"]
