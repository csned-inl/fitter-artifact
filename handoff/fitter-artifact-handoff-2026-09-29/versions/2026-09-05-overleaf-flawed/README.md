# CLARITY Controller Fitting Artifact

This repository contains a self-contained implementation of the CLARITY
controller fitting pipeline. It reads controller contracts and process
equations from SysML v2, determines when recurrent state can be replaced by a
finite buffer, checks the resulting sampled process, certifies physical safety
between controller updates, and trains a compact feedforward policy.

The active artifact supports the thermostat, chemical mixing plant, and
discrete cruise controller under `src/clarity/models/`. Historical continuous
action material is isolated under `backups/continuous_controller/` and is not
loaded by the active pipeline.

## Setup

Use Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

This installs the pinned dependencies and the four `clarity-*` commands
declared in `pyproject.toml`.

## Run

Run all five stages on the bundled models.

```bash
bash run_fitting_sequence.sh
```

Run preprocessing through discretization certification without training.

```bash
bash run_fitting_sequence.sh --preprocessing-only
```

Particular SysML files and one fixed time step can be supplied directly.

```bash
bash run_fitting_sequence.sh --dt 0.1 path/to/model.sysml
```

`--models-root` selects another model directory. `--smoke-training` performs a
short Stage 5 integration run. The complete command interface is available
through `python -m clarity.pipeline.runner --help`.

## Supported SysML Profile

The pipeline obtains its controller and process semantics from these
annotations.

| annotation | use |
|---|---|
| `#Neural` | identifies the policy action and its typed inputs and outputs |
| `#NeuralRequirement` | defines the controller contract used for checking, architecture derivation, and shielding |
| `#Completion` | identifies the policy completion input |
| `#ScenarioInput` and `#ScenarioConstraint` | define scenario parameters, bounds, and initial relations |
| `#Prohibition` and `#Obligation` | identify system properties checked by discretization certification |
| `#ContinuousRate` | identifies sampled rate updates interpreted over the physical interval |

Connections, flows, sensor equations, actuator equations, state updates, and
initial values are read from the surrounding SysML model. Missing or ambiguous
semantics stop certification rather than being supplied by model-specific
configuration.

## Pipeline

1. Direct action extraction evaluates the `#NeuralRequirement` when it
   determines the current action without learned parameters.
2. The memoryless check establishes whether the current neural inputs determine
   the immediate controller constraint. This claim concerns the current
   decision, not the complete physical process.
3. Markov process certification searches up to two prior observations and four
   prior executed actions for a sufficient finite buffer. It checks the
   sampled transition obligations and writes a certificate and reduced MDP
   specification.
4. Discretization certification checks the Stage 3 evidence and proves every
   parsed `#Prohibition` and `#Obligation` at the controller update and
   throughout the following fixed `dt` interval. The complete physical model is
   used, including state not observed directly by the controller.
5. Feedforward training consumes the Stage 3 reduced MDP specification. The
   checked architecture uses two hidden layers with a shared width equal to the
   larger of the number of distinct SysML comparison boundaries and Boolean
   controller outputs. Training retains the SysML-derived shield and begins
   only after Stage 4 certifies the model.

Every time-dependent stage receives the same validated `dt`. The Stage 4
method is described in `DISCRETIZATION_CERTIFICATION_DESIGN.md`.

## Outputs

Each run rebuilds its selected output directory and writes one numbered
subdirectory per stage. `fitting_sequence_summary.json` records the complete
machine-readable result and `fitting_sequence_report.md` presents the same run
in a compact table. The exact output inventory is in `ARTIFACT_MANIFEST.md`.

## Validation

Run the validation programs from the artifact root.

```bash
python tests/certification/validate_reference_models.py
python tests/certification/validate_certification_battery.py
python tests/discretization/validate_discretization.py \
  outputs/latest/04_discretization_safety/certificates/*.certificate.json
python tests/training/validate_reduced_stack.py
python tests/training/validate_recurrent_gradients.py
```

The suites cover the bundled models, rejected model variants, certificate
mutations, proof rules, source reconstruction, checker deferral, reduced
training, and recurrent baseline gradients.

## Archive

```bash
bash make_overleaf_archive.sh
```

This writes `../fitter_artifact_overleaf.zip`. Git metadata, generated outputs,
historical backups, the virtual environment, build products, and Python caches
are excluded.
