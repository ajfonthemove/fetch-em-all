import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from fetch_em_all import downloads
from fetch_em_all.controller import Controller, serve


class Matching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl = Path(self.tmp.name)
        (self.dl / "old.pdf").write_bytes(b"%PDF old")
        self.before = downloads.snapshot(self.dl)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name, age=0.0):
        p = self.dl / name
        p.write_bytes(b"%PDF-1.4")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_name_pattern(self):
        p = downloads.name_pattern("Chap 3.pdf")
        self.assertTrue(p.match("Chap 3.pdf") and p.match("chap 3 (2).PDF"))
        self.assertFalse(p.match("Chap 3.pdf.crdownload") or p.match("xChap 3.pdf"))

    def test_newest_variant_of_expected_name(self):
        self.add("unrelated.pdf")
        self.add("sol90.pdf", age=5)
        newest = self.add("sol90 (1).pdf")
        self.assertEqual(downloads.finished_copy(self.dl, self.before, "sol90.pdf", "pdf"), newest)

    def test_single_unexpected_name_is_taken_but_never_guessed(self):
        got = self.add("server_name.pdf")
        self.assertEqual(downloads.finished_copy(self.dl, self.before, "234", "pdf"), got)
        self.add("another.pdf")
        self.assertIsNone(downloads.finished_copy(self.dl, self.before, "234", "pdf"))

    def test_ignores_preexisting_hidden_and_in_progress(self):
        (self.dl / ".DS_Store").write_bytes(b"x")
        (self.dl / "a.pdf.crdownload").write_bytes(b"")
        self.assertIsNone(downloads.finished_copy(self.dl, self.before, "old.pdf", "pdf"))
        self.assertTrue(downloads.started(self.dl, self.before, "a.pdf"))
        self.assertTrue(downloads.in_progress(self.dl, self.before))

    def test_check_content(self):
        downloads.check_content(b"%PDF-1.7 ...", "a.pdf")
        downloads.check_content(b"<!doctype html><p>", "page.html")
        downloads.check_content(b"PK\x03\x04", "data.zip")
        for head, name in [(b"<html>404", "a.pdf"), (b"  <!DOCTYPE html>", "data.zip"), (b"", "x.bin")]:
            with self.assertRaises(ValueError):
                downloads.check_content(head, name)

    def test_index_and_failed(self):
        index = downloads.Index(self.dl)
        index.add(path="a.pdf", bytes=3)
        index.close()
        downloads.write_failed(self.dl, [{"url": "u", "error": "e"}])
        entry = json.loads((self.dl / "index.jsonl").read_text())
        self.assertEqual((entry["path"], entry["bytes"]), ("a.pdf", 3))
        self.assertIn("filed_at", entry)
        self.assertEqual(json.loads((self.dl / "failed.jsonl").read_text())["error"], "e")


class ControllerHandOff(unittest.TestCase):
    def test_each_url_handed_out_once(self):
        c = Controller()
        self.assertEqual(c.next_job(), {})
        c.offer("https://x/a.pdf", "a.pdf")
        self.assertEqual(c.next_job(), {"url": "https://x/a.pdf", "label": "a.pdf"})
        self.assertTrue(c.taken.is_set())
        self.assertEqual(c.next_job(), {})
        c.offer("https://x/b.pdf", "b.pdf")
        self.assertFalse(c.taken.is_set())
        c.finish()
        self.assertEqual(c.next_job(), {"done": True})

    def test_server(self):
        c = Controller()
        server = serve(c, 0)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/") as r:
                page = r.read().decode()
            self.assertIn('fetch("/next"', page)
            self.assertIn("location.href = job.url", page)
            c.offer("https://x/a.pdf", "a.pdf")
            with urllib.request.urlopen(base + "/next") as r:
                self.assertEqual(json.load(r)["url"], "https://x/a.pdf")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/elsewhere")
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()


class WaitFor(unittest.TestCase):
    def test_wait_for(self):
        flag = threading.Event()
        timer = threading.Timer(0.1, flag.set)
        timer.start()
        self.assertTrue(downloads.wait_for(flag.is_set, 2, step=0.02))
        self.assertIsNone(downloads.wait_for(lambda: None, 0.1, step=0.02))
        timer.join()


if __name__ == "__main__":
    unittest.main()
