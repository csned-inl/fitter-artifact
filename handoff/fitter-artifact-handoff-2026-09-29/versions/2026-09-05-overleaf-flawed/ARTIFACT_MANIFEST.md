# CLARITY Artifact Manifest

## Entry Points

| path or command | role |
|---|---|
| `run_fitting_sequence.sh` | portable launcher for the complete pipeline |
| `clarity-fit` | installed equivalent of the complete launcher |
| `clarity-evaluate-rule` | direct `#NeuralRequirement` evaluation |
| `clarity-certify-markov` | Markov process certificate generation and checking |
| `clarity-certify-discretization` | discretization certificate generation and checking |
| `make_overleaf_archive.sh` | source archive builder |

## Documentation

| path | contents |
|---|---|
| `README.md` | setup, supported SysML profile, execution, and validation |
| `ABOUT.md` | concise controller simplification example |
| `DISCRETIZATION_CERTIFICATION_DESIGN.md` | discretization claim and certification method |
| `ARTIFACT_MANIFEST.md` | source, dependency, and output inventory |

## Active Source

| path | contents |
|---|---|
| `src/clarity/models/` | bundled SysML models |
| `src/clarity/sysml/` | discovery, parsing, simulation, adapters, and shared `dt` validation |
| `src/clarity/certification/` | buffer reconstruction, Markov certificates, reduced MDP specifications, and derived architectures |
| `src/clarity/discretization/` | Stage 4 obligation construction, analysis coordination, checker progression, timing, and SMT fallback |
| `src/clarity/discretization/model/` | physical interval reduction and exact expression handling |
| `src/clarity/discretization/checkers/` | linear, convex, lazy factored, invariant, and reachability methods |
| `src/clarity/discretization/certificates/` | certificate generation, compact storage, source reconstruction, and separate evidence checking |
| `src/clarity/pipeline/stages/` | implementations of the five pipeline stages |
| `src/clarity/pipeline/` | pipeline coordination, command entry points, and generated reports |
| `src/clarity/runtime/` | simulator environment, requirement oracle, model helpers, and shield |
| `src/clarity/training/reduced/` | feedforward policy and NumPy PPO training |
| `src/clarity/training/recurrent/` | recurrent baseline implementation |

## Bundled Models

| model | path |
|---|---|
| Discrete cruise controller | `src/clarity/models/cruise-controller-model/model.sysml` |
| Chemical mixing plant | `src/clarity/models/mixing-sysml-model/model.sysml` |
| Thermostat | `src/clarity/models/thermostat/model.sysml` |

Historical continuous action material is stored under
`backups/continuous_controller/`. It is not imported, tested, or included in
the source archive.

## Runtime Dependencies

| dependency | use |
|---|---|
| Bash | shell launcher and source archive builder |
| Python 3.12 or newer | all stages |
| NumPy | simulator interfaces and training |
| SciPy | numerical candidates for linear and convex certificates |
| Z3 | Markov proof obligations and SMT fallbacks |
| PyTorch | retained runtime policy helpers |

Versions are pinned in `requirements.txt` and mirrored in `pyproject.toml`.

## Validation Programs

| path | checks |
|---|---|
| `tests/certification/` | reference models, rejected variants, proof reconstruction, and certificate mutations |
| `tests/discretization/` | source reduction, proof rules, checker progression, certificate replay, and mutations |
| `tests/training/` | reduced stack behavior, integration training, and recurrent gradients |

Validation programs remain outside the installed runtime package.

## Generated Output

| path | contents |
|---|---|
| `outputs/latest/sysml_inputs.json` | resolved SysML inputs for the run |
| `outputs/latest/01_affine_rule/` | direct controller requirement results |
| `outputs/latest/02_memoryless/` | current decision check |
| `outputs/latest/03_markov_mdp/` | Markov certificates, reduced specifications, SMT queries, and proof records |
| `outputs/latest/04_discretization_safety/` | discretization certificates and summaries |
| `outputs/latest/05_reduced_training/` | fitted policies, checkpoints, and evaluation summaries |
| `outputs/latest/logs/` | stage command summaries and failure tails |
| `outputs/latest/fitting_sequence_summary.json` | complete machine-readable run summary |
| `outputs/latest/fitting_sequence_report.md` | concise generated report |

Generated outputs, build products, virtual environments, and caches are
ignored by Git.
