THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from csned-inl/clarity-standalone. fitter-artifact, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# GitHub Actions execution

The project has three deliberately separate execution tiers. No tier
changes the source-authority rule or authorizes simulator reconstruction.

The design and personal-side setup contract for a GitHub-mediated runner loop
are in `docs/PERSONAL_RUNNER_BRIDGE_HANDOFF.md`. That bridge keeps GitHub as the
only rendezvous point between the work-side agent and personal compute; it does
not connect personal hardware to the locked-down work laptop.

## Three-tier testing policy

All development and certification work must use the cheapest sufficient tier
and escalate only when the required capability is unavailable or the workload
is materially better suited to the next tier.

### Tier 1: work-side private VM

Use the agent's private VM for immediate, dependency-light feedback:

- source inspection and diffs;
- parsing, formatting, and static checks;
- solver-independent unit tests;
- small deterministic transformations;
- construction and review of proof queries and certificates.

This VM may lack Z3, GPU support, system packages, or other project
dependencies. A skipped test or missing dependency is not a pass. When a
required capability is absent, escalate the exact test unchanged rather than
weakening it.

### Tier 2: GitHub-hosted runners

Use GitHub-hosted Actions as the default execution environment for ordinary CI
and available semantic checks:

- install the repository's pinned dependencies;
- run the structural, controller-class, and direct Z3 tests;
- exercise clean-environment installation;
- run moderate CPU and parallel test workloads;
- gate commits before requesting personal compute.

Prefer this tier over personal hardware whenever it can run the required test
faithfully. A hosted failure stops escalation until the failure is understood,
unless the failure is specifically an unavailable hosted capability.

### Tier 3: personal self-hosted compute

Use the GitHub-mediated personal runner bridge only for work that needs a
capability or scale not reasonably available in the first two tiers:

- GPU or specialized hardware;
- large memory or CPU parallelism;
- long-running proof or analysis jobs;
- exact personal-side environments;
- final reproduction on the user's selected hardware.

The bridge has nontrivial dispatch and publication latency. Batch related tests
into one version-pinned request instead of issuing many tiny jobs. Hardware
allocation behind the runner remains a personal-side decision.

### Routing and soundness rules

1. Run all applicable Tier 1 checks first.
2. Run the normal pinned suite on Tier 2.
3. Send only the remaining capability-dependent or heavy work to Tier 3.
4. Identify every remote job by exact repository and 40-character commit SHA.
5. Use the same test or proof entry point across tiers when the environments
   support it; do not substitute a weaker check merely to obtain a pass.
6. Record the tier, dependency versions, source tree, model-authority commit,
   and artifact hashes in durable results.
7. Treat timeout, skipped coverage, solver `unknown`, missing capability, and
   infrastructure failure as inconclusive or error, never proof success.
8. When a heavy tier generates a certificate, verify that certificate with the
   smallest independent verifier available on a cheaper tier when practical.

This policy makes personal compute a targeted escalation path, not the default
test loop, while preserving identical mathematical proof obligations.

## GitHub-hosted certification

`.github/workflows/markov-certification.yml` runs on pushes to the active
prototype branch, pull requests, and manual dispatch. It installs the pinned
Z3 package and runs only the direct SysML structural, controller-class, and Z3
pipeline tests.

The job verifies the installed `z3-solver` distribution before testing. A
missing or different solver version fails the job rather than turning Z3 tests
into skips.

## Optional direct workstation certification

`.github/workflows/markov-workstation.yml` is manual-dispatch only. It targets
a runner with all of these labels:

- `self-hosted`
- `linux`
- `x64`
- `clarity-workstation`
- `gpu`

It records CPU, memory, and NVIDIA GPU visibility, creates an isolated virtual
environment under the GitHub runner temporary directory, installs the same
pinned dependencies, and runs the same proof tests. Future authorized heavy or
GPU-specific tests may be added to this manual workflow without placing them on
every hosted CI run.

This direct workflow requires a runner registered to this work-side repository.
It is not the active personal bridge. The active Tier 3 route is the
`learningformality/clarity-compute` queue described in
`docs/PERSONAL_RUNNER_BRIDGE_HANDOFF.md`.

## Optional direct workstation registration

An owner with permission to manage Actions runners must open the repository or
organization settings and select **Actions → Runners → New self-hosted runner**.
Choose Linux and x64, then run the download and registration commands GitHub
generates on the workstation. Add the custom labels
`clarity-workstation,gpu` during registration.

The registration command uses a short-lived token generated by GitHub. Never
commit that token, a personal access token, or runner credentials to this
repository. The runner application must be active for manual jobs to start; it
may be installed as a service using the instructions GitHub displays.

After registration, open **Actions → Markov Workstation Certification** and
select **Run workflow**. The workflow never runs automatically for pull
requests, which prevents unreviewed contributed code from being sent to the
workstation.
