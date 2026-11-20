"""Tiny stdlib HTTP server: JSON API + static files, bound to localhost only."""

from __future__ import annotations

import json
import mimetypes
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import extract as ex
from . import filer
from .db import Store
from .jobs import JobManager
from .sources import AREAS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_BODY = 2_000_000


def parse_feeds(raw: str) -> list[tuple[str, str, str]]:
    """Parse the feed editor textarea: one URL per line, optional '# Name · Area'."""
    feeds: list[tuple[str, str, str]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        area = ""
        if "#" in line:
            line, comment = line.split("#", 1)
            line = line.strip()
            comment = comment.strip()
            if "·" in comment:
                name, area = [p.strip() for p in comment.split("·", 1)]
            else:
                name = comment
        else:
            name = ""
        if not line.startswith("http"):
            continue
        host = line.split("//", 1)[-1].split("/")[0].replace("www.", "")
        feeds.append((name or host, line, area or "Custom"))
    return feeds


class Handler(BaseHTTPRequestHandler):
    server_version = "ExtempDesk"
    store: Store
    jobs: JobManager

    # ------------------------------------------------------------------ utils
    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def log_message(self, fmt, *args):  # quieter console
        return

    # ------------------------------------------------------------------ static
    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/") :]
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._json({"error": "not found"}, 404)
            return
        if not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------------- verbs
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if route == "/api/stats":
                self._json(
                    {
                        "stats": self.store.stats(),
                        "disk": self.store.footprint(),
                        "sources": self.store.sources()[:40],
                    }
                )
            elif route == "/api/sources":
                feeds = self.store.feeds()
                self._json(
                    {
                        "feeds": feeds,
                        "areas": list(AREAS),
                        "enabled": sum(1 for f in feeds if f["enabled"]),
                    }
                )
            elif route == "/api/search":
                days = qs.get("days", "90")
                days_val = 0 if days in ("0", "all", "") else max(1, int(days))
                self._json(
                    self.store.search(
                        qs.get("q", ""),
                        days=days_val,
                        limit=min(int(qs.get("limit", 50)), 200),
                        offset=max(int(qs.get("offset", 0)), 0),
                        source=qs.get("source") or None,
                    )
                )
            elif route == "/api/article":
                article = self.store.get(int(qs.get("id", 0)))
                if not article:
                    self._json({"error": "no such article"}, 404)
                else:
                    self._json(article)
            elif route == "/api/job":
                job = self.jobs.get(qs.get("id", ""))
                self._json(job.snapshot() if job else {"error": "no such job"}, 200 if job else 404)
            elif route == "/api/jobs":
                self._json({"jobs": self.jobs.recent()})
            elif route == "/api/config":
                self._json({"areas": list(AREAS)})
            elif route.startswith("/api/"):
                self._json({"error": "unknown endpoint"}, 404)
            else:
                self._serve_static(route)
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            body = self._body()
            if route == "/api/file":
                feeds = parse_feeds(body.get("feeds") or "") or self.store.enabled_feeds()
                if not feeds:
                    self._json({"error": "no usable feed URLs given"}, 400)
                    return
                days = max(0, int(body.get("days", 90)))
                per_feed = min(max(int(body.get("per_feed", 40)), 1), 200)
                job = self.jobs.run(
                    "auto-file",
                    {"feeds": len(feeds), "days": days},
                    lambda progress: filer.autofile(
                        self.store, feeds, days=days, per_feed=per_feed, progress=progress
                    ),
                )
                self._json({"job": job.snapshot()})
            elif route == "/api/query":
                query = (body.get("query") or "").strip()
                if not query:
                    self._json({"error": "query needed"}, 400)
                    return
                days = max(0, int(body.get("days", 90)))
                limit = min(max(int(body.get("limit", 40)), 1), 150)
                follow = bool(body.get("follow_refs"))
                job = self.jobs.run(
                    "query-file",
                    {"query": query, "days": days, "limit": limit},
                    lambda progress: filer.query_file(
                        self.store,
                        query,
                        days=days,
                        limit=limit,
                        follow_refs=follow,
                        progress=progress,
                    ),
                )
                self._json({"job": job.snapshot()})
            elif route == "/api/extract":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    self._json({"error": "give a full http(s) URL"}, 400)
                    return
                article = ex.extract(url, area=(body.get("area") or None), min_words=40)
                if article is None:
                    self._json(
                        {"error": "could not find an article body at that URL"}, 422
                    )
                    return
                article_id, created = self.store.save(article)
                self._json({"id": article_id, "created": created, "article": article})
            elif route == "/api/delete":
                self.store.delete(int(body.get("id", 0)))
                self._json({"ok": True})
            elif route == "/api/sources":
                self._handle_sources(body)
            elif route == "/api/sources/test":
                self._handle_source_test(body)
            else:
                self._json({"error": "unknown endpoint"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


    # ------------------------------------------------------------------ sources
    def _sources_payload(self) -> dict:
        feeds = self.store.feeds()
        return {
            "feeds": feeds,
            "areas": list(AREAS),
            "enabled": sum(1 for f in feeds if f["enabled"]),
        }

    def _handle_sources(self, body: dict) -> None:
        action = (body.get("action") or "").strip()
        if action == "add":
            url = (body.get("url") or "").strip()
            if not url.startswith("http"):
                raise ValueError("give a full http(s) feed URL")
            self.store.feed_add(url, body.get("name") or "", body.get("area") or "Custom")
        elif action == "toggle":
            self.store.feed_set_enabled(bool(body.get("enabled")), int(body.get("id", 0)))
        elif action == "enable_all":
            self.store.feed_set_enabled(True)
        elif action == "disable_all":
            self.store.feed_set_enabled(False)
        elif action == "delete":
            self.store.feed_delete(int(body.get("id", 0)))
        elif action == "rename":
            self.store.feed_update(
                int(body.get("id", 0)),
                name=(body.get("name") or "").strip(),
                area=(body.get("area") or "Custom").strip(),
            )
        else:
            raise ValueError(f"unknown action {action!r}")
        self._json(self._sources_payload())

    def _handle_source_test(self, body: dict) -> None:
        feeds = self.store.feeds()
        target = body.get("id")
        if target:
            feeds = [f for f in feeds if f["id"] == int(target)]
            if not feeds:
                raise ValueError("no such feed")
        days = max(1, int(body.get("days", 90)))
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(feeds)))) as pool:
            results = list(pool.map(lambda f: _probe(f, days), feeds))
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for result in results:
            self.store.feed_update(
                result["id"],
                last_checked=stamp,
                last_status=result["status"],
                last_count=result["count"],
                last_error=result["error"],
            )
        self._json({"results": results, **self._sources_payload()})


def _probe(feed: dict, days: int = 90) -> dict:
    """Fetch one feed and report whether it still works."""
    try:
        items = filer._candidates_from_feed(feed["url"], days, 5)
    except Exception as exc:
        return {
            "id": feed["id"],
            "status": "dead",
            "count": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }
    return {"id": feed["id"], "status": "ok", "count": len(items), "error": None}


def serve(store: Store, jobs: JobManager, host: str = "127.0.0.1", port: int = 8765):
    handler = type("BoundHandler", (Handler,), {"store": store, "jobs": jobs})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
