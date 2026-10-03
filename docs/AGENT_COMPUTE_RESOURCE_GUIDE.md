# Three-tier compute and GitHub runner guide for agents

## Purpose

This document tells an AI agent how to choose among three available execution environments and how to use a GitHub-mediated personal-compute bridge safely and efficiently.

The environments are:

1. the agent's private execution VM;
2. GitHub-hosted Actions runners;
3. one or more self-hosted runners on user-controlled personal hardware.

The user's managed workstation is only a browser endpoint. It is not a compute target, relay, or network bridge. Personal hardware and the managed workstation must never connect directly. GitHub is the sole rendezvous point between the work-side agent and personal compute.

## Resource summary

| Resource | Primary advantage | Primary limitation | Default use |
|---|---|---|---|
| Agent private VM | Immediate feedback and direct file access | May lack packages, hardware, network access, or persistence | Static analysis and small dependency-light tests |
| GitHub-hosted runner | Clean, reproducible environment with installable dependencies | Startup latency, bounded resources, ephemeral state | Ordinary CI, solver tests, and moderate CPU work |
| Personal self-hosted runner | User-controlled software and potentially extensive compute | GitHub orchestration latency and greater security responsibility | Heavy, specialized, GPU, memory-intensive, or long-running work |

Use the cheapest environment that can run the required check faithfully. Escalate because of a concrete capability or scale requirement, not merely because another environment exists.

## Tier 1: agent private VM

### Strengths

- Lowest interaction latency.
- Direct access to the current working files.
- Good for rapid inspection and iteration.
- No GitHub Actions queue or publication delay.
- Suitable for deterministic local transformations and targeted experiments.

### Typical work

- source inspection and diffs;
- parsing and syntax checks;
- formatting and linting;
- static dependency analysis;
- solver-independent unit tests;
- small deterministic scripts;
- query construction and certificate inspection;
- review of generated workflow and configuration files.

### Limitations

The VM may lack specific system packages, language runtimes, solvers, GPU support, memory, network access, or permissions. It may also be transient.

An unavailable dependency is not a test result. A skipped test is not a pass. If the VM cannot faithfully run a required check, preserve the exact check and escalate it to the next suitable tier.

Do not weaken assertions, silently replace dependencies, or substitute a smaller problem solely to make the local environment pass.

## Tier 2: GitHub-hosted runners

### Strengths

- Clean environment for every workflow run.
- Dependencies can normally be installed from pinned manifests.
- Good reproduction of a fresh-user installation.
- Native commit checks, logs, statuses, caching, matrices, and artifacts.
- Usually the correct default for ordinary continuous integration.

### Typical work

- full unit and integration suites;
- pinned SMT or theorem-prover invocations;
- clean-build verification;
- moderate CPU parallelism;
- multi-version runtime matrices;
- packaging and installation tests;
- validation before spending personal compute.

### Limitations

- Fixed orchestration and startup latency.
- Ephemeral filesystem and process state.
- Runtime, memory, storage, and concurrency limits.
- Limited or unavailable specialized hardware.
- Network and organizational policy restrictions may apply.

Prefer GitHub-hosted execution over personal hardware whenever it can run the required check faithfully and within reasonable time.

## Tier 3: personal self-hosted compute

### Strengths

- User-controlled operating system, tools, dependencies, and hardware.
- Can expose specialized accelerators or large CPU and memory resources.
- Can support long-running or highly parallel workloads.
- Can use environments prepared specifically for the task.

### Typical work

- GPU workloads;
- large-memory analyses;
- substantial CPU parallelism;
- long-running proof search or model checking;
- large data processing;
- hardware-specific reproduction;
- work blocked by hosted-runner limitations.

### Limitations

- GitHub event, dispatch, runner pickup, and publication create fixed latency.
- A millisecond computation can still require tens of seconds end to end.
- The user is responsible for runner isolation, maintenance, and resource allocation.
- Workflow code is executable authority over the runner environment.
- Persistent runners can retain damage or state from unsafe jobs.

Treat this tier as targeted escalation, not as an interactive shell. Batch related work into one request so the computation dominates the orchestration delay.

## Test-routing algorithm

For every change, use this sequence:

1. Identify the exact claims that need checking.
2. Identify their software, hardware, memory, time, and network requirements.
3. Run all applicable checks immediately in the private VM.
4. Run the normal pinned suite on GitHub-hosted infrastructure.
5. Stop and diagnose ordinary failures before escalating.
6. Send only unavailable, specialized, or materially heavy work to personal compute.
7. Batch compatible personal-compute work into one version-pinned job.
8. Retrieve and validate durable results before making a claim to the user.

Escalation is justified when:

- a required dependency cannot be installed locally;
- clean-environment reproduction matters;
- hosted resources are insufficient;
- specialized hardware is required;
- execution time would unreasonably occupy a cheaper tier;
- the user requests reproduction on personal hardware.

