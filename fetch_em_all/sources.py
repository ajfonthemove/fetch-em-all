"""Readers for each kind of URL source. Each returns a list of row dicts; rows carry at least the URL."""

import csv
import html
import json
import re
import urllib.parse
from pathlib import Path


class SourceError(ValueError):
    pass


def read_table(path, delimiter):
    """CSV/TSV rows as dicts, plus the column names."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows = [dict(r) for r in reader]
        return rows, list(reader.fieldnames or [])


def read_text(path):
    """One URL per line; blank lines and '#' comments are skipped."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append({"url": line})
    return rows


def read_json(path):
    """A JSON list (of URL strings or objects), or JSON Lines (one string or object per line)."""
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.lower() == ".jsonl":
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        items = json.loads(text)
        if not isinstance(items, list):
            raise SourceError(f"{path}: expected a JSON list")
    rows = []
    for i, item in enumerate(items, 1):
        if isinstance(item, str):
            rows.append({"url": item})
        elif isinstance(item, dict):
            rows.append({k: v if isinstance(v, str) else json.dumps(v) for k, v in item.items()})
        else:
            raise SourceError(f"{path}: item {i} is neither a string nor an object")
    return rows


_A_TAG = re.compile(r"<a\s[^>]*?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)[^>]*>(.*?)</a>", re.S | re.I)
_BASE_TAG = re.compile(r"<base\s[^>]*?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def read_html(path, base_url=None):
    """Links (<a href>) of a saved page, made absolute against base_url (or the page's own <base href>)."""
    page = Path(path).read_text(encoding="utf-8", errors="replace")
    base_tag = _BASE_TAG.search(page)
    base = base_url or (html.unescape(base_tag.group(1).strip("\"'")) if base_tag else None)
    rows, relative = [], 0
    for href, text in _A_TAG.findall(page):
        href = html.unescape(href.strip("\"'")).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        if not urllib.parse.urlparse(href).scheme:
            if not base:
                relative += 1
                continue
            href = urllib.parse.urljoin(base, href)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(text))).strip()
        rows.append({"url": href.split("#")[0], "link_text": text})
    if relative:
        raise SourceError(f"{path}: {relative} links are relative; set base_url to the page's address")
    return rows
