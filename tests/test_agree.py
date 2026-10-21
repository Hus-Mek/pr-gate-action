import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from gate.agree import Finding, agree, main


def finding(path="app.py", line=10, severity="non-blocking", body="suggestion (non-blocking): x"):
    return Finding(path, line, severity, body)


class AgreeTest(unittest.TestCase):
    def test_matches_within_line_window(self):
        a = [finding(line=10)]
        b = [finding(line=13)]

        agreed, dropped = agree(a, b)

        self.assertEqual(agreed, a)
        self.assertEqual(dropped, 0)

    def test_drops_findings_outside_line_window(self):
        a = [finding(line=10)]
        b = [finding(line=14)]

        agreed, dropped = agree(a, b)

        self.assertEqual(agreed, [])
        self.assertEqual(dropped, 2)

    def test_custom_line_window_is_respected(self):
        a = [finding(line=10)]
        b = [finding(line=14)]

        agreed, _ = agree(a, b, line_window=4)

        self.assertEqual(len(agreed), 1)

    def test_different_path_never_matches(self):
        a = [finding(path="app.py", line=10)]
        b = [finding(path="lib.py", line=10)]

        agreed, dropped = agree(a, b)

        self.assertEqual(agreed, [])
        self.assertEqual(dropped, 2)

    def test_each_second_pass_finding_is_used_once(self):
        a = [finding(line=10, body="first"), finding(line=11, body="second")]
        b = [finding(line=10)]

        agreed, dropped = agree(a, b)

        self.assertEqual([f.body for f in agreed], ["first"])
        self.assertEqual(dropped, 1)

    def test_closest_pair_wins_over_input_order(self):
        a = [finding(line=10, body="far"), finding(line=13, body="near")]
        b = [finding(line=13)]

        agreed, _ = agree(a, b)

        self.assertEqual([f.body for f in agreed], ["near"])

    def test_blocking_in_second_pass_promotes_with_its_label(self):
        a = [finding(severity="non-blocking", body="suggestion (non-blocking): x")]
        b = [finding(severity="blocking", body="issue (blocking): x")]

        agreed, _ = agree(a, b)

        self.assertEqual(agreed[0].severity, "blocking")
        self.assertEqual(agreed[0].body, "issue (blocking): x")
        self.assertEqual(agreed[0].line, a[0].line)

    def test_blocking_in_first_pass_is_kept(self):
        a = [finding(severity="blocking", body="issue (blocking): a")]
        b = [finding(severity="non-blocking")]

        agreed, _ = agree(a, b)

        self.assertEqual(agreed, a)

    def test_empty_inputs(self):
        self.assertEqual(agree([], []), ([], 0))
        self.assertEqual(agree([finding()], []), ([], 1))
        self.assertEqual(agree([], [finding()]), ([], 1))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_writes_agreed_findings_and_counts(self):
        item = {"path": "app.py", "line": 5, "severity": "blocking", "body": "issue (blocking): x"}
        one = self.write("one.json", json.dumps([item]))
        two = self.write("two.json", json.dumps([item, {**item, "path": "other.py"}]))
        out_path = self.root / "agreed.json"

        code, out, _ = self.run_main(one, two, "--out", str(out_path))

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"agreed": 1, "dropped": 1})
        self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), [item])

    def test_bad_json_exits_2(self):
        bad = self.write("bad.json", "{not json")
        good = self.write("good.json", "[]")

        code, _, err = self.run_main(bad, good, "--out", str(self.root / "o.json"))

        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_missing_file_exits_2(self):
        good = self.write("good.json", "[]")

        code, _, err = self.run_main(good, str(self.root / "absent.json"), "--out", str(self.root / "o.json"))

        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_schema_violation_exits_2(self):
        bad = self.write("bad.json", json.dumps([{"path": "a.py", "line": "7", "severity": "blocking", "body": "x"}]))
        good = self.write("good.json", "[]")

        code, _, err = self.run_main(bad, good, "--out", str(self.root / "o.json"))

        self.assertEqual(code, 2)
        self.assertIn("'line'", err)

    def test_non_list_json_exits_2(self):
        bad = self.write("bad.json", json.dumps({"findings": []}))
        good = self.write("good.json", "[]")

        code, _, err = self.run_main(bad, good, "--out", str(self.root / "o.json"))

        self.assertEqual(code, 2)
        self.assertIn("JSON list", err)


if __name__ == "__main__":
    unittest.main()
