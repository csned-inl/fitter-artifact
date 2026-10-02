THE PLAN IS TO GIVE THE MINIMAL AMOUNT OF INFORMATION TO Z3 TO PROVE THE BUFFERED CONTROLLER IS MARKOV. IF YOU BEGIN TO DO OTHERWISE STOP PRODUCTION IMMEDIATELY AND CALL FOR MY HELP.

**AUTHORITATIVE MODEL SOURCE RULE:** SysML model semantics come only from `csned-inl/clarity-standalone`. `fitter-artifact`, the `learningformality` mirrors, generated certificates, backups, exported SMV, simulator traces, and all prior verification work are non-authoritative and must never be treated as model ground truth. Any disagreement stops production and requires user review.

# Personal GitHub runner bridge handoff

## Audience and objective

This document is addressed to the agent operating the user's personal machines and the GitHub account `learningformality`.

Build a GitHub-mediated compute bridge with this information flow:

1. The work-side agent writes code or a structured compute request to GitHub.
2. GitHub Actions assigns the request to one or more self-hosted runners on the user's personal hardware.
3. The personal runner executes the requested, version-pinned task.
4. The runner returns a compact status plus complete machine-readable results to GitHub.
5. The work-side agent reads those results through its GitHub integration and continues development.

There must be no network connection, remote shell, shared filesystem, or other command channel between the personal hardware and the locked-down work laptop. GitHub is the only rendezvous point. How the personal-side agent allocates machines, accelerators, schedulers, or parallel workers is deliberately outside this document.

This bridge is an execution mechanism only. It does not change the Markov-certification goal, authorize simulator reconstruction, or change which repository is authoritative for SysML model semantics.

This bridge is Tier 3 of the project's testing strategy. The work-side private
VM handles immediate dependency-light checks, GitHub-hosted runners handle
ordinary pinned CI and Z3 tests, and personal hardware handles only unavailable
capabilities or materially heavy work. Because bridge dispatch has measurable
fixed latency, combine related heavy checks into one request rather than using
the bridge as an interactive shell.

## Hard boundaries

- Never attempt to access the work laptop or its filesystem.
- Never request, copy, or reuse work-laptop credentials.
- Never treat a personal mirror as an independent semantic authority.
- Never turn a timeout, solver `unknown`, process exit, or successful test invocation into a mathematical proof result.
- Never accept arbitrary shell text from a GitHub comment.
- Never run self-hosted jobs from fork pull requests or untrusted actors.
- Never rewrite a divergent Git branch without first preserving it and obtaining user approval.
- Fail closed on an unrecognized repository, task, argument, commit, result schema, source hash, or model-source mismatch.

## Known repository map

As observed on 2026-10-02, the relevant pairs are:

| Role | Work-side repository | Personal mirror |
|---|---|---|
| Certification implementation | `csned-inl/fitter-artifact` | `learningformality/fitter-artifact` |
| Authoritative SysML models | `csned-inl/clarity-standalone` | `learningformality/clarity-standalone` |

The work-side repositories are public. The personal mirrors are private. The work-side GitHub integration can currently read and push the two personal mirrors, but it does not have repository-administration authority there. The personal-side agent must perform runner registration, repository creation, ruleset configuration, secret configuration, and other administrative work.

The two personal `main` branches currently have the same commit IDs as the corresponding work-side `main` branches:

| Repository | Current shared `main` commit |
|---|---|
| `fitter-artifact` | `f968a86bdc311da783b5ad81400e523047b31827` |
| `clarity-standalone` | `aa3c6ae4640e3bcadcf66542eb0124e6c57e2bb3` |

Do not merge feature branches into `main` merely to call the mirrors “updated.” Preserve branch topology and commit identity.

### Work branches currently absent from the personal `fitter-artifact` mirror

| Branch | Work-side commit at observation time |
|---|---|
| `codex/finite-history-mdp-proof` | `fcdb98eb57b74681ae5872bca7236ff10ccbadec` |
| `codex/thermostat-symbolic-machine` | `f4d15e636ec7670b8e8c95e0cdcabc9960dffe2d` |
| `codex/workstation-primary` | `62bab1d1c4baea0b055041742bd79e34fbe96a10` |

The personal mirror already has `handoff/fitter-artifact-2026-09-29` at `62bab1d1c4baea0b055041742bd79e34fbe96a10`.

### Work branches currently absent from the personal `clarity-standalone` mirror

| Branch | Work-side commit at observation time |
|---|---|
| `codex/optional-pytorch-analytic` | `23ba0c854842b9893953372f8007fc455114d7b9` |
| `codex/plc-e001-structured-text` | `97b2da34e82a88108b8f063e3a5ee3917785036e` |
| `codex/workstation-reporting` | `e2517bb34704d6c49292d465286a952c728a34af` |
| `workstation-results` | `a295ae74fe12079ce671c4a74c7fe7ac99f23bb0` |

