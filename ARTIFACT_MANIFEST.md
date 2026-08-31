# CLARITY Artifact Manifest

## Entry Points

| path or command | role |
|---|---|
| `run_fitting_sequence.sh` | portable complete pipeline launcher |
| `clarity-fit` | installed equivalent of the complete launcher |
| `clarity-evaluate-rule` | direct `#NeuralRequirement` evaluation |
| `clarity-certify-markov` | Markov process certificate generation |
| `clarity-certify-discretization` | discretization certificate generation and checking |
| `make_overleaf_archive.sh` | clean source archive builder |

## Active Source

| path | contents |
|---|---|
| `src/clarity/models/` | thermostat, chemical mixing plant, and discrete cruise controller SysML files |
| `src/clarity/sysml/` | SysML parser, simulator, model discovery, adapter, and shared time step validation |
| `src/clarity/certification/` | strict equation extraction, relevance, reconstruction, Markov process certificates, and reduced MDP specs |
| `src/clarity/discretization/model/` | complete physical interval reduction and constraint normalization |
| `src/clarity/discretization/checkers/` | linear, convex, exact symbolic, local SMT, invariant, and SMT reachability checks |
| `src/clarity/discretization/certificates/` | certificate generation, compact content addressed storage, and independent verification |
| `src/clarity/pipeline/` | four preprocessing stages and fitted training orchestration |
| `src/clarity/runtime/` | environment, oracle, shield, and runtime model helpers |
| `src/clarity/training/reduced/` | fitted feedforward policy and NumPy PPO training |
| `src/clarity/training/recurrent/` | recurrent baseline implementation |

Validation programs are under `tests/certification/`,
`tests/discretization/`, and `tests/training/`. They are not installed in the
runtime package.

Historical continuous action files are under `backups/continuous_controller/`.
They are not imported, tested, packaged, or included in the source archive.

## Runtime Dependencies

| dependency | use |
|---|---|
| Python 3.12 or newer | all stages |
| NumPy | simulator interfaces and training |
| SciPy | numerical candidates for linear and convex certificates |
| Z3 | Markov process proof obligations and final SMT fallbacks |
| PyTorch | retained training dependencies |

All versions are pinned in `requirements.txt` and mirrored by
`pyproject.toml`.

## Generated Output

| path | contents |
|---|---|
| `outputs/latest/01_affine_rule/` | direct controller rule results |
| `outputs/latest/02_memoryless/` | current input check |
| `outputs/latest/03_markov_mdp/` | Markov process certificates and reduced specifications |
| `outputs/latest/04_discretization_safety/` | compact canonical safety certificates |
| `outputs/latest/05_reduced_training/` | fitted policy runs when training is enabled |
| `outputs/latest/logs/` | captured stage output |

Generated outputs, build products, virtual environments, and caches are
ignored by Git.