Escalation is not justified merely because:

- a local test failed;
- a solver returned `unknown`;
- a timeout occurred without analysis;
- the agent wants a more favorable result;
- the higher tier happens to be available.

## GitHub as the control plane

Use a dedicated private GitHub control repository for the personal-compute bridge. Keep source repositories and the control repository conceptually separate.

The control repository should contain:

- a protected default branch with trusted workflow definitions;
- a version-controlled task policy;
- one permanent issue used as the compute queue;
- an append-only results branch;
- no unrelated project secrets or personal data.

The work-side agent submits a structured issue comment. GitHub validates and dispatches the request. Personal hardware executes it. GitHub publishes a structured result comment and a durable result bundle. The work-side agent retrieves the bundle through ordinary repository access.

This arrangement requires no inbound connection to personal hardware. The self-hosted runner initiates its connection to GitHub and receives assigned work through GitHub Actions.

## Why use issue comments as commands

Issue comments work well when the agent's GitHub integration can reliably:

- read an issue;
- add a comment;
- read later comments;
- read ordinary files and commits;

but cannot reliably invoke workflow dispatch or download Actions artifacts.

The comment is a typed request, not a shell command. A fixed workflow on the protected default branch interprets it according to a narrow schema.

## Request protocol

Use a versioned marker followed by JSON:

```text
/compute-v1
{
  "request_id": "UNIQUE_REQUEST_ID",
  "task": "APPROVED_TASK_NAME",
  "inputs": {
    "code": {
      "repository": "OWNER/REPOSITORY",
      "sha": "FULL_40_CHARACTER_COMMIT_SHA"
    }
  },
  "arguments": {}
}
```

Requirements:

- `request_id` must be unique and match a conservative character pattern.
- `task` must be in a version-controlled allowlist.
- repositories must be explicitly allowed.
- execution identity must be a full commit SHA, never a moving branch or tag.
- arguments must pass a task-specific schema.
- no request value may be evaluated as shell text.
- duplicate request IDs must be rejected or return the existing immutable result.

The task policy should map task names to fixed argv arrays or trusted scripts. Add a new task by reviewing a policy change, not by allowing arbitrary commands in comments.

## Three-stage security boundary

Use three logical jobs when security matters:

1. **Hosted validation** checks the actor, queue, protocol, JSON, task, repositories, commit identities, arguments, and request uniqueness. Invalid requests never reach personal hardware.
2. **Self-hosted execution** runs the validated task without repository-write credentials or unrelated secrets.
3. **Hosted publication** receives a bounded inert result bundle, stores it in GitHub, and posts the response using narrowly scoped write credentials.

This separation primarily protects repository credentials and result integrity. It prevents tested code from sharing a job with the token used to modify results or issue comments.

It does not by itself make arbitrary code safe for the physical runner. Runner protection still requires an appropriate isolation boundary, a dedicated unprivileged identity, no unrelated credential exposure, no sensitive filesystem mounts, trusted workflow commits, source allowlists, and no self-hosted execution from untrusted pull requests.

Combining the stages can reduce latency, but it gives executed code a path closer to repository-write authority. Do not collapse the boundary merely to make a tiny ping faster.

## Latency model

Separate these measurements:

- request-publication time;
- GitHub event-recognition time;
- hosted validation queue and runtime;
- self-hosted runner pickup time;
- actual task runtime;
- hosted publication queue and runtime;
- work-side polling time.

The task's reported duration measures only part of the round trip. A job that executes in milliseconds may take tens of seconds to return because orchestration dominates.

Optimize in this order:

1. batch related tasks;
2. cache dependencies and environments;
3. avoid repeated repository setup;
4. use a matrix or internal parallelism within one request;
5. keep the runner online when authorized;
6. measure several warm and cold runs;
7. change security boundaries only after measuring a material bottleneck.

GitHub Actions is suitable for asynchronous compute jobs. It is not a replacement for a low-latency interactive shell.

## Durable result protocol

Store each result under an immutable path on a dedicated results branch:

```text
results/<request_id>/
```

Recommended files:

```text
result.json
stdout.log
stderr.log
checksums.sha256
```

Add certificates, counterexamples, reports, and other artifacts as required. Never overwrite an existing request directory.

The response issue comment should include:

- request ID;
- infrastructure status;
- task status;
- domain or proof status;
- exact input commits;
- result commit;
- result path;
- total task duration;
- principal artifact hashes.

The agent must fetch results at the exact result commit supplied by the response, not merely at the moving tip of the results branch.

## Status separation

Do not collapse infrastructure, program, and domain conclusions into one Boolean.

Use separate fields such as:

```text
infrastructure_status: SUCCESS | FAILURE | CANCELLED | TIMED_OUT
task_status: SUCCESS | FAILURE | NOT_RUN
domain_status: POSITIVE | NEGATIVE | INCONCLUSIVE | NOT_APPLICABLE | ERROR
```