`workstation-results` is generated evidence, not source authority. Mirror it only to preserve existing history.

The commit IDs above are observations, not permanent configuration. Fetch both repositories again immediately before synchronization and use the then-current remote refs.

## Phase 1: synchronize the personal mirrors without losing history

### Required policy

For each repository pair:

1. Clone the personal repository as `origin`.
2. Add the work repository as a read-only remote named `work`.
3. Fetch all branch refs from both remotes.
4. Compare each same-named branch by full commit ID and merge-base relationship.
5. Create missing personal branches at the exact work-side commits.
6. Fast-forward a personal branch only when its current tip is an ancestor of the work-side tip.
7. If branches diverge, preserve both. Do not merge, rebase, or force-push automatically. Create a clearly named work snapshot branch and report the divergence to the user.
8. Verify the resulting remote branch IDs with `git ls-remote`.

Commit identity matters. A faithful mirror of a branch must point to the same Git commit SHA, not merely contain a later copy of the same files.

### Suggested synchronization procedure

Use separate working directories. The following is the intended shape; adapt paths without changing the safety rules.

```bash
git clone git@github.com:learningformality/fitter-artifact.git
cd fitter-artifact
git remote add work https://github.com/csned-inl/fitter-artifact.git
git fetch --prune origin '+refs/heads/*:refs/remotes/origin/*'
git fetch --prune work '+refs/heads/*:refs/remotes/work/*'
git branch -r --format='%(refname:short) %(objectname)'
```

For a branch missing from `origin`, publish the exact work ref without rewriting anything:

```bash
git push origin \
  refs/remotes/work/codex/thermostat-symbolic-machine:refs/heads/codex/thermostat-symbolic-machine
```

Repeat for each verified missing branch. Apply the same procedure to `clarity-standalone`.

For an existing same-named personal branch, classify it before changing it:

```bash
git merge-base --is-ancestor \
  refs/remotes/origin/BRANCH \
  refs/remotes/work/BRANCH
```

- Exit 0 means a non-force fast-forward is permitted.
- If the tips are equal, do nothing.
- If the personal tip is not an ancestor, treat the branch as divergent.

For divergence, publish the work tip under a non-destructive snapshot ref such as:

```text
work-snapshot/2026-10-02/BRANCH
```

Then give the user the two tip SHAs and the commits unique to each side. Do not decide privately that either history is disposable.

### Post-sync verification

Produce a table containing, for every synchronized branch:

- work repository and branch;
- work tip SHA;
- personal repository and branch;
- personal tip SHA;
- equality result;
- action taken: unchanged, created, fast-forwarded, or divergence preserved.

All intended mirror rows must end with equal full commit IDs. A tree hash or file comparison alone is insufficient.

### Future synchronization

Implement an idempotent personal-side mirror audit. It may run manually or on a schedule chosen by the user and personal-side agent. It must:

- fetch without rewriting;
- create absent refs;
- fast-forward only when ancestry proves safety;
- fail and report on divergence;
- never merge feature branches into `main`;
- never promote generated results to source authority.

The runner does not need the private mirrors to read the current work-side code because both work repositories are public. For proof work, prefer cloning the authoritative work repository directly at an exact 40-character commit SHA. The personal mirrors are continuity copies and convenient execution mirrors, not replacements for source identity.

## Phase 2: create the private compute-control repository

Create a new private repository owned by `learningformality`. The recommended name is:

```text
learningformality/clarity-compute
```

Its purpose is control and evidence transport. Do not turn it into another copy of either project.

Configure it as follows:

- Default branch: `main`.
- Protect `main` against force pushes and deletion.
- Require review for changes under `.github/workflows/` and the task-policy files.
- Add `csned-inl` as a collaborator with permission to comment, read results, and contribute bridge changes.
- Explicitly authorize the ChatGPT/Codex GitHub integration for this repository. Access inherited by a human collaborator does not necessarily authorize a separately installed GitHub App.
- Create an unprotected or Actions-writable branch named `compute-results` used only for append-only result bundles.
- Create one permanent issue titled `Compute Queue` and record its issue number.
- Do not enable self-hosted execution on `pull_request` or `pull_request_target`.

The work-side integration presently supports repository commits, ref updates, issue comments, issue-comment reads, and commit-status reads. It does not presently expose a dependable workflow-dispatch or Actions-artifact-download operation. Therefore, the command channel must be a structured comment on the permanent issue, and complete results must be materialized as ordinary GitHub repository files in `compute-results`.

## Phase 3: register the runner endpoint

