# CLARITY fitter artifact: version and verification handoff

Captured September 29, 2026. This directory brings together the flawed Overleaf archive, its unpacked source, a checkpoint immediately before compact proof compilation began, and the modified source currently in `automated-writing/fitter_artifact`. The dated directory names describe when each source was archived or captured; they do not claim a successful certification.

## Package map

| Location | Source and identity | Purpose |
|---|---|---|
| [`archives/fitter_artifact_overleaf_2026-09-05.tar`](archives/fitter_artifact_overleaf_2026-09-05.tar) | Overleaf repository `fitter_artifact.tar` at commit `bf284bb9914d7b7b30143a230e6fb60b7351411f`, September 5 | Exact original archive |
| [`versions/2026-09-05-overleaf-flawed/`](versions/2026-09-05-overleaf-flawed/) | Contents of that tar, with its top-level `fitter_artifact/` directory removed only for browsing | Inspect the flawed version |
| [`versions/2026-09-19-pre-compact-256941e/`](versions/2026-09-19-pre-compact-256941e/) | Git tree `256941e8926de722ae277d86b458043e57a2e6b5`, September 19, 19:21 EDT, titled “Checkpoint source before compact lazy implementation” | Corrected-value checkpoint before the lighter proof compiler |
| [`versions/2026-09-29-current-modified-working-tree/`](versions/2026-09-29-current-modified-working-tree/) | Source, tests, and documentation copied September 29 from `automated-writing/fitter_artifact`, whose local HEAD is `6d17efb` plus uncommitted and untracked work | Current partial implementation |
| [`evidence/`](evidence/) | Selected recorded run reports, working-tree status, and tracked diff | Evidence used for this handoff |

The pre-compact checkpoint is on a proof-development branch, not a direct ancestor of the current working tree's local HEAD. The current source files were compared with the separately committed September 27 checkout `f968a86`; they matched except for `.gitignore` and generated/cache files. The *modified working tree*, including its untracked source and documents, is the version packaged here. `evidence/current-tracked-changes.patch` covers only tracked changes relative to local HEAD; the complete current source is in its version directory.

## What was wrong with the old verification

The September 5 archive's discretization reduction defines `physical_aliases` in [`physical_reduction.py`](versions/2026-09-05-overleaf-flawed/src/clarity/discretization/model/physical_reduction.py#L68). It follows an equation path from a sampled symbol to a physical variable and records the rule `ordered_sensor_path_to_current_physical_value_v1`. Its purported independent source reconstruction repeats the mapping in [`source_reconstruction.py`](versions/2026-09-05-overleaf-flawed/src/clarity/discretization/certificates/verification/source_reconstruction.py#L414).

That path establishes provenance, not simultaneous equality. The plant may change after a sensor samples; sent and received messages may carry older values. Replacing the held or received value with the current physical value can therefore make the proof check a different state from the one the controller actually used. Repeating the same substitution in the checker does not validate it. This is the specific unsound rule found in the archived version; it does not by itself establish which individual archived certificates would fail a repaired checker.

