import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from fetch_em_all import cli, doctor
from tests.test_engine import Handler, Run


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(cli, "_keep_awake"):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Commands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Run.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        Run.tearDownClass.__func__(cls)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        Handler.hits.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, text):
        path = self.dir / "fetch.yaml"
        path.write_text(textwrap.dedent(text).lstrip())
        return str(path)

    def test_plan_lists_jobs(self):
        cfg = self.config(f"""
            out: out
            sources:
              - name: a
                urls: [https://ex.org/1.pdf, {{url: https://ex.org/2.pdf, fallback: "{self.base}/pdf/2"}}]
            """)
        code, out, _ = call("plan", cfg, "--list")
        self.assertEqual(code, 0)
        self.assertIn("2 files from 1 sources; 0 already in", out)
        self.assertIn("a: 2 (1 with a fallback)", out)
        self.assertIn("todo  https://ex.org/1.pdf  ->  a/1.pdf", out)

    def test_config_problems_exit_2(self):
        code, _, err = call("plan", self.config("sources: []\n"))
        self.assertEqual(code, 2)
        self.assertIn("out: missing", err)

    def test_run_fallback_only(self):
        cfg = self.config(f"""
            out: out
            fallback_pace_seconds: 0
            pace_seconds: 0
            sources:
              - name: a
                urls: [{{url: https://ex.org/1.pdf, fallback: "{self.base}/pdf/1"}}]
            """)
        code, out, _ = call("run", cfg, "--fallback-only")
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "out" / "a" / "1.pdf").read_bytes().startswith(b"%PDF"))
        self.assertIn("1 filed, 0 failed", out)
        code, out, _ = call("run", cfg, "--fallback-only")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "Nothing to fetch."))

    def test_browser_needs_macos(self):
        cfg = self.config("out: out\nsources: [{name: a, urls: [https://ex.org/1.pdf]}]\n")
        with mock.patch.object(sys, "platform", "linux"):
            code, _, err = call("run", cfg)
        self.assertEqual(code, 2)
        self.assertIn("needs macOS", err)

    def test_doctor_off_macos(self):
        with mock.patch.object(sys, "platform", "linux"):
            results = doctor.checks()
        self.assertEqual(results[0][:2], (doctor.BAD, "macOS"))
        self.assertFalse(any("installed" in item for _, item, _ in results))


if __name__ == "__main__":
    unittest.main()
