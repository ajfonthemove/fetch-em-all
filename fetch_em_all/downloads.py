"""Watch the browser's downloads folder, check what arrived, and keep the run's index."""

import hashlib
import json
import re
import time
from datetime import datetime, timezone

IN_PROGRESS = (".crdownload", ".download", ".part")


def snapshot(folder):
    return {p.name for p in folder.iterdir()}


def name_pattern(file_name):
    """Matches file_name and the browser's de-duplicated 'name (1).ext' variants."""
    stem, dot, ext = file_name.rpartition(".")
    stem, ext = (stem, ext) if dot else (file_name, "")
    return re.compile(rf"^{re.escape(stem)}( \(\d+\))?{re.escape(dot + ext)}$", re.I)


def finished_copy(folder, before, expected_name, expected_ext=""):
    """A completed file that appeared since `before` under expected_name (or a variant), else None.

    Redirect links can arrive under the server's own name: if nothing matches, a single new completed file
    with the expected extension (or any extension, if none is expected) is taken. Never guesses between several.
    """
    new = [p for p in folder.iterdir() if p.name not in before and not p.name.endswith(IN_PROGRESS)
           and not p.name.startswith(".")]
    pattern = name_pattern(expected_name) if expected_name else None
    hits = [p for p in new if pattern and pattern.match(p.name)]
    if not hits:
        hits = [p for p in new if not expected_ext or p.suffix.lower() == "." + expected_ext.lower()]
        if len(hits) != 1:
            return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def in_progress(folder, before):
    return any(n.endswith(IN_PROGRESS) for n in snapshot(folder) - before)


def started(folder, before, expected_name, expected_ext=""):
    """True once the browser has created this download (in progress or done)."""
    return in_progress(folder, before) or finished_copy(folder, before, expected_name, expected_ext) is not None


def wait_for(condition, timeout, step=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(step)
    return None


def check_content(head, dest_name):
    """Raise ValueError if the first bytes don't look like the file type dest_name implies."""
    suffix = dest_name.rsplit(".", 1)[-1].lower() if "." in dest_name else ""
    if not head:
        raise ValueError("empty file")
    if suffix not in ("html", "htm", "xhtml") and re.match(rb"\s*(<!doctype html|<html)", head[:512], re.I):
        raise ValueError("got a web page instead of a file")
    if suffix == "pdf" and b"%PDF" not in head[:1024]:
        raise ValueError("not a PDF")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Index:
    """Appends one JSON line per filed file to <out>/index.jsonl."""

    def __init__(self, out_dir):
        self.path = out_dir / "index.jsonl"
        self.file = open(self.path, "a", encoding="utf-8")

    def add(self, **entry):
        self.file.write(json.dumps({**entry, "filed_at": now()}, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def write_failed(out_dir, failures):
    with open(out_dir / "failed.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in failures)
