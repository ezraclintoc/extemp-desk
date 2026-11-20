#!/usr/bin/env python3
"""Extemp Desk — offline article filing, querying and reading for NCFCA extemp."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from extempdesk import filer
from extempdesk.db import Store
from extempdesk.extract import HEADERS  # noqa: F401  (imported for side-effect parity)
from extempdesk.jobs import JobManager
from extempdesk.server import serve
from extempdesk.sources import DEFAULT_FEEDS

ROOT = Path(__file__).resolve().parent


def check_feeds(days: int = 90) -> int:
    """Dev utility: report which default feeds are alive and how many items they carry."""
    from concurrent.futures import ThreadPoolExecutor

    def probe(feed):
        name, url, area = feed
        try:
            items = filer._candidates_from_feed(url, days, 5)
            return f"  OK    {name:26} {len(items):>2} items  [{area}]"
        except Exception as exc:
            return f"  DEAD  {name:26} {type(exc).__name__}: {str(exc)[:70]}"

    print(f"Checking {len(DEFAULT_FEEDS)} default feeds (last {days} days)…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for line in pool.map(probe, DEFAULT_FEEDS):
            print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extemp Desk — local extemp filing app")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=str(ROOT / "data" / "library.db"))
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    parser.add_argument("--check-feeds", action="store_true", help="test the default feed list")
    args = parser.parse_args()

    if args.check_feeds:
        return check_feeds()

    store = Store(args.db)
    jobs = JobManager()
    httpd = serve(store, jobs, host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}/"
    stats = store.stats()
    disk = store.footprint()["disk_bytes"]
    size = f"{disk / 1024 / 1024:.1f} MB" if disk > 1024 * 1024 else f"{disk / 1024:.0f} KB"
    print(f"Extemp Desk running at {url}")
    print(
        f"Library: {stats['total']} articles · {stats['last90']} within 90 days · "
        f"{size} on disk · {args.db}"
    )
    print(f"Sources: {len(store.enabled_feeds())} enabled — edit them in the Sources tab.")
    print("Ctrl-C to stop. Your library is entirely local and works with the internet off.")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
