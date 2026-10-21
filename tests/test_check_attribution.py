import unittest

from gate.check_attribution import find_attribution, parse_log


class FindAttributionTest(unittest.TestCase):
    def test_flags_co_authored_by_claude(self):
        texts = {"abc123": "feat: x\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"}

        self.assertEqual(find_attribution(texts), ("abc123",))

    def test_flags_generated_with_footer_in_pr_body(self):
        texts = {"PR body": "Summary\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"}

        self.assertEqual(find_attribution(texts), ("PR body",))

    def test_human_co_author_is_allowed(self):
        texts = {"abc123": "fix: y\n\nCo-Authored-By: A Reviewer <reviewer@example.com>"}

        self.assertEqual(find_attribution(texts), ())

    def test_code_mentioning_the_action_is_allowed(self):
        texts = {"abc123": "ci: pin anthropics/claude-code-action to v1.0.253"}

        self.assertEqual(find_attribution(texts), ())


class ParseLogTest(unittest.TestCase):
    def test_splits_records_by_separator(self):
        raw = "aaa\x00first message\n\x1ebbb\x00second\x1e"

        self.assertEqual(parse_log(raw), {"aaa": "first message\n", "bbb": "second"})


if __name__ == "__main__":
    unittest.main()
