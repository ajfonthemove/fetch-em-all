"""Fetch a fallback URL (e.g. an Internet Archive copy) directly, paced per host."""

import http.client
import ssl
import threading
import time
import urllib.parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .downloads import check_content

USER_AGENT = "fetch-em-all (+https://github.com/ajfonthemove/fetch-em-all)"
ARCHIVE_HOSTS = {"web.archive.org"}
CHUNK = 1 << 20


def ssl_context():
    """Verify TLS against certifi's bundle when available (python.org macOS builds ship without one)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class Pacer:
    """Minimum interval between requests to each host."""

    def __init__(self, default_interval, archive_interval):
        self.default, self.archive = default_interval, archive_interval
        self.lock, self.last = threading.Lock(), {}

    def wait(self, url):
        host = urllib.parse.urlparse(url).netloc
        interval = self.archive if host in ARCHIVE_HOSTS else self.default
        with self.lock:
            delay = interval - (time.monotonic() - self.last.get(host, 0.0))
            if delay > 0:
                time.sleep(delay)
            self.last[host] = time.monotonic()


class Fetcher:
    def __init__(self, pacer, attempts=4, timeout=120):
        self.pacer, self.attempts, self.timeout = pacer, attempts, timeout
        self.context = ssl_context()

    def _open(self, url):
        for attempt in range(self.attempts):
            self.pacer.wait(url)
            try:
                return urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=self.timeout,
                               context=self.context)
            except HTTPError as e:
                e.close()
                if e.code in (401, 403, 404, 410) or attempt == self.attempts - 1:
                    raise
                retry_after = (e.headers.get("Retry-After") or "").strip()
                time.sleep(int(retry_after) if retry_after.isdigit() else 5 * 2 ** attempt)
            except (URLError, TimeoutError, ConnectionError) as e:
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    raise RuntimeError("TLS verification failed: install certifi (python3 -m pip install certifi) "
                                       "or, on python.org builds, run 'Install Certificates.command'")
                if attempt == self.attempts - 1:
                    raise
                time.sleep(5 * 2 ** attempt)

    def fetch(self, url, dest):
        """Stream url to dest (via dest.part); returns bytes written. Retries truncated transfers."""
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                total = 0
                with self._open(url) as resp, open(part, "wb") as out:
                    expected = int(resp.headers.get("Content-Length") or 0)
                    first = True
                    while chunk := resp.read(CHUNK):
                        if first:
                            check_content(chunk, dest.name)
                            first = False
                        out.write(chunk)
                        total += len(chunk)
                if first:
                    raise ValueError("empty response")
                if expected and total != expected:
                    raise ConnectionError(f"incomplete download ({total} of {expected} bytes)")
                part.replace(dest)
                return total
            except (OSError, http.client.HTTPException) as e:
                if isinstance(e, HTTPError) or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            finally:
                part.unlink(missing_ok=True)
