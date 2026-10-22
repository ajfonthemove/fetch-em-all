"""The run loop with a fake browser session and a local server for fallbacks (no real browser involved)."""

import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fetch_em_all import engine
from fetch_em_all.config import Job, Settings
from fetch_em_all.fallback import Fetcher, Pacer
from fetch_em_all.session import Blocked, DeadLink

PDF = b"%PDF-1.5\n" + b"x" * 4000 + b"\n%%EOF\n"


class Handler(http.server.BaseHTTPRequestHandler):
    hits = {}

    def do_GET(self):
        n = Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        if self.path.startswith("/trunc") or (self.path.startswith("/flaky") and n == 1):
            self.send_response(200)
            self.send_header("Content-Length", str(len(PDF)))
            self.end_headers()
            self.wfile.write(PDF[:100])
            self.close_connection = True
            return
        if self.path.startswith(("/pdf", "/flaky")):
            body = PDF
        elif self.path.startswith("/html"):
            body = b"<!DOCTYPE html><title>Not found</title>"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakeSession:
    """Behaves like BrowserSession.fetch: 'saves' a download into the downloads folder, or raises."""

    def __init__(self, downloads_dir, outcomes):
        self.dir, self.outcomes, self.calls = downloads_dir, outcomes, []

    def fetch(self, url, expected_name, expected_ext=""):
        self.calls.append(url)
        outcome = self.outcomes.get(url, "pdf")
        if isinstance(outcome, Exception):
            raise outcome
        path = self.dir / (expected_name or "download")
        path.write_bytes(PDF if outcome == "pdf" else b"<!DOCTYPE html>oops")
        return path


class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "dl").mkdir()
        self.settings = Settings(out=root / "out", browser="chrome", pace_seconds=0, fallback_pace_seconds=0,
                                 downloads_dir=root / "dl", port=0, start_timeout=1, finish_timeout=1)
        self.fetcher = Fetcher(Pacer(0, 0))
        Handler.hits.clear()
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def say(self, *args, **kw):
        self.log.append(" ".join(map(str, args)))

    def job(self, name, fallback=""):
        return Job(source="s", url=f"https://site.example/{name}", dest=Path("s", name), fallback=fallback)

    def lines(self, name):
        path = self.settings.out / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_browser_then_fallback_then_failure(self):
        jobs = [self.job("a.pdf"),
                self.job("b.pdf", fallback=self.base + "/pdf/b"),        # browser: dead link -> fallback
                self.job("c.pdf"),                                        # browser: dead link, no fallback
                self.job("d.pdf", fallback=self.base + "/html/d"),       # fallback serves an error page
                self.job("e.pdf")]                                        # browser saves a web page
        session = FakeSession(self.settings.downloads_dir, {
            jobs[1].url: DeadLink("Page not found"), jobs[2].url: DeadLink("Page not found"),
            jobs[3].url: DeadLink("Page not found"), jobs[4].url: "html"})
        filed, failures, attempted = engine.run(jobs, self.settings, session, self.fetcher, log=self.say)
        self.assertEqual((filed, attempted), (2, 5))
        index = {e["path"]: e for e in self.lines("index.jsonl")}
        self.assertEqual({p: e["via"] for p, e in index.items()}, {"s/a.pdf": "browser", "s/b.pdf": "fallback"})
        self.assertEqual(index["s/b.pdf"]["origin_url"], jobs[1].url)
        self.assertEqual(len(index["s/a.pdf"]["sha256"]), 64)
        failed = {f["dest"]: f["error"] for f in self.lines("failed.jsonl")}
        self.assertEqual(set(failed), {"s/c.pdf", "s/d.pdf", "s/e.pdf"})
        self.assertIn("got a web page", failed["s/d.pdf"])
        self.assertIn("got a web page", failed["s/e.pdf"])
        self.assertEqual(list(self.settings.out.rglob("*.part")), [])

    def test_resume_skips_filed(self):
        jobs = [self.job("a.pdf"), self.job("b.pdf")]
        session = FakeSession(self.settings.downloads_dir, {})
        engine.run(jobs[:1], self.settings, session, None, log=self.say)
        filed, failures, attempted = engine.run(jobs, self.settings, session, None, log=self.say)
        self.assertEqual((filed, attempted), (1, 1))
        self.assertEqual(session.calls, [jobs[0].url, jobs[1].url])
        self.assertEqual(len(self.lines("index.jsonl")), 2)

    def test_blocked_stops_the_run(self):
        jobs = [self.job("a.pdf"), self.job("b.pdf"), self.job("c.pdf")]
        session = FakeSession(self.settings.downloads_dir, {jobs[1].url: Blocked("allow multiple downloads")})
        filed, failures, attempted = engine.run(jobs, self.settings, session, self.fetcher, log=self.say)
        self.assertEqual((filed, len(failures), attempted), (1, 1, 3))
        self.assertNotIn(jobs[2].url, session.calls)
        self.assertTrue(any("Stopping: allow multiple downloads" in line for line in self.log))

    def test_fallback_only(self):
        jobs = [self.job("a.pdf", fallback=self.base + "/flaky/a"), self.job("b.pdf")]
        with mock.patch("fetch_em_all.fallback.time.sleep"):
            filed, failures, attempted = engine.run(jobs, self.settings, None, self.fetcher, log=self.say)
        self.assertEqual((filed, attempted), (1, 2))
        self.assertEqual(Handler.hits["/flaky/a"], 2)  # truncated once, retried
        self.assertIn("no fallback URL", failures[0]["error"])

    def test_limit(self):
        jobs = [self.job(f"{i}.pdf") for i in range(5)]
        filed, _, attempted = engine.run(jobs, self.settings, FakeSession(self.settings.downloads_dir, {}),
                                         None, limit=2, log=self.say)
        self.assertEqual((filed, attempted), (2, 2))


class FallbackFetcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Run.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        Run.tearDownClass.__func__(cls)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "x" / "f.pdf"
        Handler.hits.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistent_truncation_gives_up_cleanly(self):
        with mock.patch("fetch_em_all.fallback.time.sleep"), self.assertRaises(ConnectionError):
            Fetcher(Pacer(0, 0)).fetch(self.base + "/trunc", self.dest)
        self.assertEqual(Handler.hits["/trunc"], 3)
        self.assertEqual(list(self.dest.parent.iterdir()), [])

    def test_missing_is_not_retried(self):
        with self.assertRaises(Exception):
            Fetcher(Pacer(0, 0)).fetch(self.base + "/missing", self.dest)
        self.assertEqual(Handler.hits["/missing"], 1)

    def test_pacer_spaces_requests_per_host(self):
        import time
        p, start = Pacer(0.05, 0.2), time.monotonic()
        for _ in range(3):
            p.wait("https://a.example/x")
        self.assertGreaterEqual(time.monotonic() - start, 0.1)
        start = time.monotonic()
        p.wait("https://web.archive.org/x")
        p.wait("https://web.archive.org/y")
        self.assertGreaterEqual(time.monotonic() - start, 0.2)


if __name__ == "__main__":
    unittest.main()
