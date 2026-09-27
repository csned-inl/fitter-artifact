# Additional proof/verification efficiency opportunities

Source review against `36f597e79f4f7e389ef47133126cb06b4cc2609d`.
This identifies candidates beyond the checked-type specialization design. No implementation or new experiments were performed for this review. Benefits below are hypotheses to measure, not established benchmark speedups.

The measured bottleneck remains Stage 3's source-transition solver: cruise control generated 15.3 million Boolean variables and 33.3 million cumulative clauses at search depth 1. The last full pipeline used 30-second proof-call timeouts and a 64 GB memory limit; all three source proofs timed out and the unit peaked at 10.8 GB. Later discretization certification was not reached. Its existing lazy factored checker already canonicalizes formulas and caches repeated subproblems; it should not be replaced on the basis of the Stage 3 profile.

## 1. Split the combined bad-state query into complete obligations

**Evidence.** `lazy_solver.construct` uses one `bad` predicate for representation failures, successor coverage, and disagreement between two executions. Output disagreement compares the full output and event structures.

**Candidate.** Give representation failures and individual output/requirement disagreements separate query predicates, retaining common transition definitions. Solve sequentially and require every obligation to discharge. Formally, absence of any violation is equivalent to the conjunction of absence of each violation. Separate queries may let the solver eliminate unrelated arithmetic and state.

**Correctness requirement.** The union of the new violation conditions must equal the original violation condition. Preserve event count/order, boundary identity, status/error, and every original requirement. A missing, timed-out, or failed subquery means no complete certificate. If a proved representation invariant is used in a later query, include its initiation/preservation evidence; do not assume it merely because it is convenient.

**Likely effect.** Potentially smaller peak solver problems; total time can increase if shared work is repeated. Splitting alone does not guarantee smaller arithmetic formulas, so measure it with unchanged transition definitions first.

## 2. Carry only justified dependencies in each proof relation

**Evidence.** `lazy_graph.fields` includes all scalar cells, message arrays, queues, call-frame data, machine state, inputs, and bookkeeping. Every `Reach`, `Step`, and `Transition` relation uses the resulting shared full-state datatype. Selecting q currently reduces compared outputs, not the whole state carried through the recursive proof.

**Candidate.** Derive the required input/output fields for a block or individual obligation and remove fields that cannot affect that obligation. Start with local unused temporaries and source-established constants. Keep shared frame equations rather than copying unchanged payloads into unrelated subproblems.

**Correctness requirement.** Dependency closure must include branch selection, aliases, presence, errors, message order, allocation, constraint convergence, and all observed outputs. A field absent from a property expression is not automatically irrelevant. Check that the retained state determines the projected transition and statuses. Never merge a physical value with its delayed counterpart.

**Likely effect.** Potentially high impact on solver size; moderate-to-high implementation effort. This needs checked dependency analysis, not a hand-maintained per-model list.

## 3. Make call/return edges more precise

**Evidence.** `lazy_solver.successors` connects a return node to every call site's continuation. `interval_order` uses this unguarded graph to decide whether to use the acyclic solver path. Actual control equations restore a particular saved continuation. Consequently, the structural cycle test can be more conservative than executable control flow. The current artifacts mark all three models cyclic; this review has not established which cycles are infeasible.

**Candidate.** Use source call structure and checked continuation information to remove impossible return edges. Then reuse the existing acyclic compiler for portions proved acyclic and keep recursive relations for actual cycles. Verified block summaries could reduce repeated internal reasoning.

**Correctness requirement.** Cover every compatible caller, exception edge and continuation. Removing an edge requires source-structural evidence or a checked invariant. Do not assume a particular scan schedule or use the observation buffer length as an execution-depth bound.

**Likely effect.** Potentially high if false structural cycles prevent the existing acyclic numeric-congruence method from being used. Benefit must be established model by model; it is not yet measured.

## 4. Reduce repeated constraint-pass and flow-order formulas

**Evidence.** `lazy_constraints.solve` lowers every pass up to the existing source-defined iteration limit. Each pass rebuilds assignments, flows, convergence checks, and requirement/error evaluations. `flow` repeatedly selects the next destination using symbolic insertion-order comparisons; its nested destination loops construct cubic-count comparisons in the number of destinations before sharing/simplification.

**Candidate.** Reuse source-fixed lookups and target sets, share unchanged expressions by exact input versions, and specialize ordering where its invariance follows from initialization and writes. Compare untouched store components by their unchanged definitions. Where a whole pass has been proved inactive or converged, emit the exact held-state summary rather than rebuilding arithmetic for it.

**Correctness requirement.** Preserve the original pass limit, operation order, convergence result, partial updates, accumulated errors, and identity-sensitive equality. Unresolved constraints and dynamically changing order must retain their existing behavior. No guessed smaller iteration bound.

