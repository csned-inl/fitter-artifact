"""Content-address repeated certificate subtrees without changing proof meaning."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


RULE = "content_addressed_analysis_subtree_pool_v1"
REFERENCE_KEY = "shared_subtree_sha256"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def compact_analysis(
    analysis: dict[str, Any],
    *,
    minimum_bytes: int = 128,
) -> dict[str, Any]:
    """Replace profitable repeated containers with content-addressed references."""

    source = deepcopy(analysis)
    source.pop("shared_subtrees", None)
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}

    def count(value: Any) -> None:
        if not isinstance(value, (dict, list)):
            return
        encoded = _canonical_bytes(value)
        key = hashlib.sha256(encoded).hexdigest()
        counts[key] = counts.get(key, 0) + 1
        sizes[key] = len(encoded)
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            count(item)

    count(source)
    reference_size = len(_canonical_bytes({REFERENCE_KEY: "0" * 64}))
    candidates = {
        key
        for key, occurrences in counts.items()
        if occurrences > 1
        and sizes[key] >= minimum_bytes
        and occurrences * (sizes[key] - reference_size) > sizes[key] + 80
    }
    nodes: dict[str, Any] = {}
    reference_count = 0

    def replace(value: Any, *, root: bool = False) -> Any:
        nonlocal reference_count
        if not isinstance(value, (dict, list)):
            return value
        key = _digest(value)
        if not root and key in candidates:
            nodes.setdefault(key, deepcopy(value))
            reference_count += 1
            return {REFERENCE_KEY: key}
        if isinstance(value, dict):
            return {name: replace(item) for name, item in value.items()}
        return [replace(item) for item in value]

    compacted = replace(source, root=True)
    compacted["shared_subtrees"] = {
        "rule": RULE,
        "minimum_bytes": minimum_bytes,
        "expanded_sha256": _digest(source),
        "reference_count": reference_count,
        "nodes": {key: nodes[key] for key in sorted(nodes)},
    }
    return compacted


def expand_analysis(analysis: Any) -> tuple[Any, list[str]]:
    """Validate and expand the optional content-addressed subtree pool."""

    if not isinstance(analysis, dict):
        return analysis, ["recorded analysis is malformed"]
    record = analysis.get("shared_subtrees")
    if record is None:
        return analysis, []
    if not isinstance(record, dict) or record.get("rule") != RULE:
        return analysis, ["shared analysis subtree pool is malformed"]
    nodes = record.get("nodes")
    if not isinstance(nodes, dict):
        return analysis, ["shared analysis subtrees are malformed"]
    errors: list[str] = []
    for key, value in nodes.items():
        if not isinstance(key, str) or _digest(value) != key:
            errors.append("shared analysis subtree hash is invalid")

    reference_count = 0

    def expand(value: Any, active: set[str]) -> Any:
        nonlocal reference_count
        if isinstance(value, dict):
            if REFERENCE_KEY in value:
                if set(value) != {REFERENCE_KEY}:
                    errors.append("shared analysis subtree reference is malformed")
                    return value
                key = value.get(REFERENCE_KEY)
                if not isinstance(key, str) or key not in nodes:
                    errors.append("shared analysis subtree reference is missing")
                    return value
                if key in active:
                    errors.append("shared analysis subtree references form a cycle")
                    return value
                reference_count += 1
                return expand(deepcopy(nodes[key]), active | {key})
            return {name: expand(item, active) for name, item in value.items()}
        if isinstance(value, list):
            return [expand(item, active) for item in value]
        return value

    expanded = {
        name: expand(item, set())
        for name, item in analysis.items()
        if name != "shared_subtrees"
    }
    if record.get("reference_count") != reference_count:
        errors.append("shared analysis subtree reference count is invalid")
    if record.get("expanded_sha256") != _digest(expanded):
        errors.append("expanded analysis hash is invalid")
    return expanded, errors