Register one or more repository-level self-hosted runners with `learningformality/clarity-compute` under **Settings → Actions → Runners**.

The only required custom capability label for this protocol is:

```text
personal-compute
```

Additional labels and the mapping from jobs to physical machines are personal-side implementation choices. The bridge protocol must not assume a particular GPU, CPU count, scheduler, operating-system layout, or number of hosts.

Run the GitHub runner under an isolation boundary chosen by the user and personal-side agent. Checked-out project code is executable code. The runner environment must not expose unrelated personal credentials or files merely because the repositories themselves are nonsensitive.

## Phase 4: implement the command protocol

### Trigger

Place the trusted workflow on protected `main`. Trigger it only on newly created comments for the permanent `Compute Queue` issue.

Before any self-hosted job is scheduled, a GitHub-hosted validation job must verify all of the following:

- exact repository: `learningformality/clarity-compute`;
- exact issue number;
- exact permitted GitHub actor, initially `csned-inl` or `learningformality`;
- protocol marker `/compute-v1`;
- valid JSON body;
- unique request ID matching a conservative character pattern;
- recognized task name;
- recognized source repository;
- full 40-character hexadecimal commit IDs;
- arguments conforming to the task-specific schema;
- request ID has not already been executed.

An invalid request must be rejected without assigning anything to a self-hosted runner.

### Request format

The work-side agent will post comments in this form:

```text
/compute-v1
{
  "request_id": "20261002T223000Z-markov-001",
  "task": "markov-certification",
  "inputs": {
    "code": {
      "repository": "csned-inl/fitter-artifact",
      "sha": "FULL_40_CHARACTER_COMMIT_SHA"
    },
    "model_authority": {
      "repository": "csned-inl/clarity-standalone",
      "sha": "FULL_40_CHARACTER_COMMIT_SHA"
    }
  },
  "arguments": {}
}
```

Branches and tags may be displayed for humans, but the executed identity must always be a resolved full commit SHA. The workflow must verify that each requested commit exists in the named repository.

### Task policy

Do not interpret `task` or `arguments` as shell fragments. Map a task name to a version-controlled argv array or trusted script in the protected bridge configuration.

Start with these tasks:

| Task | Purpose |
|---|---|
| `ping` | Prove the command, runner, and result path work without project execution. |
| `repo-sync-audit` | Report work/personal branch equality without modifying repositories. |
| `markov-certification` | Run the direct structural, controller-class, and Z3 certification tests. |
| `markov-structural` | Run only the one-way structural Markov checker tests. |
| `markov-z3` | Run only the direct compact Z3 fallback tests. |
| `project-tests` | Run a specifically configured test suite from an allowlisted source commit. |

New tasks may be added later by changing the reviewed task policy. This provides broad future capability without accepting arbitrary public commands.

For the current Markov implementation, `markov-certification` is equivalent to the test scope in `.github/workflows/markov-certification.yml` at the requested `fitter-artifact` commit. It must use the dependency versions pinned by that commit. It must not silently substitute a newer solver or a simulator-based proof path.

## Phase 5: separate validation, execution, and publication

Implement three logical jobs:

1. **Validate on GitHub-hosted infrastructure.** Parse and validate the request. Invalid requests never reach personal hardware.
2. **Execute on the self-hosted runner.** Use the validated repository, commit, task, and typed arguments. Give this job no repository-write permission and no personal secrets. Clone public source repositories directly and check out detached exact commits.
3. **Publish on GitHub-hosted infrastructure.** Download the bounded result bundle without executing it, append it to `compute-results`, and post the compact response comment.

The publishing job, not project code running on personal hardware, should possess `contents: write` and `issues: write`. Give each job the minimum `GITHUB_TOKEN` permissions it requires.

Use a timeout for every execution. Ensure a failure, cancellation, or timeout still produces a result record. Prevent two executions with the same request ID. Preserve logs needed for diagnosis but impose explicit size limits.

## Phase 6: result protocol

### Result location

Write each complete result beneath this immutable path on `compute-results`:

```text
results/<request_id>/
```

At minimum include:

```text
result.json
stdout.log
stderr.log
checksums.sha256
```

Include proof certificates, counterexamples, solver output, and other task artifacts when produced. Do not overwrite an existing request directory. A repeated request ID must return the existing result or be rejected.

### Required result fields

`result.json` must contain:

- protocol version;
- request ID;
- task name and task-policy revision;
- exact source repositories and commit SHAs;
- source tree hashes;
- fixed model-authority repository and commit, when applicable;
- runner class/labels without exposing personal network details;
- operating-system and relevant tool versions;
- dependency-lock hash;
- start and finish timestamps;
- duration;
- process exit status;
- infrastructure status;
- task status;
- proof status;
- artifact names, sizes, and SHA-256 hashes;
- a deterministic hash of the complete result bundle before publication.

