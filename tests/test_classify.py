import contextlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gate import classify as classify_module
from gate.classify import (
    COMMENT_MARKER,
    DEFAULT_POLICY,
    Change,
    classify,
    generated_files,
    load_policy,
    parse_check_attr,
    parse_numstat,
    render_markdown,
)


def change(path, added=10, deleted=0):
    return Change(path=path, added=added, deleted=deleted)


class ParseNumstatTest(unittest.TestCase):
    def test_parses_text_and_binary_rows(self):
        raw = "12\t3\tsrc/app.py\n-\t-\tassets/logo.png\n"

        changes = parse_numstat(raw)

        self.assertEqual(changes, (change("src/app.py", 12, 3), change("assets/logo.png", 0, 0)))

    def test_rename_uses_new_path(self):
        changes = parse_numstat("1\t1\tsrc/{old.py => new.py}\n")

        self.assertEqual(changes[0].path, "src/new.py")


class ClassifyTest(unittest.TestCase):
    def test_docs_and_tests_only_is_t0(self):
        result = classify((change("README.md"), change("tests/test_x.py")), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 0)

    def test_empty_diff_is_flagged_not_called_docs_only(self):
        # A re-merged, already-landed branch diffs empty against base; the gate must fail it.
        result = classify((), frozenset(), DEFAULT_POLICY)

        self.assertEqual((result.tier, result.empty), (0, True))
        self.assertIn("empty diff", result.reasons[0])

    def test_non_empty_diff_is_not_flagged_empty(self):
        self.assertFalse(classify((change("README.md"),), frozenset(), DEFAULT_POLICY).empty)

    def test_small_source_change_is_t1(self):
        result = classify((change("src/app.py", 40, 5),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 1)

    def test_over_size_cap_is_t2(self):
        result = classify((change("src/app.py", 140, 20),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 2)
        self.assertIn("160 changed lines", result.reasons[0])

    def test_dependency_manifest_is_t2(self):
        result = classify((change("src/app.py"), change("pyproject.toml")), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 2)

    def test_hotspot_file_is_t2(self):
        result = classify((change("src/app.py"),), frozenset({"src/app.py"}), DEFAULT_POLICY)

        self.assertEqual(result.tier, 2)

    def test_critical_path_is_t3_even_when_tiny(self):
        result = classify((change(".github/workflows/ci.yml", 1, 1),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 3)

    def test_nested_critical_path_matches(self):
        result = classify((change("backend/db/migrations/0004_add.py"),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 3)

    def test_tier_is_max_of_triggers_and_keeps_every_reason(self):
        changes = (change("src/auth/login.py", 200, 0), change("package.json"))

        result = classify(changes, frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 3)
        self.assertEqual(len(result.reasons), 3)

    def test_gitattributes_is_critical(self):
        result = classify((change(".gitattributes", 1, 0),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 3)

    def test_policy_file_itself_is_critical(self):
        result = classify((change(".github/risk-policy.toml"),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.tier, 3)


class SizeCapTest(unittest.TestCase):
    def test_over_hard_cap_is_flagged_with_its_line_count(self):
        result = classify((change("src/app.py", 380, 30),), frozenset(), DEFAULT_POLICY)

        self.assertEqual((result.lines, result.over_cap), (410, True))
        self.assertTrue(any("hard cap of 400" in r for r in result.reasons))

    def test_at_the_cap_is_not_over(self):
        result = classify((change("src/app.py", 400, 0),), frozenset(), DEFAULT_POLICY)

        self.assertFalse(result.over_cap)

    def test_lockfiles_do_not_count_toward_size(self):
        changes = (change("src/app.py", 20, 0), change("uv.lock", 3000, 2000))

        result = classify(changes, frozenset(), DEFAULT_POLICY)

        self.assertEqual((result.lines, result.over_cap), (20, False))
        self.assertEqual(result.tier, 2)  # still a dependency change

    def test_generated_files_do_not_count_toward_size(self):
        changes = (change("src/app.py", 20, 0), change("src/schema_pb2.py", 900, 0))

        result = classify(changes, frozenset(), DEFAULT_POLICY, generated=frozenset({"src/schema_pb2.py"}))

        self.assertEqual((result.lines, result.over_cap, result.tier), (20, False, 1))

    def test_minified_assets_do_not_count_toward_size(self):
        result = classify((change("web/app.min.js", 5000, 0),), frozenset(), DEFAULT_POLICY)

        self.assertEqual(result.lines, 0)


class ParseCheckAttrTest(unittest.TestCase):
    def test_keeps_only_paths_marked_generated(self):
        raw = (
            "api/gen.py: linguist-generated: set\n"
            "src/app.py: linguist-generated: unspecified\n"
            "web/x.js: linguist-generated: true\n"
            "web/y.js: linguist-generated: unset\n"
        )

        self.assertEqual(parse_check_attr(raw), frozenset({"api/gen.py", "web/x.js"}))


class GeneratedFilesFromBaseTest(unittest.TestCase):
    """A PR must not be able to shrink its own size by marking files generated."""

    def test_pr_added_gitattributes_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            run("init", "-q")
            run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
            base = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], check=True, capture_output=True,
                                  text=True).stdout.strip()
            (repo / ".gitattributes").write_text("* linguist-generated\n", encoding="utf-8")
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

            with contextlib.chdir(tmp):
                generated = generated_files(("app.py",), base)

            self.assertEqual(generated, frozenset())


PRE_SIZE_CAP_POLICY = """
[size]
max_t1_lines = 150
[hotspot]
window_days = 90
min_fix_commits = 3
[paths]
trivial = ["*.md"]
dependency = ["pyproject.toml"]
critical = [".github/*"]
"""


class OlderPolicyTest(unittest.TestCase):
    def test_installed_classify_runs_beside_a_policy_without_size_cap_keys(self):
        # A repo keeps its own policy across releases, so a new classify meets an older policy.
        with tempfile.TemporaryDirectory() as tmp:
            gate = Path(tmp)
            (gate / "classify").write_bytes(Path(classify_module.__file__).read_bytes())
            (gate / "risk-policy.toml").write_text(PRE_SIZE_CAP_POLICY, encoding="utf-8")

            run = subprocess.run([sys.executable, "-I", str(gate / "classify"), "--help"],
                                 check=False, capture_output=True, text=True, encoding="utf-8")
            policy = load_policy(gate / "risk-policy.toml")

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(policy.max_lines, 400)
        self.assertIn("*.lock", policy.size_excluded)


class RenderSizeTest(unittest.TestCase):
    def test_reports_counted_lines_and_the_cap(self):
        result = classify((change("src/app.py", 40, 5),), frozenset(), DEFAULT_POLICY)

        self.assertIn("45 counted lines (hard cap 400)", render_markdown(result))

    def test_renders_marker_tier_action_and_reasons(self):
        result = classify((change("src/auth/login.py"),), frozenset(), DEFAULT_POLICY)

        body = render_markdown(result)

        self.assertTrue(body.startswith(COMMENT_MARKER))
        self.assertIn("**T3**", body)
        self.assertIn("human merges", body)
        self.assertIn("- critical path changed: src/auth/login.py", body)


if __name__ == "__main__":
    unittest.main()
