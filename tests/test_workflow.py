import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/gate.yml"
CALLER = ROOT / "examples/caller.yml"
PAT = "secrets.PR_GATE_PUSH_TOKEN"
AGENT = "anthropics/claude-code-action@"
GATE_CHECKOUT = "repository: Hus-Mek/pr-gate-action"
# A local merge guard matches check names by their last " / " segment, so these ids are an API.
JOB_IDS = ("attribution", "classify", "review-pass", "review", "fix", "push-fix")
PERMISSION_RANK = {"none": 0, "read": 1, "write": 2}


def jobs(text):
    """Job name -> its body, from the two-space-indented keys under `jobs:`."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def header(text):
    """Everything before the top-level `jobs:` key."""
    return text.split("\njobs:\n", 1)[0]


def permissions(block, indent):
    """{scope: level} from a `permissions:` mapping at the given indent, or {} if there is none."""
    match = re.search(rf"^{indent}permissions:\n((?:{indent}  [\w-]+: \w+\n)+)", block, flags=re.MULTILINE)
    if not match:
        return {}
    return dict(line.strip().split(": ") for line in match.group(1).splitlines())


class PushTokenIsolationTest(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs(WORKFLOW.read_text(encoding="utf-8"))

    def test_push_runs_in_a_job_without_the_fix_agent_and_without_hooks(self):
        # The agent and the repo's tests can plant .git/hooks/pre-push or leave a process behind;
        # either would see the PAT if the push ran in the same job.
        pushers = {name: body for name, body in self.jobs.items() if 'push "$GITHUB_SERVER_URL' in body}

        self.assertTrue(pushers)
        for name, body in pushers.items():
            self.assertNotIn(AGENT, body, name)
            self.assertNotIn("pytest", body, name)
            self.assertIn("core.hooksPath=/dev/null", body, name)

    def test_agent_jobs_use_the_pat_only_before_the_agent_runs(self):
        for name, body in self.jobs.items():
            if AGENT in body and PAT in body:
                self.assertLess(body.rindex(PAT), body.index(AGENT), name)


class ReusableWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)

    def test_is_called_only_through_workflow_call(self):
        trigger = header(self.text).split("\non:\n", 1)[1]

        self.assertRegex(trigger, r"^  workflow_call:\n")
        self.assertNotRegex(trigger, r"(?m)^  (pull_request|push|pull_request_target):")

    def test_declares_gate_ref_and_optional_secrets(self):
        call = header(self.text)

        self.assertRegex(call, r"gate_ref:\n(?:        .*\n)*        default: v1\n")
        for secret in ("CLAUDE_CODE_OAUTH_TOKEN", "PR_GATE_PUSH_TOKEN"):
            self.assertRegex(call, rf"{secret}:\n(?:        .*\n)*        required: false\n", secret)

    def test_defines_no_top_level_concurrency(self):
        # The caller owns concurrency; a second group here would collide with it.
        self.assertNotRegex(header(self.text), r"(?m)^concurrency:")

    def test_keeps_the_job_ids_a_merge_guard_matches(self):
        self.assertEqual(tuple(self.jobs), JOB_IDS)

    def test_gate_scripts_come_only_from_pr_gate_action(self):
        # The PR's own .github/pr-gate files must never supply gate code.
        self.assertNotIn(".github/pr-gate/$f", self.text)
        self.assertNotIn("cp \".github/pr-gate", self.text)
        for name, body in self.jobs.items():
            if "$RUNNER_TEMP/gate/" in body:
                self.assertIn(GATE_CHECKOUT, body, name)
                self.assertIn("ref: ${{ inputs.gate_ref }}", body, name)
                self.assertIn('rm -rf "$src"', body, name)

    def test_gate_checkout_happens_after_the_callers_checkout(self):
        # A later checkout at the workspace root would wipe the nested gate checkout.
        for name, body in self.jobs.items():
            if GATE_CHECKOUT in body:
                self.assertLess(body.index("uses: actions/checkout@"), body.index(GATE_CHECKOUT), name)

    def test_policy_comes_from_the_base_branch_or_the_default_never_the_pr(self):
        for name, body in self.jobs.items():
            if GATE_CHECKOUT not in body:
                continue
            self.assertIn('git show "$ref:.github/pr-gate/risk-policy.toml"', body, name)
            self.assertIn('cp "$src/gate/risk-policy.toml"', body, name)
            self.assertNotIn("cp .github/pr-gate/risk-policy.toml", body, name)
            # A failed base fetch must stop the step, not fall back to the PR's merge commit.
            self.assertNotIn("&& ref=FETCH_HEAD", body, name)


class FixJobTest(unittest.TestCase):
    def setUp(self):
        self.fix = jobs(WORKFLOW.read_text(encoding="utf-8"))["fix"]

    def test_installs_the_repos_dependencies_before_the_agent_runs(self):
        # Without them the agent cannot run the tests it is told to make pass, and gives up.
        self.assertLess(self.fix.index("uv sync --frozen"), self.fix.index(AGENT))

    def test_agent_may_run_the_tests_through_uv(self):
        self.assertIn("Bash(uv run pytest:*)", self.fix)
        self.assertIn("Bash(uv run python -m pytest:*)", self.fix)

    def test_agent_authors_the_fix_itself(self):
        # A repo's agent instructions may say to delegate code to a lane; none exists in CI.
        self.assertIn("even if the repo's agent instructions", self.fix)

    def test_a_refused_fix_keeps_the_agent_log(self):
        self.assertIn('echo "refused=true" >> "$GITHUB_OUTPUT"', self.fix)
        upload = self.fix.index("if: steps.verify.outputs.refused == 'true'")
        self.assertIn("steps.claude.outputs.execution_file", self.fix[upload:])


class CallerPermissionsTest(unittest.TestCase):
    def test_every_job_asks_for_no_more_than_the_example_caller_grants(self):
        # A called workflow's jobs cannot exceed the caller's GITHUB_TOKEN permissions.
        granted = permissions(header(CALLER.read_text(encoding="utf-8")), "")
        text = WORKFLOW.read_text(encoding="utf-8")
        requests = {"(top level)": permissions(header(text), "")}
        requests.update({name: permissions(body, "    ") for name, body in jobs(text).items()})

        for name, asked in requests.items():
            for scope, level in asked.items():
                have = PERMISSION_RANK[granted.get(scope, "none")]
                self.assertGreaterEqual(have, PERMISSION_RANK[level], f"{name}: {scope}: {level}")

    def test_example_caller_uses_the_reusable_workflow_and_passes_secrets_by_name(self):
        # `secrets: inherit` stops at the owner boundary, so a caller in another owner would run
        # the gate with empty secrets and the review would skip while reporting pass.
        text = CALLER.read_text(encoding="utf-8")

        self.assertIn("uses: Hus-Mek/pr-gate-action/.github/workflows/gate.yml@v1", text)
        self.assertNotIn("secrets: inherit", text)
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "PR_GATE_PUSH_TOKEN"):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", text)
        self.assertRegex(text, r"(?m)^concurrency:")


if __name__ == "__main__":
    unittest.main()
