#!/usr/bin/env python3
"""Public interface for Markov certificate generation and checking."""

from .certificate_checker import check_certificate
from .certificate_generation import (
    build_certificate_for_path,
    main,
    reconstruction_trace,
)
from .certificate_schema import (
    PROFILE_MDP_THEOREM,
    PROFILE_OBLIGATIONS_DISCHARGED,
    PROOF_PROFILE,
    SCHEMA_VERSION,
    SOLVER_BACKED_MDP_THEOREM,
    load_certificate,
    model_hash,
    write_certificate,
)

__all__ = [
    "PROFILE_MDP_THEOREM",
    "PROFILE_OBLIGATIONS_DISCHARGED",
    "PROOF_PROFILE",
    "SCHEMA_VERSION",
    "SOLVER_BACKED_MDP_THEOREM",
    "build_certificate_for_path",
    "check_certificate",
    "load_certificate",
    "model_hash",
    "reconstruction_trace",
    "write_certificate",
]


if __name__ == "__main__":
    raise SystemExit(main())
