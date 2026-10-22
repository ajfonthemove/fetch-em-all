"""Load a YAML config, validate it, and expand its sources into jobs (URL -> destination)."""

import re
import string
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import sources
from .browser import BROWSERS

DEFAULTS = {
    "browser": "chrome",
    "pace_seconds": 2.0,
    "fallback_pace_seconds": 5.0,
    "downloads_dir": "~/Downloads",
    "port": 8765,
    "start_timeout": 15,
    "finish_timeout": 300,
}
TOP_KEYS = set(DEFAULTS) | {"out", "sources"}
SOURCE_KEYS = {"name", "dest", "urls", "from", "from_html", "url_column", "fallback_column", "where", "base_url", "match"}
INLINE_KEYS = {"url", "dest", "fallback"}
TABLE_TYPES = {".csv": ",", ".tsv": "\t"}
OTHER_TYPES = {".txt", ".json", ".jsonl"}
MAX_NAME_BYTES = 150


class ConfigError(ValueError):
    """Every problem found in a config, reported together."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(f"- {p}" for p in self.problems))


@dataclass
class Settings:
    out: Path
    browser: str
    pace_seconds: float
    fallback_pace_seconds: float
    downloads_dir: Path
    port: int
    start_timeout: float
    finish_timeout: float


@dataclass
class Job:
    source: str
    url: str
    dest: Path                  # relative to Settings.out
    fallback: str = ""
    fields: dict = field(default_factory=dict)


def safe_name(text, fallback="untitled"):
    """One portable path component: no separators or control characters, at most MAX_NAME_BYTES bytes."""
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "_", text or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    while len(name.encode("utf-8")) > MAX_NAME_BYTES:
        name = name[:-1]
    name = name.rstrip(" .")
    return name if name and name not in (".", "..") else fallback


def url_fields(url, n):
    parts = urllib.parse.urlparse(url)
    file_name = urllib.parse.unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) or f"{n}"
    stem, dot, ext = file_name.rpartition(".")
    return {"host": parts.netloc, "path": urllib.parse.unquote(parts.path.lstrip("/")), "file_name": file_name,
            "stem": stem if dot else file_name, "ext": ext if dot else "", "n": str(n)}


def render_dest(template, values):
    """Fill a dest template and sanitize each path component; raises KeyError for unknown fields."""
    text = string.Formatter().vformat(template, (), values)
    parts = [safe_name(p, "") for p in text.replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("destination is empty")
    return Path(*parts)


def _number(raw, key, problems, kind=float, minimum=0):
    try:
        value = kind(raw)
    except (TypeError, ValueError):
        problems.append(f"{key}: expected a number, got {raw!r}")
        return None
    if value < minimum:
        problems.append(f"{key}: must be at least {minimum}")
    return value


def parse_settings(raw, base_dir, out_override=None):
    problems = []
    for key in sorted(set(raw) - TOP_KEYS):
        problems.append(f"unknown key {key!r} (known: {', '.join(sorted(TOP_KEYS))})")
    merged = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    out = out_override or raw.get("out")
    if not out:
        problems.append("out: missing (the folder to file downloads into); set it here or pass --out")
    if merged["browser"] not in BROWSERS:
        problems.append(f"browser: {merged['browser']!r} is not one of {', '.join(BROWSERS)}")
    numbers = {k: _number(merged[k], k, problems) for k in
               ("pace_seconds", "fallback_pace_seconds", "start_timeout", "finish_timeout")}
    port = _number(merged["port"], "port", problems, int, 1)
    if problems:
        raise ConfigError(problems)
    resolve = lambda p: (base_dir / Path(str(p)).expanduser()).resolve()
    out_dir = Path(out_override).expanduser().resolve() if out_override else resolve(out)  # --out is relative to cwd
    return Settings(out=out_dir, browser=merged["browser"], downloads_dir=resolve(merged["downloads_dir"]),
                    port=port, **numbers)


def _rows_for(src, base_dir, problems, label):
    """Rows of one source (each with 'url', and 'fallback'/'dest' when given)."""
    url_column, fallback_column = src.get("url_column", "url"), src.get("fallback_column")
    kinds = [k for k in ("urls", "from", "from_html") if k in src]
    if len(kinds) != 1:
        problems.append(f"{label}: needs exactly one of urls, from, from_html")
        return []
    if "urls" in src:
        rows = []
        if not isinstance(src["urls"], list):
            problems.append(f"{label}: urls must be a list")
            return []
        for i, item in enumerate(src["urls"], 1):
            if isinstance(item, str):
                rows.append({"url": item})
            elif isinstance(item, dict) and "url" in item:
                for key in sorted(set(item) - INLINE_KEYS):
                    problems.append(f"{label}: urls[{i}]: unknown key {key!r} (known: url, dest, fallback)")
                rows.append({k: str(v) for k, v in item.items()})
            else:
                problems.append(f"{label}: urls[{i}] must be a URL or a mapping with 'url'")
        return rows

    path = (base_dir / Path(str(src.get("from") or src.get("from_html"))).expanduser()).resolve()
    if not path.is_file():
        problems.append(f"{label}: file not found: {path}")
        return []
    try:
        if "from_html" in src:
            return sources.read_html(path, src.get("base_url"))
        suffix = path.suffix.lower()
        if suffix in TABLE_TYPES:
            rows, columns = sources.read_table(path, TABLE_TYPES[suffix])
        elif suffix in OTHER_TYPES:
            rows = sources.read_text(path) if suffix == ".txt" else sources.read_json(path)
            columns = sorted({k for r in rows for k in r})
        else:
            problems.append(f"{label}: unsupported file type {suffix!r} "
                            f"(use {', '.join(sorted(set(TABLE_TYPES) | OTHER_TYPES))}, or from_html)")
            return []
    except (OSError, ValueError, sources.SourceError) as e:
        problems.append(f"{label}: {e}")
        return []
    where = src.get("where") if isinstance(src.get("where"), dict) else {}
    missing = [c for c in (url_column, fallback_column, *where) if c and rows and c not in columns]
    for column in missing:
        problems.append(f"{label}: no column {column!r} in {path.name} (columns: {', '.join(columns)})")
    if missing:
        return []
    for r in rows:
        r["url"] = r.get(url_column, "")
        if fallback_column:
            r["fallback"] = r.get(fallback_column, "")
    return rows


def expand(raw_sources, base_dir, only=None):
    """Jobs for every source (or those named in `only`), validated and de-duplicated."""
    problems, jobs, names = [], [], []
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError(["sources: expected a non-empty list"])
    for i, src in enumerate(raw_sources, 1):
        if not isinstance(src, dict):
            problems.append(f"sources[{i}]: expected a mapping")
            continue
        name = str(src.get("name") or "")
        label = f"source {name!r}" if name else f"sources[{i}]"
        if not name:
            problems.append(f"{label}: missing name")
        elif name in names:
            problems.append(f"{label}: duplicate name")
        names.append(name)
        for key in sorted(set(src) - SOURCE_KEYS):
            problems.append(f"{label}: unknown key {key!r} (known: {', '.join(sorted(SOURCE_KEYS))})")
        if only and name not in only:
            continue
        rows = _rows_for(src, base_dir, problems, label)
        where = src.get("where") or {}
        if not isinstance(where, dict):
            problems.append(f"{label}: where must be a mapping of column: value")
            where = {}
        try:
            match = re.compile(src["match"]) if src.get("match") else None
        except re.error as e:
            problems.append(f"{label}: match is not a valid regular expression ({e})")
            match = None
        template = str(src.get("dest") or "{source}/{file_name}")
        n = 0
        for row in rows:
            if any(str(row.get(col, "")) not in ([str(v) for v in want] if isinstance(want, list) else [str(want)])
                   for col, want in where.items()):
                continue
            url = str(row.get("url", "")).strip()
            if not url or (match and not match.search(url)):  # rows without a URL are skipped, not errors
                continue
            n += 1
            if urllib.parse.urlparse(url).scheme not in ("http", "https"):
                problems.append(f"{label}: row {n}: not an http(s) URL: {url!r}")
                continue
            # the row's own non-empty columns win over fields derived from the URL (e.g. a manifest's file_name)
            derived = url_fields(url, n)
            values = {**derived, **{k: str(v) for k, v in row.items() if str(v) or k not in derived}, "source": name}
            try:
                dest = render_dest(str(row.get("dest") or template), values)
            except KeyError as e:
                problems.append(f"{label}: dest template uses unknown field {e} "
                                f"(available: {', '.join(sorted(values))})")
                break
            except ValueError as e:
                problems.append(f"{label}: row {n}: {e}")
                continue
            fallback = str(row.get("fallback", "")).strip()
            if fallback and urllib.parse.urlparse(fallback).scheme not in ("http", "https"):
                problems.append(f"{label}: row {n}: fallback is not an http(s) URL: {fallback!r}")
                fallback = ""
            jobs.append(Job(source=name, url=url, dest=dest, fallback=fallback, fields=row))
    unknown = sorted(set(only or ()) - set(names))
    if unknown:
        problems.append(f"--only: no source named {', '.join(unknown)} (sources: {', '.join(n for n in names if n)})")

    unique, by_dest = [], {}
    for job in jobs:  # same URL to the same place is fine; two URLs to one place is not
        key = str(job.dest).lower()  # macOS volumes are usually case-insensitive
        if key not in by_dest:
            by_dest[key] = job
            unique.append(job)
        elif by_dest[key].url != job.url:
            problems.append(f"destination {job.dest} is used by two URLs: {by_dest[key].url} and {job.url} "
                            f"(sources {by_dest[key].source!r}, {job.source!r}); make the dest templates distinct")
    if problems:
        raise ConfigError(problems)
    return unique


def load(path, only=None, out_override=None):
    """(Settings, jobs) for a YAML config file; raises ConfigError listing every problem."""
    try:
        import yaml
    except ImportError:
        raise ConfigError(["PyYAML is required to read configs: python3 -m pip install pyyaml"])
    path = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as e:
        raise ConfigError([f"cannot read {path}: {e}"])
    except yaml.YAMLError as e:
        raise ConfigError([f"{path} is not valid YAML: {e}"])
    if not isinstance(raw, dict):
        raise ConfigError([f"{path}: expected a mapping at the top level"])
    problems = []
    try:
        settings = parse_settings(raw, path.parent, out_override)
    except ConfigError as e:
        problems += e.problems
        settings = None
    try:
        jobs = expand(raw.get("sources"), path.parent, set(only) if only else None)
    except ConfigError as e:
        problems += e.problems
        jobs = []
    if problems:
        raise ConfigError(problems)
    return settings, jobs


def jobs_for_urls(urls, dest="{file_name}"):
    """Jobs for a one-off list of URLs (fetch-em-all get)."""
    return expand([{"name": "get", "urls": list(urls), "dest": dest}], Path.cwd())
