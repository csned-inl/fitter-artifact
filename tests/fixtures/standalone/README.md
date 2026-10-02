THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Standalone acceptance fixtures

These files are non-authoritative, byte-for-byte test mirrors. They exist only
so the generic `prove_markov(model_path)` acceptance test can run without
network access. The compiler does not recognize their names or hashes.

| Fixture | Authoritative repository path | Git blob | SHA-256 |
| --- | --- | --- | --- |
| `cruise-controller-model.sysml` | `csned-inl/clarity-standalone:sysml-models/cruise-controller-model/model.sysml` | `a789060d978dd52331f7b1f9485233518fa63e6a` | `3fd949d55c97de73946be48f803f982eecbb2fee78e568843d77ef4964403254` |

Any mismatch stops the acceptance test. Refreshing a fixture requires explicit
source review; a local edit never changes authoritative model semantics.
