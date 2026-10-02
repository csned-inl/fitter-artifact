THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Binding model-source authority

This rule applies to every direct SysML certificate, proof plan, implementation,
test, report, and retrospective in this project.

## Authority order

1. The authoritative model is the exact file in `csned-inl/clarity-standalone`.
2. A local model copy is usable only after it is checked byte-for-byte against
   a recorded standalone repository path and immutable Git blob identity.
3. `fitter-artifact` contains proof implementations and outputs. It does not
   establish model semantics, even when it contains a file with the same name.
4. Generated SMV, simulator behavior, old certificates, backups, extracted
   equations, and prior agent work are comparison evidence only. None may
   override, complete, or reinterpret the standalone SysML source.

## Required provenance record

Every model-specific certificate must record the standalone repository, file
path, reviewed ref or commit, Git blob SHA, and byte-level source digest. Source
validation must fail closed if the standalone identity is unavailable or if a
local copy differs.

## Conflict rule

If any implementation, artifact, simulator, SMV export, certificate, or prior
document disagrees with the standalone SysML model, stop. Do not choose the
apparently newer, more convenient, or previously certified interpretation.
Report the disagreement to the user before changing the proof contract.

## Current Mixing Machine authority

- Repository: `csned-inl/clarity-standalone`
- Path: `sysml-models/mixing-sysml-model/model.sysml`
- Reviewed branch: `main`
- Reviewed standalone commit: `aa3c6ae4640e3bcadcf66542eb0124e6c57e2bb3`
- Git blob SHA observed on 2026-10-02: `20ef2ed6dd55ab083ce8b1a403beab17c58dbdff`
- SHA-256: `09d59723435da435325299934ed8472bbac3e89c6d9840df892b9f7e1c912f8f`

The similarly named file and all historical Mixing Machine certificates in
`fitter-artifact` are explicitly non-authoritative.
