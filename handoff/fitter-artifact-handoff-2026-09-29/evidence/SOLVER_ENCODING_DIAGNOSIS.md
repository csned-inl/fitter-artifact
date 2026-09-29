# Solver encoding diagnosis

## Conclusion

The production encoding has demonstrated sources of avoidable overhead. Compilation is compact, but the backend expands generic arithmetic into millions of Boolean constraints. The intended optimization that proves fixed value types and specializes their representation is not connected to the production compiler. These findings identify an implementation efficiency problem; they do not establish that specialization alone will complete the three benchmark proofs.

No production code, model, parser, or solver settings were changed during this investigation. The examined implementation remains `36f597e79f4f7e389ef47133126cb06b4cc2609d`.

## Actual benchmark query

On kubuntu-workstation, the saved cruise-control query was replayed with the same Spacer backend, both existing inlining options disabled, and a 30-second internal timeout. Verbose diagnostics were enabled. A 35-second external process deadline and 64 GB memory limit bounded the diagnostic. This replay diagnoses the saved formula; it is not an additional successful certificate.

Z3 version: 5.0.0. Parsing took 0.060 seconds. The parsed query introduced one wrapper rule, giving 549 rules and 251 predicates. Rule initialization took 0.29 seconds, and SMT solving consumed 27.51 seconds before cancellation.

Reported statistics:

| Quantity | Value |
| --- | ---: |
| Maximum Spacer search depth | 1 |
| Boolean variables created | 15,288,648 |
| Clauses created, cumulative | 33,300,678 |
| Bit-vector bit-to-core operations | 1,153,266 |
| Z3 maximum memory, reported MB | 9,883.74 |
| Process-group peak memory, systemd | 9.5 GB |

The clause count is cumulative; it is not the number of clauses simultaneously retained. The trace shows substantial Boolean/bit-vector arithmetic work at shallow search depth, not exponential enumeration of long controller execution paths by the Python compiler. It does not isolate the cost of each arithmetic operator.

## Encoding mismatch with the approved design

`lazy_scalar_store.py` creates every symbolic scalar as `Value`; message payload values use the same representation. `lazy_expressions.py` implements each numerical operation with guarded integer, floating-point, mixed-type conversion, overflow, and error cases. In particular, generic operands retain Int/Real/Float64 conversions and mixed-comparison formulas even where a separately proved type invariant could eliminate them.

Section 5 of `docs/COMPACT_LAZY_PROOF_DESIGN.md` calls for statically typed fields where source operations establish one kind. The helper `lazy_specialization.check_specialization` exists, but no production compiler module imports or invokes it. Its current callers are tests. The same design required backend feasibility to be established before broad integration; the mixed tagged-value recursive capability test still fails.

The sound basis for removing irrelevant type branches is an invariant I proved from initialization and preserved by every relevant source transition. If I establishes that a location always contains a float, replacing its tagged representation with the corresponding Float64 payload is reversible on those reachable states. Physical and delayed locations remain separate. A source declaration alone is not this proof.

## Existing capability tests, unchanged

Each fixture ran in a separate workstation process with its original one-second solver limit:

| Fixture | Result | Total elapsed seconds | Process peak RSS KiB |
| --- | --- | ---: | ---: |
| Native floating-point sample/hold | UNSAT | 0.0044 | 65,520 |
| Generic tagged arithmetic | UNKNOWN / timeout | 1.0512 | 210,756 |
| Checked fixed-type arithmetic | UNSAT | 0.0997 | 79,908 |

The generic fixture produced 1,004,862 Boolean variables and 2,203,068 clauses. The checked fixed-type fixture includes explicit initiation, preservation, reversible-projection and absence-of-error checks, followed by a nonnegative-value proof. These are small capability examples, not full-model speedup measurements; their final queries are not identical.

## Matched-property diagnostic and backend failure

A separate, in-memory-only diagnostic strengthened the generic fixture to check the same no-error, floating-type, nonnegative-value property established by the checked-type fixture. Both used a 30-second solver setting. The generic child exited with signal 11 before reporting a result; the checked-type fixture proved the property in 0.097 seconds. Repeating the diagnostic confirmed the generic child's exit code -11.

This is a solver-process crash on a diagnostic formula, not a safety counterexample and not what the production 30-second pipeline run reported. The initial diagnostic wrapper did not propagate the child failure; a follow-up explicitly captured the return codes. Neither wrapper completion nor the empty generic output is counted as a passing test. The internal native cause of this crash has not been isolated.

## Evidence

- [Exact-query verbose trace](solver-verbose-diagnosis-events.json)
- [Original capability test results](solver-encoding-diagnosis-events.json)
- [Matched-property diagnostic with explicit child exit codes](solver-comparison-exit-events.json)

All computational diagnostics were dispatched through Harnesslite to kubuntu-workstation. No benchmark proof was obtained in this investigation.
