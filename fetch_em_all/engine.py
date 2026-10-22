"""The run loop: for each job not yet on disk, fetch through the browser (or the fallback), check, file, index."""

import shutil
import sys
import time

from . import downloads
from .config import url_fields
from .session import Blocked, DeadLink, FetchTimeout


def present(jobs, out_dir):
    return [j for j in jobs if (out_dir / j.dest).exists()]


def _file(src, dest):
    with open(src, "rb") as f:
        downloads.check_content(f.read(1024), dest.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dest)


def run(jobs, settings, session=None, fetcher=None, limit=None, log=print):
    """Fetch every job whose destination is missing. Returns (filed, failures).

    session: a BrowserSession (entered) or None to skip the browser; fetcher: a fallback Fetcher or None.
    """
    out_dir = settings.out
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [j for j in jobs if not (out_dir / j.dest).exists()]
    todo = todo[:limit] if limit else todo
    index, failures, filed = downloads.Index(out_dir), [], 0
    try:
        for i, job in enumerate(todo, 1):
            dest, via, fetched, errors = out_dir / job.dest, None, None, []
            if session is not None:
                fields = url_fields(job.url, 0)
                try:
                    src = session.fetch(job.url, fields["file_name"], dest.suffix.lstrip("."))
                    _file(src, dest)
                    via, fetched = "browser", job.url
                except Blocked as e:
                    failures.append({"source": job.source, "url": job.url, "dest": str(job.dest), "error": str(e)})
                    log(f"[{i}/{len(todo)}] Stopping: {e}", file=sys.stderr)
                    break
                except (DeadLink, FetchTimeout, ValueError, OSError) as e:
                    errors.append(f"browser: {e}")
                time.sleep(settings.pace_seconds)
            if via is None and fetcher is not None and job.fallback:
                try:
                    fetcher.fetch(job.fallback, dest)
                    via, fetched = "fallback", job.fallback
                except Exception as e:  # any failure of one file is recorded; the run goes on
                    errors.append(f"fallback: {e}")
            if via is None:
                if session is None and (fetcher is None or not job.fallback):
                    errors.append("no fallback URL" if fetcher is not None else "nothing to fetch with")
                failures.append({"source": job.source, "url": job.url, "dest": str(job.dest),
                                 "error": "; ".join(errors)})
                log(f"[{i}/{len(todo)}] FAIL {job.dest}: {'; '.join(errors)}", file=sys.stderr)
                continue
            size = dest.stat().st_size
            index.add(path=str(job.dest), source=job.source, via=via, url=fetched, origin_url=job.url,
                      bytes=size, sha256=downloads.sha256_of(dest), fields=job.fields)
            filed += 1
            log(f"[{i}/{len(todo)}] {size / 1e6:8.2f} MB  {via:8s} {job.dest}", flush=True)
    finally:
        index.close()
        downloads.write_failed(out_dir, failures)
    return filed, failures, len(todo)
