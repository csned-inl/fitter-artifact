THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

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

The Z3 Python API reports the engine version (`4.15.4`) while package metadata
reports the pinned distribution version (`4.15.4.0`). Readiness is checked
against the distribution version; both values are retained in the report.

To inspect the machine without changing the Python environment:

```bash
bash scripts/finite_history_workstation_preflight.sh
```

When `.venv-finite-history` already exists, inspection automatically uses its
interpreter. Set `PYTHON_BIN=/path/to/python` only to test a different explicit
environment.

## Run implemented gates

For the normal end-to-end workstation handoff, use one command:

```bash
bash scripts/finite_history_workstation_cycle.sh
```

It verifies the expected branch, retries a fast-forward-only GitHub sync,
re-executes itself if the pull updated the runner, reuses the existing pinned
environment when it is ready, runs preflight and all implemented gates, writes
`.codex-workstation/latest.json`, and invokes `wsl-inventory` when installed.
The inventory contains only the sanitized latest result, so a remote reviewer
can inspect it without terminal output being pasted into chat.

Use `--offline` to deliberately validate the currently checked-out revision,
or `--no-inventory` to keep the result local. Neither is the normal workflow.

The lower-level gate command remains available for development:

```bash
bash scripts/finite_history_workstation_gates.sh
```

Each gate has a five-minute external deadline by default. Override it only when
needed:

```bash
FINITE_HISTORY_GATE_TIMEOUT_SECONDS=900 \
  bash scripts/finite_history_workstation_gates.sh
```

The runner records each check's exit status, duration, output hashes, and a
bounded sanitized diagnostic for failures, plus compilation status, Z3
availability, and an explicit `certificate_claimed: false`. Raw logs remain
local under `runs/`; only the allow-listed structured failure summary is
published by WSL inventory. It cannot run or claim the final SMT proof until
production obligation lowering and full certificate replay are implemented.
`z3_fixture_status: passed` confirms only the fail-closed solver boundary and
its expected SAT/UNSAT smoke fixtures.
