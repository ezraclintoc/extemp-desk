"""Auto-File (bulk RSS filing) and Query-File (search-engine filing)."""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, unquote, urlparse

import feedparser
import requests

from . import extract as ex
from .db import Store, canonical_url

MAX_WORKERS = 8

# GDELT answers 429 unless calls are at least five seconds apart. Respect it.
ENGINE_MIN_INTERVAL = {"GDELT": 7.0}
_engine_lock = threading.Lock()
_last_call: dict[str, float] = {}


# --------------------------------------------------------------------- helpers
def _entry_date(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6]).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                continue
    for key in ("published", "updated", "created"):
        if entry.get(key):
            got = ex.normalise_date(entry.get(key))
            if got:
                return got
    return None


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def normalise_link(url: str) -> str:
    """Rewrite the reader-unfriendly link shapes some feeds emit."""
    # UN News publishes /feed/view/en/story/... links that 406; /en/story/... works.
    if "news.un.org/feed/view/" in url:
        return url.replace("/feed/view/", "/")
    return url


def _throttle(engine: str) -> None:
    gap = ENGINE_MIN_INTERVAL.get(engine, 0.0)
    if not gap:
        return
    with _engine_lock:
        waited = time.time() - _last_call.get(engine, 0.0)
        if waited < gap:
            time.sleep(gap - waited)
        _last_call[engine] = time.time()


