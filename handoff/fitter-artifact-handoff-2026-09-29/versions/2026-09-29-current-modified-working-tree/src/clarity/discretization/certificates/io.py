"""Canonical serialization and storage for discretization certificates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 5
CERTIFICATE_KIND = "discretization_safety_certificate_v5"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def certificate_hash(certificate: dict[str, Any]) -> str:
    clone = json.loads(json.dumps(certificate))
    clone.pop("self_sha256", None)
    return sha256_bytes(canonical_json_bytes(clone))


def write_certificate(certificate: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    clone = json.loads(json.dumps(certificate))
    clone["self_sha256"] = certificate_hash(clone)
    output.write_bytes(canonical_json_bytes(clone) + b"\n")


def load_certificate(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
