"""Article Extractor: fetch a page and pull out clean article text + metadata."""

from __future__ import annotations

import re
import warnings
from datetime import datetime

import requests
import trafilatura

warnings.filterwarnings("ignore", module="urllib3")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

MIN_WORDS = 60

# Links that are never citations worth chasing.
JUNK_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "linkedin.com", "t.me", "reddit.com", "pinterest.com", "tiktok.com",
    "google.com", "apple.com", "amazon.com", "doubleclick.net", "googletagmanager.com",
    "wp.com", "wordpress.org", "cloudflare.com", "schema.org", "w3.org",
    "creativecommons.org", "paypal.com", "bit.ly", "shar.es", "addtoany.com",
    "sharethis.com", "gravatar.com", "feedburner.com", "mailchi.mp", "substackcdn.com",
)


class BlockedError(Exception):
    """The publisher refused us: paywall, bot protection or geoblock."""


BLOCKED_CODES = {401, 402, 403, 406, 451}


def fetch(url: str, timeout: int = 25) -> str:
    """One retry for flaky servers, then give up. Blocked means blocked."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = exc
            if attempt == 0:
                continue
            raise
        if resp.status_code in BLOCKED_CODES:
            raise BlockedError(f"HTTP {resp.status_code} from {url.split('/')[2]}")
        if resp.status_code >= 500 and attempt == 0:
            last = requests.HTTPError(f"HTTP {resp.status_code}")
            continue
        resp.raise_for_status()
        break
    else:  # pragma: no cover - loop always breaks or raises
        raise last if last else RuntimeError("fetch failed")
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
        raise ValueError(f"not an HTML page ({ctype.split(';')[0] or 'unknown type'})")
    return resp.text


def normalise_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value)
    hit = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if hit:
        return hit.group(0)
    hit = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if hit:
        month, day, year = (int(x) for x in hit.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:20].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n\n".join(part.strip() for part in text.split("\n\n") if part.strip())


def extract_links(html: str, base_url: str) -> list[str]:
    """Outbound links that appear inside the article body (i.e. its citations)."""
    try:
        markdown = trafilatura.extract(
            html,
            url=base_url,
            output_format="markdown",
            include_links=True,
            include_formatting=False,
            include_comments=False,
            favor_recall=True,
        )
    except Exception:
        return []
    if not markdown:
        return []
    raw = re.findall(r"\]\(\s*<?(https?://[^)\s>]+)", markdown)
    out: list[str] = []
    seen: set[str] = set()
    for link in raw:
        host = re.sub(r"^https?://", "", link).split("/")[0].lower()
        if any(host == j or host.endswith("." + j) for j in JUNK_HOSTS):
            continue
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
    return out


def extract(
    url: str,
    html: str | None = None,
    area: str | None = None,
    min_words: int = MIN_WORDS,
    want_links: bool = False,
) -> dict | None:
    """Return a saveable article dict, or None if the page has no real body."""
    html = html if html is not None else fetch(url)
    doc = trafilatura.bare_extraction(
        html,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=True,
        favor_recall=False,
    )
    if doc is None:
        return None
    text = clean_text(getattr(doc, "text", "") or "")
    words = len(text.split())
    if words < min_words:
        return None

    title = (getattr(doc, "title", None) or "").strip()
    site = (getattr(doc, "sitename", None) or "").strip()
    if not title:
        head = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if head:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", head.group(1))).strip()
    if not title:
        title = url

    article = {
        "url": url,
        "title": title,
        "text": text,
        "source": site or None,
        "published": normalise_date(getattr(doc, "date", None)),
        "area": area,
        "method": "extract",
    }
    if want_links:
        article["links"] = extract_links(html, url)
    return article


def looks_like_feed(text: str) -> bool:
    head = (text or "")[:400].lower()
    return "<rss" in head or "<feed" in head or "<?xml" in head