def _request(
    engine: str,
    url: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    tries: int = 3,
    timeout: int = 25,
) -> requests.Response:
    """GET/POST with engine throttling and backoff on 429/5xx/timeouts."""
    last: Exception | None = None
    if engine in ENGINE_MIN_INTERVAL:
        # Strictly rate-limited: one shot, then the caller cools it down.
        tries = 1
    delay = 2.0
    for _attempt in range(tries):
        _throttle(engine)
        try:
            if data is None:
                resp = requests.get(url, params=params, headers=ex.HEADERS, timeout=timeout)
            else:
                resp = requests.post(
                    url,
                    data=data,
                    headers={**ex.HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            last = exc
        else:
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last = requests.HTTPError(f"HTTP {resp.status_code}")
                if attempt == tries - 1:
                    break
                try:
                    retry_after = float(resp.headers.get("Retry-After") or 0)
                except ValueError:
                    retry_after = 0.0
                time.sleep(max(retry_after, delay))
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        if attempt == tries - 1:
            break
        time.sleep(delay)
        delay *= 2
    raise last if last else RuntimeError("request failed")


# --------------------------------------------------------------- feed reading
def _candidates_from_feed(url: str, days: int, cap: int) -> list[dict]:
    resp = _request("feed", url, timeout=25)
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unreadable feed: {parsed.get('bozo_exception')}")
    cutoff = _cutoff(days)
    out: list[dict] = []
    for entry in parsed.entries:
        link = normalise_link(entry.get("link") or "")
        if not link.startswith("http"):
            continue
        published = _entry_date(entry)
        if published and published < cutoff and days > 0:
            continue
        out.append(
            {
                "url": link,
                "title": (entry.get("title") or "").strip(),
                "published": published,
            }
        )
        if len(out) >= cap:
            break
    return out


def _fetch_all(
    candidates: list[dict],
    store: Store,
    area: str | None,
    progress,
    label: str,
    cutoff: str | None = None,
    want_links: bool = False,
) -> dict:
    """Extract + save a list of candidate URLs concurrently.

    Links found through citations can be years old; when a filing window is
    given, an article dated before it is skipped rather than filed.
    """
    fresh = [c for c in candidates if not store.exists(c["url"])]
    already = len(candidates) - len(fresh)
    if already:
        progress(f"{label}: skipped {already} already in your library")

    saved = 0
    short = 0
    failed = 0
    blocked = 0
    stale = 0
    done = 0
    total = len(fresh)
    links: list[str] = []
    if not total:
        return {
            "new": 0, "duplicate": already, "short": 0, "failed": 0,
            "blocked": 0, "stale": 0, "links": links,
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(
                ex.extract, c["url"], None, c.get("area") or area, ex.MIN_WORDS, want_links
            ): c
            for c in fresh
        }
        for future in as_completed(futures):
            candidate = futures[future]
            done += 1
            try:
                article = future.result()
            except ex.BlockedError:
                blocked += 1
                continue
            except Exception:
                failed += 1
                continue
            if article is None:
                short += 1
                continue
            if not article.get("published"):
                article["published"] = candidate.get("published")
            if cutoff and article.get("published") and article["published"] < cutoff:
                stale += 1
                continue
            if candidate.get("title") and len(candidate["title"]) > len(article["title"]):
                article["title"] = candidate["title"]
            _, created = store.save(article)
            saved += int(created)
            if want_links and created:
                links.extend(article.get("links") or [])
            if done % 10 == 0 or done == total:
                progress(
                    f"{label}: {done}/{total} processed · {saved} filed",
                    done=done,
                    total=total,
                )
    return {
        "new": saved,
        "duplicate": already,
        "short": short,
        "failed": failed,
        "blocked": blocked,
        "stale": stale,
        "links": links,
    }


# ------------------------------------------------------------------- auto-file
def autofile(
    store: Store,
    feeds: list[tuple[str, str, str]],
    days: int = 90,
    per_feed: int = 40,
    progress=lambda msg, **kw: None,
) -> dict:
    """Download + file every recent article from a list of RSS feeds."""
    progress(f"Auto-File: {len(feeds)} feeds, last {days} days, up to {per_feed}/feed")
    discovered: list[dict] = []
    ok_feeds = 0
    dead: list[str] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {
            pool.submit(_candidates_from_feed, url, days, per_feed): (name, area)
            for name, url, area in feeds
        }
        for future in as_completed(jobs):
            name, area = jobs[future]
            try:
                items = future.result()
            except Exception as exc:
                dead.append(f"{name} ({type(exc).__name__})")
                progress(f"feed failed: {name} — {type(exc).__name__}: {str(exc)[:70]}")
                continue
            ok_feeds += 1
            for item in items:
                item["area"] = area
            discovered.extend(items)
            progress(f"feed ok: {name} — {len(items)} recent items")

    # De-duplicate the same story syndicated across feeds.
    seen: set[str] = set()
    unique: list[dict] = []
    for item in discovered:
        key = canonical_url(item["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    progress(f"Auto-File: {len(unique)} unique articles found — extracting text")
    result = _fetch_all(
        unique, store, None, progress, "Auto-File", cutoff=_cutoff(days) if days else None
    )
    result.pop("links", None)
    result["feeds_ok"] = ok_feeds
    result["feeds_failed"] = dead
    progress(
        f"Auto-File done: {result['new']} new articles filed, "
        f"{result['short']} pages skipped (no article body), "
        f"{result['blocked']} blocked by the publisher, "
        f"{result['stale']} older than the window, {result['failed']} errors"
    )
    return result


# ------------------------------------------------------------------ query-file
OPERATOR_RE = re.compile(r"(\w+):(\S+)")


def parse_query(text: str, default_days: int = 90) -> dict:
    """Split a query into terms, "phrases", -exclusions and a site: filter.

    Every engine gets syntax it actually understands, instead of us forwarding
    whatever the user typed and hoping.
    """
    text = (text or "").strip()
    site = None
    for match in OPERATOR_RE.finditer(text):
        key, value = match.group(1).lower(), match.group(2)
        if key in ("site", "domain"):
            site = value.lstrip(".")
            text = text.replace(match.group(0), " ")
    tokens = re.findall(r'"[^"]*"|\S+', text)
    terms: list[str] = []
    phrases: list[str] = []
    exclude: list[str] = []
    for raw in tokens:
        negate = raw.startswith("-") and len(raw) > 1
        token = raw[1:] if negate else raw
        if token.startswith('"') and token.endswith('"') and len(token) > 2:
            value = token.strip('"').strip()
            if value:
                phrases.append(value)
                if negate:
                    exclude.append(value)
        else:
            value = token.strip().strip('"').strip()
            if not value:
                continue
            if negate:
                exclude.append(value)
            else:
                terms.append(value)
    return {
        "raw": (text or "").strip(),
        "terms": terms,
        "phrases": phrases,
        "exclude": exclude,
        "site": site,
        "days": default_days,
    }


def _bing_query(spec: dict) -> str:
    parts = [f'"{p}"' for p in spec["phrases"]] + spec["terms"]
    parts += [f"-{word}" for word in spec["exclude"]]
    if spec["site"]:
        parts.append(f"site:{spec['site']}")
    return " ".join(parts)


def _gdelt_query(spec: dict) -> str:
    parts = [f'"{p}"' for p in spec["phrases"]] + spec["terms"]
    parts.append("sourcelang:english")
    if spec["site"]:
        parts.append(f"domainis:{spec['site']}")
    return " ".join(parts)


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())


def _similar(a: str, b: str) -> bool:
    """Same story syndicated under slightly different heads?"""
    ta, tb = set(_title_key(a).split()), set(_title_key(b).split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= 0.85


def _score(spec: dict, item: dict) -> int:
    """Crude but effective: does this hit actually contain what was asked for?"""
    title = (item.get("title") or "").lower()
    url = (item.get("url") or "").lower()
    score = 0
    for phrase in spec["phrases"]:
        if phrase.lower() in title:
            score += 6
    for term in spec["terms"]:
        low = term.lower()
        if low in title:
            score += 3
        elif low in url:
            score += 1
    if spec["site"] and spec["site"] in url:
        score += 4
    for word in spec["exclude"]:
        if word.lower() in title:
            score -= 10
    published = item.get("published")
    if published:
        try:
            age = (datetime.now(timezone.utc) - datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
            if age <= 7:
                score += 2
            elif age > 45:
                score -= 1
        except ValueError:
            pass
    return score


def _engine_bing_news(spec: dict) -> list[dict]:
    url = (
        "https://www.bing.com/news/search?q="
        + quote_plus(_bing_query(spec) or "news")
        + "&format=RSS&count=100&qft=sortbydate%3d%221%22"
    )
    resp = _request("Bing News", url, timeout=25)
    parsed = feedparser.parse(resp.content)
    out = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        if not link.startswith("http"):
            continue
        out.append(
            {
                "url": link,
                "title": (entry.get("title") or "").strip(),
                "published": _entry_date(entry),
                "engine": "Bing News",
            }
        )
    return out


def _engine_gdelt(spec: dict) -> list[dict]:
    params = {
        "query": _gdelt_query(spec),
        "mode": "artlist",
        "maxrecords": "250",
        "format": "json",
        "sort": "datedesc",
        "timespan": f"{max(spec['days'], 1)}d",
    }
    resp = _request("GDELT", "https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=30)
    if not resp.text.strip().startswith("{"):
        # GDELT answers plain text when it dislikes a query (too short, etc.)
        raise ValueError(resp.text.strip()[:120] or "empty response")
    data = json.loads(resp.text)
    out = []
    for item in data.get("articles", []):
        link = item.get("url") or ""
        if not link.startswith("http"):
            continue
        stamp = item.get("seendate") or ""
        published = None
        if len(stamp) >= 8 and stamp[:8].isdigit():
            published = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
        out.append(
            {
                "url": link,
                "title": (item.get("title") or "").strip(),
                "published": published,
                "engine": "GDELT",
            }
        )
    return out


def _engine_ddg(spec: dict) -> list[dict]:
    query = _bing_query(spec) or "news"
    resp = _request(
        "DuckDuckGo",
        "https://lite.duckduckgo.com/lite/",
        data={"q": query, "kl": "us-en"},
        timeout=25,
    )
    html = resp.text
    if "result-link" not in html and "uddg=" not in html:
        raise ValueError("no results markup (rate limited?)")
    out = []
    for href, label in re.findall(
        r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', html, re.S
    ):
        link = href
        match = re.search(r"uddg=([^&]+)", link)
        if match:
            link = unquote(match.group(1))
        if not link.startswith("http"):
            continue
        title = re.sub(r"<[^>]+>", "", label)
        domain = urlparse(link).netloc.replace("www.", "")
        out.append(
            {
                "url": link,
                "title": title.strip(),
                "source_hint": domain,
                "published": None,
                "engine": "DuckDuckGo",
            }
        )
    return out


ENGINES = {
    "Bing News": _engine_bing_news,
    "GDELT": _engine_gdelt,
    "DuckDuckGo": _engine_ddg,
}


def _run_engines(spec: dict, progress, down: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Query every engine; a dead engine costs speed, never the whole run.

    `down` is the job's circuit breaker: once an engine refuses us we stop
    asking it for the remaining queries instead of paying the timeout each time.
    """
    down = down if down is not None else set()
    found: list[dict] = []
    report: list[dict] = []
    todo = {name: fn for name, fn in ENGINES.items() if name not in down}
    for name in ENGINES:
        if name in down:
            report.append(
                {"engine": name, "status": "skipped", "hits": 0, "seconds": 0.0,
                 "error": "skipped: unavailable earlier in this run"}
            )
    if not todo:
        return found, report

    started = time.time()
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        jobs = {pool.submit(fn, spec): name for name, fn in todo.items()}
        for future in as_completed(jobs):
            name = jobs[future]
            elapsed = round(time.time() - started, 1)
            try:
                hits = future.result()
            except Exception as exc:
                down.add(name)
                report.append(
                    {
                        "engine": name,
                        "status": "failed",
                        "hits": 0,
                        "seconds": elapsed,
                        "error": f"{type(exc).__name__}: {str(exc)[:100]}",
                    }
                )
                progress(f"engine unavailable: {name} ({str(exc)[:80]}) — skipping it for the rest of this run")
                continue
            report.append(
                {
                    "engine": name,
                    "status": "ok",
                    "hits": len(hits),
                    "seconds": elapsed,
                    "error": None,
                }
            )
            progress(f"engine ok: {name} — {len(hits)} hits")
            found.extend(hits)
    return found, report


def query_file(
    store: Store,
    query: str,
    days: int = 90,
    limit: int = 40,
    follow_refs: bool = False,
    refs_per_article: int = 3,
    progress=lambda msg, **kw: None,
) -> dict:
    """Run one or more queries across every engine, rank the hits, file the best.

    Each line of `query` is a separate search. Operators that every engine
    understands are honoured: "exact phrase", -exclusion, site:example.com.
    """
    lines = [line.strip() for line in (query or "").splitlines() if line.strip()]
    if not lines:
        raise ValueError("query-file needs a search query")

    specs = [parse_query(line, days) for line in lines]
    progress(
        f"Query-File: {len(specs)} quer{'y' if len(specs) == 1 else 'ies'} "
        f"across {len(ENGINES)} engines, last {days} days"
    )

    found: list[dict] = []
    report: list[dict] = []
    down: set[str] = set()
    for spec in specs:
        label = spec["raw"]
        progress(f'query: “{label}”')
        hits, engine_report = _run_engines(spec, progress, down)
        if not hits and (spec["site"] or spec["exclude"] or spec["phrases"]):
            # Over-specified query found nothing; retry once with the plain words.
            relaxed = dict(spec, site=None, exclude=[], phrases=[], raw=" ".join(spec["terms"]))
            if relaxed["raw"]:
                progress("nothing matched — retrying the same query without operators")
                hits, extra = _run_engines(relaxed, progress, down)
                engine_report.extend(extra)
                spec = relaxed
        for item in hits:
            item["_spec"] = spec
            item["_score"] = _score(spec, item)
        found.extend(hits)
        report.extend({**row, "query": label} for row in engine_report)

    # Cross-engine duplicates: engines wrap the same story in different URLs.
    found.sort(key=lambda i: (-i["_score"], i.get("published") or ""))
    unique: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, str]] = []
    for item in found:
        key = canonical_url(item["url"])
        if key in seen_urls:
            continue
        host = urlparse(item["url"]).netloc.replace("www.", "")
        if any(host == h and _similar(item.get("title", ""), t) for h, t in seen_titles):
            continue
        seen_urls.add(key)
        seen_titles.append((host, item.get("title", "")))
        unique.append(item)

    dropped = max(0, len(unique) - limit)
    unique = unique[:limit]
    if dropped:
        progress(f"Query-File: {dropped} weaker hits left out of the top {limit}")
    progress(f"Query-File: extracting {len(unique)} articles")

    refs_wanted = follow_refs and bool(unique)
    result = _fetch_all(
        unique, store, None, progress, "Query-File",
        cutoff=_cutoff(days) if days else None, want_links=refs_wanted,
    )
    cited = result.pop("links", [])

    ref_stats = {"attempted": 0, "filed": 0}
    if follow_refs and cited:
        ref_stats = _follow_references(store, cited, limit, progress, days)
    elif follow_refs:
        progress("Query-File: nothing new to chase citations from")

    result["new"] += ref_stats["filed"]
    result["engines"] = report
    result["queries"] = [spec["raw"] for spec in specs]
    result["citations_filed"] = ref_stats["filed"]
    progress(
        f"Query-File done: {result['new']} new articles filed "
        f"({ref_stats['filed']} from citations, {result['blocked']} blocked, "
        f"{result['stale']} older than the window)"
    )
    return result


def _follow_references(
    store: Store,
    links: list[str],
    cap: int,
    progress,
    days: int = 90,
) -> dict:
    """Depth-1 citation chasing, using links captured during extraction."""
    refs: list[str] = []
    seen: set[str] = set()
    for link in links:
        link = normalise_link(link)
        if not link.startswith("http"):
            continue
        key = canonical_url(link)
        if key in seen or store.exists(link):
            continue
        seen.add(key)
        refs.append(link)
        if len(refs) >= cap:
            break

    if not refs:
        progress("Query-File: no fresh citations found")
        return {"attempted": 0, "filed": 0}

    progress(f"Query-File: chasing {len(refs)} cited links")
    candidates = [{"url": link, "title": "", "published": None} for link in refs]
    result = _fetch_all(
        candidates, store, None, progress, "Query-File refs",
        cutoff=_cutoff(days) if days else None,
    )
    return {"attempted": len(refs), "filed": result["new"]}
