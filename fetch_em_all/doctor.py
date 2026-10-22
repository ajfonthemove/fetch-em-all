"""Read-only setup check: reports what it can see and says what the human should change. Changes nothing."""

import importlib.util
import json
import socket
import sys

from .browser import BROWSERS, is_installed, profile_dir

OK, BAD, UNKNOWN = "ok", "fix", "??"


def _profile_prefs(browser):
    """(profile name, Preferences dict) for the browser's last-used profile, or (None, None)."""
    root = profile_dir(browser)
    try:
        last = json.loads((root / "Local State").read_text(encoding="utf-8"))["profile"].get("last_used") or "Default"
    except (OSError, ValueError, KeyError, AttributeError):
        last = "Default"
    try:
        return last, json.loads((root / last / "Preferences").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None


def checks(browser_name="chrome", port=8765):
    """[(status, item, detail)]"""
    out = []
    add = lambda status, item, detail="": out.append((status, item, detail))
    add(OK if sys.platform == "darwin" else BAD, "macOS",
        "" if sys.platform == "darwin" else "browser fetching needs macOS; elsewhere use `run --fallback-only`")
    new_enough = sys.version_info >= (3, 9)
    add(OK if new_enough else BAD, f"Python {sys.version.split()[0]}", "" if new_enough else "needs 3.9 or newer")
    has_yaml = importlib.util.find_spec("yaml") is not None
    add(OK if has_yaml else BAD, "PyYAML", "" if has_yaml else "python3 -m pip install pyyaml")
    has_certifi = importlib.util.find_spec("certifi") is not None
    add(OK if has_certifi else UNKNOWN, "certifi (TLS roots for fallback fetches)", "" if has_certifi else
        "optional; python3 -m pip install certifi if HTTPS fails with CERTIFICATE_VERIFY_FAILED")
    browser = BROWSERS[browser_name]
    if sys.platform != "darwin":
        return out
    installed = is_installed(browser)
    add(OK if installed else BAD, f"{browser.app} installed")
    profile, prefs = _profile_prefs(browser) if installed else (None, None)
    s = browser.scheme
    if prefs is None:
        add(UNKNOWN, "browser settings", f"could not read the {browser.app} profile; check the settings below by hand")
    else:
        pdf = prefs.get("plugins", {}).get("always_open_pdf_externally")
        add(OK if pdf else UNKNOWN, "PDFs are downloaded, not shown",
            "" if pdf else f"for PDF jobs set {s}://settings/content/pdfDocuments to 'Download PDFs'")
        ask = prefs.get("download", {}).get("prompt_for_download")
        add(BAD if ask else OK, "'Ask where to save each file' is off",
            f"turn it off at {s}://settings/downloads" if ask else "")
        folder = prefs.get("download", {}).get("default_directory")
        add(OK, "download folder", f"{folder} (set downloads_dir to match)" if folder else "default (~/Downloads)")
        exceptions = prefs.get("profile", {}).get("content_settings", {}).get("exceptions", {})
        allowed = any(k.startswith(f"http://127.0.0.1:{port},") and v.get("setting") == 1
                      for k, v in exceptions.get("automatic_downloads", {}).items())
        add(OK if allowed else UNKNOWN, f"multiple downloads allowed for http://127.0.0.1:{port}",
            "" if allowed else f"add it under {s}://settings/content/automaticDownloads > Allowed")
        add(OK, "profile read", f"{profile} (the browser saves settings with a delay; `run --limit 1` is the proof)")
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        add(OK, f"port {port} free")
    except OSError:
        add(BAD, f"port {port} free", "in use; pick another `port` (and allow that one in the browser)")
    add(UNKNOWN, "permission to control the browser", "macOS asks on the first run; allow it "
        "(System Settings > Privacy & Security > Automation)")
    return out


def report(browser_name="chrome", port=8765, log=print):
    results = checks(browser_name, port)
    for status, item, detail in results:
        log(f"  [{status:>3}] {item}" + (f": {detail}" if detail else ""))
    return 1 if any(s == BAD for s, _, _ in results) else 0
