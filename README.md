# CLARITY Controller Fitting Artifact

This repository is a self contained implementation of the CLARITY controller
fitting pipeline. It discovers the included SysML v2 models, extracts their
controller contracts and physical equations, proves finite buffers sufficient
for Markov control, certifies safety throughout each fixed discretization
interval, and can train the resulting feedforward policies.

The active artifact supports the three discrete action models under
`src/clarity/models/`. Historical continuous action material is isolated under
`backups/continuous_controller/` and is not loaded by the pipeline.

## Setup

Use Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

This installs the dependencies and provides the four `clarity-*` commands
declared in `pyproject.toml`. The shell launcher can also run directly from a
source checkout because it supplies `src/` on `PYTHONPATH`.

## Run

Run the complete pipeline from this directory.

```bash
bash run_fitting_sequence.sh
```

Run preprocessing only, through discretization certification.

```bash
bash run_fitting_sequence.sh --preprocessing-only
```

Select particular models or change the one fixed time step used by every
stage.

```bash
bash run_fitting_sequence.sh --dt 0.1 path/to/model.sysml
```

Use `--models-root path/to/models` to discover models under another directory.
Use `--smoke-training` for a short Stage 5 integration run.

## Pipeline

1. The action extraction stage evaluates the `#NeuralRequirement` directly.
2. The memoryless stage checks whether the current neural inputs determine the
   current controller constraint.
3. The Markov process stage finds an observation and action buffer, checks the
   corresponding theorem obligations, and writes the reduced MDP
   specification.
4. The discretization stage proves every parsed `#Prohibition` and
   `#Obligation` over the complete physical interval from zero through `dt`.
5. The training stage fits a feedforward policy from the checked reduced
   specification and retains the SysML derived shield.

Stage 4 uses one model independent progression. It constructs the unsafe
constraint lazily, attempts linear and convex proofs first, then exact symbolic
and SMT checks, and finally performs reachable state exclusion when local
feasibility is insufficient. A method that cannot prove its applicability or
claim returns `DEFERRED`; it cannot silently certify the case.

The discretization certificate checker is separate from generation. It
reparses the hashed SysML source, reconstructs the property, physical
trajectories, sensor mappings, endpoint equations, constants, initial
conditions, and sampled transition relation, then checks the recorded exact
proof evidence. Repeated proof subtrees are stored in a validated content
addressed pool and the certificate is written as canonical compact JSON.

## Outputs

Each run replaces `outputs/latest/`.

| path | contents |
|---|---|
| `01_affine_rule/` | direct requirement evaluation |
| `02_memoryless/` | current input controller check |
| `03_markov_mdp/` | certificates, reduced specs, SMT queries, and proof records |
| `04_discretization_safety/` | compact safety certificates and summaries |
| `05_reduced_training/` | fitted policies, checkpoints, and evaluation summaries |
| `fitting_sequence_summary.json` | machine readable run summary |
| `fitting_sequence_report.md` | concise generated report |

## Validation

Run the focused regression groups against freshly generated certificates.

```bash
python tests/certification/validate_reference_models.py
python tests/training/validate_reduced_stack.py --skip-smoke
python tests/training/validate_recurrent_gradients.py
python tests/discretization/validate_discretization.py \
  outputs/latest/04_discretization_safety/certificates/*.certificate.json
```

The discretization battery separately covers arithmetic proof rules,
certificate mutation rejection, source reconstruction, and loud deferral when
within step semantics are missing.

## Source Layout

| path | role |
|---|---|
| `src/clarity/sysml/` | parser, simulator, discovery, and fixed `dt` handling |
| `src/clarity/certification/` | Markov extraction plus separate certificate generation, schema, and checking |
| `src/clarity/discretization/model/` | physical interval reduction |
| `src/clarity/discretization/checkers/` | linear, convex, symbolic, and reachability methods |
| `src/clarity/discretization/certificates/` | generation, compact storage, and independent checking |
| `src/clarity/pipeline/stages/` | one implementation module for each pipeline stage |
| `src/clarity/pipeline/` | the coordinator, shared process support, reports, and stage entry points |
| `src/clarity/runtime/` | simulator environment, requirement oracle, and shield |
| `src/clarity/training/` | recurrent baseline and reduced feedforward training |
| `tests/` | validation programs, kept outside the installed package |

## Archive

```bash
bash make_overleaf_archive.sh
```

This writes `../fitter_artifact_overleaf.zip`. Git metadata, the virtual
environment, generated outputs, historical backups, build products, and Python
caches are excluded.