`result.json` cannot contain the Git commit SHA of the commit that contains
`result.json`, because that would be a circular hash dependency. The publishing
job records the resulting Git commit SHA in the response comment after the
result bundle has been committed.

Use separate status fields. At minimum:

```text
infrastructure_status: SUCCESS | FAILURE | CANCELLED | TIMED_OUT
task_status: SUCCESS | FAILURE | NOT_RUN
proof_status: CERTIFIED | COUNTEREXAMPLE | INCONCLUSIVE | NOT_APPLICABLE | ERROR
```

These meanings are strict:

- `CERTIFIED` requires the proof method's positive certificate conditions.
- `COUNTEREXAMPLE` requires a valid model witness under the stated contract.
- Solver `unknown`, timeout, resource exhaustion, missing dependency, or incomplete execution is `INCONCLUSIVE` or `ERROR`, never `CERTIFIED` and never a counterexample.
- A process exit code of zero only means the requested program completed according to its own interface. It does not independently establish `CERTIFIED`.

### Response comment

Post a short issue comment beginning with `/compute-result-v1` and containing compact JSON with:

- request ID;
- infrastructure, task, and proof statuses;
- exact input commits;
- result-branch commit;
- path to `result.json`;
- total duration;
- hashes of principal certificates.

The work-side agent must be able to fetch `result.json` and the named files through ordinary GitHub repository-file access. An Actions-only artifact is useful for humans but is not sufficient for this bridge.

## Source-authority enforcement for proof jobs

For every Markov proof request:

1. Record `csned-inl/clarity-standalone` as the model-authority repository.
2. Resolve and check out the exact requested authority commit.
3. Check the model paths and hashes expected by the certification implementation.
4. Treat any mismatch between the certification input and the authoritative standalone model as a hard error.
5. Do not fall back to a model copy inside `fitter-artifact`, the personal mirrors, an SMV export, a simulator trace, or a generated certificate.

A personal mirror may be used as a byte cache only if the workflow verifies that it contains the exact same commit SHA and object content as the authoritative work repository. Its repository name must never replace the authoritative source identity in the certificate.

## Acceptance sequence

Complete these checks in order:

1. **Mirror audit:** all intended personal mirror branches equal the current work-side branch SHAs, with no history loss.
2. **Visibility:** the work-side GitHub integration can read `learningformality/clarity-compute`, read its queue issue, add a comment, and read `compute-results`.
3. **Runner handshake:** `/compute-v1` with task `ping` yields a valid `/compute-result-v1` response and committed `result.json`.
4. **Rejection:** a malformed request, unknown actor, unknown task, moving branch name, and duplicate request ID are rejected without scheduling a self-hosted job.
5. **Failure transport:** a deliberately failing harmless task still returns structured failure evidence.
6. **Timeout transport:** a deliberately bounded sleep demonstrates `TIMED_OUT`, not proof success or counterexample.
7. **Project test:** run `markov-certification` against an exact known `fitter-artifact` commit and exact `clarity-standalone` authority commit.
8. **Round trip:** the work-side agent retrieves `result.json`, checks its hashes and commits, and accurately reports the outcome to the user.

Do not enable unattended heavy execution until the first six bridge checks pass.

## Information to return to the work-side agent

When setup is complete, give the user this receipt so it can be pasted into the work-side conversation:

```json
{
  "protocol": "personal-runner-bridge/v1",
  "control_repository": "learningformality/clarity-compute",
  "default_branch": "main",
  "results_branch": "compute-results",
  "queue_issue_number": 0,
  "allowed_request_actors": ["csned-inl", "learningformality"],
  "runner_required_labels": ["self-hosted", "personal-compute"],
  "available_tasks": [],
  "workflow_commit": "FULL_40_CHARACTER_SHA",
  "mirror_audit": {
    "learningformality/fitter-artifact": "PASS_OR_EXPLANATION",
    "learningformality/clarity-standalone": "PASS_OR_EXPLANATION"
  },
  "ping_request_id": "REQUEST_ID",
  "ping_result_commit": "FULL_40_CHARACTER_SHA",
  "ping_result_path": "results/REQUEST_ID/result.json",
  "notes": []
}
```

Do not place registration tokens, personal access tokens, runner credentials, private hostnames, IP addresses, or secrets in this receipt.

## Completion condition

This setup is complete only when the work-side agent can place a structured request in GitHub, personal hardware executes it without any connection to the work laptop, a durable structured result returns to GitHub, and the work-side agent can retrieve and validate that result using its existing GitHub integration.

The bridge is not complete merely because a self-hosted runner appears as “online.”
