"""fetch-em-all: download URLs through your own browser on macOS, in the background, filed and checksummed."""

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import __version__, config, doctor, engine
from .browser import BROWSERS
from .fallback import Fetcher, Pacer


def _plan(settings, jobs, log=print):
    have = engine.present(jobs, settings.out)
    log(f"{len(jobs)} files from {len({j.source for j in jobs})} sources; {len(have)} already in {settings.out}; "
        f"{len(jobs) - len(have)} to fetch")
    for name, count in Counter(j.source for j in jobs).items():
        log(f"  {name}: {count} ({sum(1 for j in jobs if j.source == name and j.fallback)} with a fallback)")
    return have


def _keep_awake():
    """Hold off idle sleep while this process runs (macOS)."""
    if sys.platform == "darwin":
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def _execute(settings, jobs, args):
    use_browser = not args.fallback_only
    if use_browser and sys.platform != "darwin":
        print("Browser fetching needs macOS; use --fallback-only to fetch fallback URLs only.", file=sys.stderr)
        return 2
    fetcher = None if args.no_fallback else Fetcher(Pacer(settings.pace_seconds, settings.fallback_pace_seconds))
    if not any(not (settings.out / j.dest).exists() for j in jobs):
        print("Nothing to fetch.")
        return 0
    _keep_awake()
    if use_browser:
        from .session import BrowserSession
        with BrowserSession(BROWSERS[settings.browser], settings.port, settings.downloads_dir,
                            settings.start_timeout, settings.finish_timeout) as session:
            filed, failures, attempted = engine.run(jobs, settings, session, fetcher, args.limit)
    else:
        filed, failures, attempted = engine.run(jobs, settings, None, fetcher, args.limit)
    skipped = attempted - filed - len(failures)
    print(f"Done: {filed} filed, {len(failures)} failed" + (f", {skipped} not attempted" if skipped else "")
          + f". Index: {settings.out / 'index.jsonl'}; failures: {settings.out / 'failed.jsonl'}")
    return 0 if filed == attempted else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fetch-em-all", description=__doc__)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def config_args(p):
        p.add_argument("config", help="YAML config file")
        p.add_argument("--only", help="comma list of source names")
        p.add_argument("--out", help="override the config's out folder")

    def run_args(p):
        p.add_argument("--limit", type=int, help="fetch at most N files")
        source = p.add_mutually_exclusive_group()
        source.add_argument("--fallback-only", action="store_true", help="skip the browser; fetch fallback URLs")
        source.add_argument("--no-fallback", action="store_true", help="never fetch fallback URLs")

    p_plan = sub.add_parser("plan", help="validate a config and show what would be fetched (no browser)")
    config_args(p_plan)
    p_plan.add_argument("--list", action="store_true", help="print every URL and destination")
    p_run = sub.add_parser("run", help="fetch everything in a config")
    config_args(p_run)
    run_args(p_run)
    p_get = sub.add_parser("get", help="fetch URLs given on the command line")
    p_get.add_argument("urls", nargs="+")
    p_get.add_argument("--out", required=True, help="folder to file downloads into")
    p_get.add_argument("--dest", default="{file_name}", help="destination template (default: %(default)s)")
    p_get.add_argument("--browser", default="chrome", choices=sorted(BROWSERS))
    p_get.add_argument("--port", type=int, default=8765)
    p_get.add_argument("--limit", type=int, help=argparse.SUPPRESS)
    p_doc = sub.add_parser("doctor", help="check this Mac's setup (read-only)")
    p_doc.add_argument("--browser", default="chrome", choices=sorted(BROWSERS))
    p_doc.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if args.command == "doctor":
        print(f"fetch-em-all {__version__} setup check ({BROWSERS[args.browser].app}):")
        return doctor.report(args.browser, args.port)
    try:
        if args.command == "get":
            raw = {"out": args.out, "browser": args.browser, "port": args.port}
            settings = config.parse_settings(raw, Path.cwd())
            jobs = config.jobs_for_urls(args.urls, args.dest)
            args.fallback_only, args.no_fallback = False, True  # command-line URLs have no fallbacks
        else:
            only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
            settings, jobs = config.load(args.config, only, args.out)
    except config.ConfigError as e:
        print(f"Config problems:\n{e}", file=sys.stderr)
        return 2
    if args.command == "plan":
        have = {id(j) for j in _plan(settings, jobs)}
        if args.list:
            for j in jobs:
                print(f"  {'have' if id(j) in have else 'todo'}  {j.url}  ->  {j.dest}")
        return 0
    if args.command == "run":
        _plan(settings, jobs)
    return _execute(settings, jobs, args)


if __name__ == "__main__":
    sys.exit(main())
