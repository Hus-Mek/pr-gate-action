# pr-gate-action

A reusable GitHub Actions workflow that gates pull requests by risk. A deterministic classifier
puts every PR in a tier, the tier decides how much AI review the PR gets, and an optional fix
loop pushes a verified fix for blocking findings. Python 3.11+ standard library only.

## What it does

| Check | What it does |
|---|---|
| `attribution` | Fails when a commit message or the PR body carries an AI-tool attribution trailer or footer. Runs on pull requests and on pushes to `main`/`master`. |
| `classify` | Classifies the merge commit against the base branch, labels the PR `risk:T0` to `risk:T3`, and posts or updates one summary comment. Fails on an empty diff (the branch is already merged) and over the size cap. |
| `review-pass` | T3 only: two independent review passes that write findings to a file instead of posting them. |
| `review` | T1 and T2: one review pass that posts inline findings. T3: posts only the findings both passes agree on. Fails while blocking findings remain on the head commit. |
| `fix` | Same-repository PRs with blocking findings: an agent makes one `fix(review): ` commit with a regression test, and the gate verifies it. |
| `push-fix` | Pushes the verified fix commit to the PR branch, so the gate runs again. |

**Tiers.** A PR's tier is the highest tier any rule triggers:

- **T0**: docs and tests only. No AI review.
- **T1**: a small source change. One review pass.
- **T2**: over the T1 size limit (150 counted lines by default), a dependency manifest, or a
  hotspot file that repeated fix commits touched. One review pass with a stronger model.
- **T3**: a critical path such as `.github/`, auth, secrets, migrations or deploy files. Two
  independent passes and the agreement filter.

**Size cap.** Lines added plus deleted in non-trivial files count toward size. Lockfiles,
minified assets, snapshots, source maps and files marked `linguist-generated` in the base branch's
`.gitattributes` do not. Above the hard cap (400 by default) `classify` fails, unless the PR has
the `size-exception` label. The label is read live, so add it and re-run the job.

**Agreement filter.** For T3, a finding is posted only when both passes report it on the same
file within three lines. A finding is blocking if either pass marked it blocking. Findings that
fall outside the diff are listed in the summary comment instead.

**Fix loop.** The `fix` job runs at most three rounds per PR, counted by `fix(review): ` commit
subjects. It only reads blocking findings posted by `github-actions[bot]`, so text other people
comment never reaches the agent. It refuses, comments, and hands the PR to a human when the agent
fails, makes other than exactly one commit, uses the wrong subject or author, edits anything under
`.github/`, adds no regression test, removes an existing assertion, or adds attribution. The job
that runs the agent never sees the push token: a separate `push-fix` job pushes the verified commit
from a bundle, with git hooks disabled and fast-forward only.

**Gate code is never the PR's code.** Every job checks out the gate scripts from this repository
at `gate_ref`. A PR cannot change the classifier, the policy or the fix-loop rules that judge it.

## Use it

Add `.github/workflows/pr-gate.yml` to the repository you want to gate (also in
[`examples/caller.yml`](examples/caller.yml)):

```yaml
name: pr-gate
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  push:
    branches: [main, master]
permissions:
  contents: read
  pull-requests: write
  issues: write
  id-token: write
concurrency:
  group: pr-gate-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
jobs:
  gate:
    uses: Hus-Mek/pr-gate-action/.github/workflows/gate.yml@v1
    with:
      gate_ref: v1
    secrets: inherit
```

- Keep `gate_ref` equal to the ref in `uses:`. The workflow file comes from `uses:`, and the
  scripts it runs come from `gate_ref`.
- The `permissions` block is required. A called workflow's jobs cannot get more than the caller
  grants, and the gate's jobs ask for `pull-requests: write`, `issues: write` and
  `id-token: write`.
- The workflow defines no concurrency group of its own; the caller's group cancels stale runs.

### Check names for branch protection

Checks appear as `<caller job id> / <gate job id>`, for example `gate / classify` and
`gate / review`. A merge guard that matches on the last ` / ` segment sees the same names as a
non-reusable install: `attribution`, `classify`, `review-pass`, `review`, `fix`, `push-fix`.

### Secrets

Both secrets are optional. Without them the gate still runs `attribution` and `classify`, and
skips the steps that need them.

| Secret | Used by | What it is |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `review-pass`, `review`, `fix` | OAuth token for the review and fix agent. Without it, no AI review runs. |
| `PR_GATE_PUSH_TOKEN` | `push-fix` | A classic personal access token with only the `repo` scope. Pushes made with the workflow's own `GITHUB_TOKEN` do not trigger new workflow runs, so the fix would never be re-reviewed. Without it, the fix loop is skipped. |

Pull requests from forks do not receive secrets, so they get `attribution` and `classify` only.

### Per-repository policy

To change tier rules, size limits or path patterns, commit a copy of
[`gate/risk-policy.toml`](gate/risk-policy.toml) to `.github/pr-gate/risk-policy.toml` in your
repository. The gate reads it from the **base branch**, so a PR that edits the policy is judged by
the policy it is changing from. Without that file, the default policy in this repository is used.

## Develop

```sh
python -m unittest discover -s tests -t .
ruff check .
actionlint .github/workflows/gate.yml .github/workflows/test.yml examples/caller.yml
```

| Path | Purpose |
|---|---|
| `gate/classify.py` | Risk classifier: `git diff --numstat` in, tier, reasons and size out. |
| `gate/risk-policy.toml` | Default risk policy. |
| `gate/check_attribution.py` | Attribution check for a commit range and the PR body. |
| `gate/agree.py` | Agreement filter for the two T3 passes. |
| `tools/review_loop.py` | Fix-round counting, fix verification, and a local `status` command. |
| `.github/workflows/gate.yml` | The reusable workflow. |