For formal verification, `domain_status` may instead use terms such as:

```text
CERTIFIED | COUNTEREXAMPLE | INCONCLUSIVE | NOT_APPLICABLE | ERROR
```

Strict interpretation:

- Infrastructure success means the execution mechanism worked.
- Task success means the requested program completed according to its interface.
- Neither fact alone proves the domain claim.
- A positive proof status requires the proof method's stated certificate conditions.
- A negative verification result requires a valid counterexample under the stated contract.
- Timeout, solver `unknown`, resource exhaustion, skipped tests, incomplete coverage, or missing dependencies are inconclusive or error.

Never report a passing test harness as a mathematical certificate unless that harness actually emitted and validated the certificate required by the method.

## Checksums and immutable identity

Every remote result should record:

- source repository and full commit SHA;
- source tree SHA;
- task-policy revision;
- dependency-lock hash;
- relevant tool versions;
- runner class without sensitive network details;
- start and finish timestamps;
- artifact sizes and SHA-256 hashes;
- deterministic bundle hash.

The publishing commit SHA cannot be embedded inside a file that determines that same commit SHA without creating a circular hash dependency. Put the result commit in the response comment after publication, or use a later sidecar receipt.

After retrieving a result, independently recompute the checksums before relying on it.

## Source synchronization

When equivalent repositories exist under different accounts, preserve Git identity:

1. fetch both repositories;
2. compare same-named branch commit SHAs;
3. create missing mirror branches at the exact source commit;
4. fast-forward only when ancestry proves safety;
5. preserve and report divergence;
6. never force-push or merge divergent histories silently;
7. verify final remote refs with `git ls-remote` or the GitHub API.

Do not call a mirror current merely because its files look similar. Commit identity, branch topology, and source authority must remain explicit.

For execution, a mirror may be a transport or cache. It does not automatically become the authoritative source of semantics or data.

## Agent lifecycle and waiting

The GitHub workflow can continue after an interactive agent turn ends. Its result remains durable in GitHub.

An agent may poll during an active turn, but it must not imply that it will remain alive indefinitely. If the result does not arrive before the turn ends, report the submitted request ID and let a later turn retrieve it.

For short tasks, poll at a modest interval rather than issuing repeated duplicate requests. For long tasks, return control to the user unless an approved monitoring mechanism exists.

## Certificate-oriented workload pattern

For expensive formal or analytical work:

1. construct and inspect the problem on Tier 1;
2. run ordinary proof checks on Tier 2;
3. generate expensive certificates or witnesses on Tier 3 when required;
4. store those artifacts with exact hashes;
5. verify the certificate with the smallest independent verifier available on Tier 1 or Tier 2 when practical.

This pattern lets a powerful machine perform expensive search while a smaller trusted process checks the result.

## Operational checklist

Before submitting personal compute:

- [ ] The required check cannot be completed adequately on a cheaper tier.
- [ ] All applicable cheaper-tier tests have run.
- [ ] The source repository and full commit SHA are known.
- [ ] The task is allowlisted.
- [ ] Arguments are typed and validated.
- [ ] The request ID is unique.
- [ ] Related work is batched appropriately.
- [ ] Timeout and expected outputs are defined.

After submission:

- [ ] The request comment was created by an allowed actor.
- [ ] Exactly one result matches the request ID.
- [ ] The response identifies an exact result commit and path.
- [ ] The result files are fetched at that commit.
- [ ] Checksums are independently verified.
- [ ] Input commits and dependency versions match the request.
- [ ] Infrastructure, task, and domain statuses are reported separately.
- [ ] No timeout or skipped coverage is presented as success.

## Anti-patterns

Do not:

- send every tiny test to personal compute;
- use self-hosted Actions as an interactive REPL;
- accept shell commands from issue comments;
- execute pull-request code on a persistent personal runner;
- expose repository-write tokens to tested code;
- identify jobs by branch names instead of commits;
- trust only the moving results-branch tip;
- treat exit code zero as proof;
- treat timeout as a negative result;
- silently change dependencies between tiers;
- hide skipped tests;
- overwrite result directories;
- connect personal hardware to a managed workstation;
- assume a runner marked online has passed an end-to-end round trip.

## End-to-end acceptance test

A bridge is operational only after all of these succeed:

1. The work-side agent submits a fresh structured `ping` request.
2. GitHub validates and dispatches it.
3. Personal hardware executes it.
4. GitHub posts a matching result comment.
5. GitHub stores the result bundle at an exact commit.
6. The work-side agent retrieves the files through its own integration.
7. The work-side agent independently verifies their checksums.

Runner registration or an “online” status alone is not an end-to-end test.

## Default decision

When uncertain, use this default:

> Inspect and test locally first, use GitHub-hosted CI for ordinary reproducible execution, and reserve personal compute for capability gaps or workloads large enough to justify its latency and security boundary.