The later repair is explained in the current [`VALUE_STATE_CORRECTNESS.md`](versions/2026-09-29-current-modified-working-tree/VALUE_STATE_CORRECTNESS.md#L40). It keeps physical state, stored sensor readings, sent payloads, and received payloads distinct. The pre-compact source already describes sampled state as independent storage in [`physical_reduction.py`](versions/2026-09-19-pre-compact-256941e/src/clarity/discretization/model/physical_reduction.py). Preserving these values exposed another gap: Stage 3 needed an ordered account of sampling, delivery, controller decisions, and plant updates. The corrected system could no longer claim certification by substituting current plant values for delayed observations.

## Checked timeline

| Date | Event and supporting record |
|---|---|
| September 5 | Overleaf commit `bf284bb` added the archived tar. The physical-alias rule is present in its packaged source. |
| September 16 | Commit `e7aa3ce` recorded the value-state integration; the later `VALUE_STATE_CORRECTNESS.md` describes the remaining ordered event gap. |
| September 19, 19:21 EDT | Commit `256941e` explicitly saved the source before compact lazy implementation. It retains sampled state independently. The next proof-development commit, `b428328`, followed at 19:24. |
| September 19, later | Workstation reports recorded the 8 GB failure, then the 30-second/64 GB rerun and solver-encoding diagnosis. |
| September 20 | The implementation record describes checked type facts, sparse state, a solver regression, and focused validation. Current code contains these features. |
| September 25–29 | The local `automated-writing/fitter_artifact` HEAD is `6d17efb` from September 25 with further uncommitted work. This package captures that working tree on September 29. |

## Why the proof process was made lighter

The corrected source semantics made a more faithful transition proof necessary. The pre-compact [`COMPACT_LAZY_PROOF_DESIGN.md`](versions/2026-09-19-pre-compact-256941e/docs/COMPACT_LAZY_PROOF_DESIGN.md#L30) identifies the next scaling problem: `transition_equations.py` created new symbolic runtime configurations for branches and continued expanding them before constructing the solver proof. The intended replacement represents source operations once in a graph, with branch conditions and named state equations, while keeping physical and delayed locations separate.

The compact compiler was then implemented, but a September 19 full run still stopped at Stage 3. [`TIMEOUT30_RESULTS.md`](evidence/TIMEOUT30_RESULTS.md) records a preceding 8 GB solver out-of-memory kill and a rerun at 64 GB with 30-second proof-call limits. On that rerun, cruise, mixing, and thermostat all returned `UNKNOWN` from solver timeouts; the unit peaked at 10.8 GB. [`SOLVER_ENCODING_DIAGNOSIS.md`](evidence/SOLVER_ENCODING_DIAGNOSIS.md) measured 15,288,648 Boolean variables and 33,300,678 cumulative clauses in the cruise query at search depth 1. The diagnosis attributes much of the overhead to generic tagged numerical values and arithmetic branches, rather than to Python-side path enumeration in the new compiler. These measurements explain the turn from merely avoiding path duplication to proving value types and reducing the solver's state representation.

## Where the current implementation stands

The current [`COMPACT_LAZY_IMPLEMENTATION.md`](versions/2026-09-29-current-modified-working-tree/docs/COMPACT_LAZY_IMPLEMENTATION.md) records checked constructor propagation, scalar specialization, sparse state relations, source-history integration, and a solver setting correction. The code connects type facts in [`lazy_graph.py`](versions/2026-09-29-current-modified-working-tree/src/clarity/certification/lazy_graph.py#L89), selects sparse relations in [`lazy_solver.py`](versions/2026-09-29-current-modified-working-tree/src/clarity/certification/lazy_solver.py#L361), and enables eager inlining there after a negative regression found false acceptance with the earlier setting. Physical and held values remain separate arguments.

**The starting point for further work is repeated Stage 3 proof timeouts. We do not yet know whether the full verification succeeds or whether further correctness defects remain.** The last recorded full pipeline run returned `UNKNOWN` for all three models; a later focused solver run recorded in `COMPACT_LAZY_IMPLEMENTATION.md` passed 18 transition-solver tests, but production closure queries for thermostat and cruise still returned `UNKNOWN` at 30 seconds. `UNKNOWN` is neither a proof nor a counterexample. The current [README](versions/2026-09-29-current-modified-working-tree/README.md) says the bundled models are uncertified. Source-to-next-decision correspondence, delayed-state history reconstruction, and progress for cyclic intervals remain proof obligations. Stage 4 discretization certification and Stage 5 reduced training were not reached in the recorded full run.

Two planning documents need to be read in time order. [`CHECKED_TYPE_SPECIALIZATION_DESIGN.md`](versions/2026-09-29-current-modified-working-tree/docs/CHECKED_TYPE_SPECIALIZATION_DESIGN.md) and [`PROOF_EFFICIENCY_REVIEW.md`](versions/2026-09-29-current-modified-working-tree/docs/PROOF_EFFICIENCY_REVIEW.md) describe specialization as unimplemented. Later September 20 entries in `COMPACT_LAZY_IMPLEMENTATION.md` and the current code show that checked type facts, specialized scalar layout, and sparse relations were subsequently implemented. The efficiency review's other suggestions remain candidates, not demonstrated fixes. Successful component tests and policy replay are not complete safety certificates.

## Validation of this handoff

- The copied tar's SHA-256 matches the Overleaf repository's `fitter_artifact.tar` blob at `bf284bb`. Its extracted files were compared byte-for-byte with all regular files in the tar.
- The pre-compact directory was created directly from Git tree `256941e`; its tree contents were compared with a fresh Git archive of that commit.
- The current directory's files were compared byte-for-byte with the modified `automated-writing/fitter_artifact` working tree after excluding Git metadata, virtual environments, generated outputs/results, bytecode, and three AppleDouble sidecars. The current working-tree status is recorded in `evidence/`.
- The flaw, repair, and current compiler claims above were checked against the code in the packaged versions. Run outcomes were checked against the copied reports. No new full certification run is claimed.
- All 504 packaged Python files parsed successfully. The package has no symlinks or files over 10 MB; a scan found no files with common credential names or common private-key/API-token patterns.
- [`SHA256SUMS`](SHA256SUMS) gives hashes for the original tar and every packaged file. Run `sha256sum -c SHA256SUMS` from this directory after transfer.
