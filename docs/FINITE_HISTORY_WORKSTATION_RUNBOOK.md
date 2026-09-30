# Finite-history proof workstation runbook

Target workstation: Dell Precision 5690 (`INL432984`) running Windows 11
Enterprise with WSL/Hyper-V.

Confirmed host resources from the 2026-09-24 Windows reports:

| Resource | Confirmed value |
| --- | --- |
| CPU | Intel Core Ultra 9 185H, 16 cores, 22 logical processors |
| RAM | 64 GB installed, 63.5 GB host-visible |
| Storage | Samsung PM9A1 NVMe 1024 GB |
| Discrete GPU | NVIDIA RTX 5000 Ada Laptop GPU, 16 GB dedicated VRAM |
| Integrated GPU | Intel Arc Pro Graphics |
| Virtualization | Hypervisor present; Hyper-V and virtualization-based security running |

Z3 is CPU- and memory-bound; neither GPU is expected to accelerate these proof
queries. WSL may expose less RAM or fewer processors than Windows. The preflight
script therefore records the Linux-visible allocation and computes a provisional
parallel-job limit. Until measured peaks from the new backend exist, it reserves
8 GiB for WSL/Windows pressure, budgets 12 GiB per solver process, and caps
parallelism at four jobs.

## Prepare WSL

```bash
cd "$HOME/src/fitter-artifact"
git fetch origin
git switch codex/finite-history-mdp-proof
git pull --ff-only origin codex/finite-history-mdp-proof

bash scripts/finite_history_workstation_preflight.sh --create-venv
```

This creates `.venv-finite-history` when absent and reuses it when present.
`pip install -r` verifies/satisfies the pinned requirements without forcing
already-satisfied packages to reinstall. Machine-readable evidence is written
under the git-ignored `runs/` directory. Installation failures remain failures;
the script does not substitute another Z3 version.

The Z3 Python API reports the engine version (`5.0.0`) while package metadata
reports the pinned distribution version (`5.0.0.0`). Readiness is checked
against the distribution version; both values are retained in the report.

To inspect the machine without changing the Python environment:

```bash
bash scripts/finite_history_workstation_preflight.sh
```

## Run implemented gates

```bash
bash scripts/finite_history_workstation_gates.sh
```

Each gate has a five-minute external deadline by default. Override it only when
needed:

```bash
FINITE_HISTORY_GATE_TIMEOUT_SECONDS=900 \
  bash scripts/finite_history_workstation_gates.sh
```

The runner records stdout, stderr, compilation status, Z3 availability, and an
explicit `certificate_claimed: false`. It cannot run or claim the final SMT
proof until production obligation lowering and full certificate replay are
implemented. `z3_fixture_status: passed` confirms only the fail-closed solver
boundary and its expected SAT/UNSAT smoke fixtures.