**Likely effect.** Potentially useful for mixing's larger equation set. Existing `same_store` already skips cells that are the identical unchanged object; extend proven sharing rather than claiming that optimization is missing.

## 5. Use fixed records for finite output collections

**Evidence.** `lazy_solver.arrays` builds next-state/observation values and statuses as arrays indexed by arbitrary integers, starting with a constant array and writing a known finite list. Output/event equality then includes array equality. Constraint-error details use arrays too.

**Candidate.** Represent fixed-length output vectors as records or tuples with one component per actual field. Preserve output-kind variants explicitly. Keep variable-length event sequences until an event bound is separately established. Evaluate finite queue/frame encodings independently; their general replacement is a larger change.

**Correctness requirement.** Prove equivalence to the existing constant-default arrays, including indices outside the written range, error variants, and sequence ordering. Do not weaken equality to selected successful values or remove requirement statuses.

**Likely effect.** Removes some array-theory bookkeeping. The measured arithmetic expansion is much larger, so this is a secondary candidate, not an established cure for the RAM spike.

## 6. Reject already unsuccessful certificates before expensive solver replay

**Evidence.** `pipeline/markov_mdp.py` calls `check_certificate` after generation for every result. The checker invokes `_check_schema_and_equations`, including a fresh source solver query, before inspecting the theorem gate. Thus an UNKNOWN generation result can trigger the same expensive proof search again. Reduced-spec construction also calls certificate checking for passing artifacts.

**Candidate.** Reject an explicitly non-passing certificate before launching numerical verification. Keep inexpensive source/schema checks and report the original failure evidence; exhaustive diagnostic replay can remain an explicit diagnostic operation. For successful certificates, preserve independent verification. Reuse immutable compiled equations with exact identities, as the current cache already does, rather than trusting the generator's verdict.

**Correctness requirement.** Early rejection can never accept an invalid certificate. Do not remove the independent check for successful certificates or cache acceptance under incomplete keys. Verification by replaying a compact independently checkable proof would be a separate design, not something available merely because a JSON certificate exists.

**Likely effect.** Directly avoids repeated failed solver calls and improves turnaround. It does not reduce peak memory of a single solver call.

## 7. Store large solver formulas once

**Evidence.** `source_solver.py` embeds SMT text inside solver evidence. `pipeline/markov_mdp.py` writes that evidence into the certificate, a separate solver-result JSON, and a standalone SMT file. In the latest run, the mixing SMT file was about 64 MB before JSON escaping. Query serialization is also performed before the solver result is known.

**Candidate.** Store the exact formula once under a content hash and reference it from result/certificate records. Stream writes where possible and avoid keeping escaped duplicates in memory. Keep formula identity verifiable and the full query available for replay.

**Correctness requirement.** Every reference must be checked against its hash and the regenerated source query; missing or mismatched evidence cannot certify anything.

**Likely effect.** Lower artifact size, serialization cost and parent-process memory. This cannot explain or eliminate the measured approximately 10 GB solver-child allocation.

## Suggested order of investigation

Proceed with the checked-type design first. The lowest-risk additional saving is early rejection of already failed certificates. Next measure complete obligation splitting and audit overly broad return edges; these can target the actual solver workload without changing source semantics. Checked dependency reduction and constraint-pass sharing require more implementation and correspondence work. Fixed output records and artifact deduplication are secondary improvements.

Use the same queries, property coverage, 30-second proof-call limits, 64 GB process-group cap, source inputs and workstation for comparisons. Measure compilation, solver and independent verification separately. Do not report a reduction in output size or duplicate calls as a reduction in the complexity of one proof query.

## Implementation checkpoint

The compact compiler is implemented and connected to the production source-transition proof entry point. It keeps distinct physical, held, sent and received values and shares source-node equations. Its latest implemented version includes the 30-second timeout settings; 64 GB is the worker job's enforced memory limit.

The original 47-test compact compiler suite passed before the timeout-only changes. A broader validation run had 10 of 18 commands pass and eight fail; original-policy replay recorded zero violations over 600 episodes. These are component/runtime results, not a complete safety certificate.

The full pipeline passes Stages 1 and 2, then stops at Stage 3, MDP certification. At the current limits all three models return UNKNOWN due to source-proof solver timeouts. Delayed-state history reconstruction is also incomplete; the compact compiler represents those updates, but the downstream reconstruction does not yet supply the needed next-decision equations. Cyclic intervals still lack discharged progress evidence. None of those failures was resolved by increasing the timeout or memory limit.

The checked-type specialization has a completed design in `CHECKED_TYPE_SPECIALIZATION_DESIGN.md` but has not been implemented. Its immediate implementation starting point is deriving/replaying type evidence, followed by operation specialization, scalar-layout integration, certificate integration and workstation validation. The additional opportunities in this document are identified candidates, not implemented changes. Stage 4 discretization-safety certification and Stage 5 reduced training have not been reached in the latest pipeline run.
