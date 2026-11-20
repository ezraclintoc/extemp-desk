"""SQLite storage + FTS5 full-text search for the article library."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "gclid", "dclid", "msclkid", "mc_cid",
    "mc_eid", "ref", "referrer", "source", "cmpid", "partner", "smid",
    "soc_src", "soc_trk", "at_medium", "at_campaign", "taid", "__twitter_impression",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL,
    url_key     TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    source      TEXT,
    area        TEXT,
    published   TEXT,
    fetched_at  TEXT NOT NULL,
    word_count  INTEGER NOT NULL DEFAULT 0,
    method      TEXT,
    title_key   TEXT,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);

CREATE TABLE IF NOT EXISTS feeds (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    area        TEXT NOT NULL DEFAULT 'Custom',
    enabled     INTEGER NOT NULL DEFAULT 1,
    builtin     INTEGER NOT NULL DEFAULT 0,
    last_checked TEXT,
    last_status TEXT,
    last_count  INTEGER,
    last_error  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    text,
    content='articles',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, text)
    VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, text)
    VALUES ('delete', old.id, old.title, old.text);
    INSERT INTO articles_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
"""


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article filed twice is not stored twice."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc:
        return url
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme or "https", host, path, urlencode(query), ""))


def url_key(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8", "ignore")).hexdigest()


OUTLET_TAIL = re.compile(
    r"\s+[-|–]\s+(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\.?$"
)


def title_tokens(title: str) -> set[str]:
    text = OUTLET_TAIL.sub("", (title or "").strip())
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return {word for word in text.split() if len(word) > 1}


def titles_similar(left: str, right: str, threshold: float = 0.85) -> bool:
    """Two headlines for the same story, phrased or suffixed slightly differently."""
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def title_key(title: str, published: str | None = None) -> str:
    """Fingerprint of a headline, so the same story filed twice is caught.

    Bing appends ' - Publisher' to headlines and feeds differ on punctuation,
    so strip outlet tails, punctuation and word order before hashing. The date
    is kept in the key: two pieces can legitimately share a headline on
    different days, but not on the same one.
    """
    text = OUTLET_TAIL.sub("", (title or "").strip())
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    words = sorted(word for word in text.split() if len(word) > 1)
    if not words:
        return ""
    stamp = (published or "")[:10]
    return hashlib.sha1((" ".join(words) + "|" + stamp).encode("utf-8", "ignore")).hexdigest()


def build_match(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH expression.

    Supports quoted phrases ("supply chain") and negatives (-tariff).
    Every bare term is prefix-matched, which is what you want mid-round.
    """
    tokens = re.findall(r'"[^"]*"|\S+', query or "")
    must: list[str] = []
    must_not: list[str] = []
    for raw in tokens:
        negate = raw.startswith("-") and len(raw) > 1
        token = raw[1:] if negate else raw
        if token.startswith('"') and token.endswith('"') and len(token) > 2:
            phrase = re.sub(r'["*]', "", token)
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if not phrase:
                continue
            expr = f'"{phrase}"'
        else:
            term = re.sub(r'["*()^:]', " ", token)
            term = re.sub(r"\s+", " ", term).strip()
            if not term:
                continue
            expr = f'"{term}"*'
        (must_not if negate else must).append(expr)
    if not must:
        return ""
    expression = " AND ".join(must)
    for bad in must_not:
        expression += f" NOT {bad}"
    return expression


class Store:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            if not conn.execute("SELECT 1 FROM feeds LIMIT 1").fetchone():
                from .sources import DEFAULT_FEEDS

                conn.executemany(
                    "INSERT OR IGNORE INTO feeds (url, name, area, enabled, builtin)"
                    " VALUES (?,?,?,1,1)",
                    [(url, name, area) for name, url, area in DEFAULT_FEEDS],
                )
                conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring libraries created by older builds up to the current schema."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
        if "title_key" not in columns:
            conn.execute("ALTER TABLE articles ADD COLUMN title_key TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_title_key ON articles(title_key)")
        missing = conn.execute(
            "SELECT id, title, published FROM articles WHERE title_key IS NULL"
        ).fetchall()
        if missing:
            conn.executemany(
                "UPDATE articles SET title_key = ? WHERE id = ?",
                [(title_key(row["title"], row["published"]), row["id"]) for row in missing],
            )
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    # ---------------------------------------------------------------- writes
    def save(self, article: dict) -> tuple[int, bool]:
        """Insert an article. Returns (id, created)."""
        url = article["url"]
        title = (article.get("title") or url).strip()
        text = article["text"]
        published = article.get("published")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        key = title_key(title, published)
        row = (
            url,
            url_key(url),
            title,
            article.get("source"),
            article.get("area"),
            published,
            now,
            len(text.split()),
            article.get("method", "extract"),
            key,
            text,
        )
        with self._write_lock:
            conn = self._conn()
            if published:
                neighbours = conn.execute(
                    "SELECT id, title FROM articles WHERE published = ? LIMIT 500",
                    (published[:10],),
                ).fetchall()
                for neighbour in neighbours:
                    if titles_similar(title, neighbour["title"]):
                        return int(neighbour["id"]), False
            if key:
                seen = conn.execute(
                    "SELECT id FROM articles WHERE title_key = ? LIMIT 1", (key,)
                ).fetchone()
                if seen:
                    return int(seen["id"]), False
            cur = conn.execute(
                """INSERT OR IGNORE INTO articles
                   (url, url_key, title, source, area, published, fetched_at,
                    word_count, method, title_key, text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            conn.commit()
            if cur.rowcount:
                return int(cur.lastrowid), True
            found = conn.execute(
                "SELECT id FROM articles WHERE url_key = ?", (row[1],)
            ).fetchone()
            return (int(found["id"]) if found else -1), False

    def exists(self, url: str) -> bool:
        conn = self._conn()
        hit = conn.execute(
            "SELECT 1 FROM articles WHERE url_key = ?", (url_key(url),)
        ).fetchone()
        return hit is not None

    def existing_keys(self, urls) -> set[str]:
        conn = self._conn()
        keys = {url_key(u) for u in urls}
        if not keys:
            return set()
        found: set[str] = set()
        batch = list(keys)
        for i in range(0, len(batch), 400):
            chunk = batch[i : i + 400]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT url_key FROM articles WHERE url_key IN ({marks})", chunk
            ):
                found.add(row["url_key"])
        return found

    def delete(self, article_id: int) -> None:
        with self._write_lock:
            conn = self._conn()
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            conn.commit()

    # ----------------------------------------------------------------- reads
    def search(
        self,
        query: str,
        days: int | None = 90,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
        with_body: bool = False,
    ) -> dict:
        match = build_match(query)
        conn = self._conn()
        where: list[str] = []
        params: list = []
        if days:
            where.append(
                "date(COALESCE(published, substr(fetched_at,1,10))) >= date('now', ?)"
            )
            params.append(f"-{int(days)} days")
        if source:
            where.append("COALESCE(source,'') = ?")
            params.append(source)
        clause = (" AND " + " AND ".join(where)) if where else ""

        if not match:
            # Empty query: newest first browse.
            sql = f"""SELECT id, title, source, published, url, word_count
                      FROM articles WHERE 1=1 {clause}
                      ORDER BY COALESCE(published, substr(fetched_at,1,10)) DESC, id DESC
                      LIMIT ? OFFSET ?"""
            rows = conn.execute(sql, [*params, limit, offset]).fetchall()
            items = [dict(r) for r in rows]
            items = [dict(i, snippet="") for i in items]
            total = conn.execute(
                f"SELECT COUNT(*) c FROM articles WHERE 1=1 {clause}", params
            ).fetchone()["c"]
            return {"items": items, "total": total, "query": match}

        sql = f"""SELECT a.id, a.title, a.source, a.published, a.url, a.word_count,
                         snippet(articles_fts, 1, '<mark>', '</mark>', ' … ', 42) AS snippet,
                         bm25(articles_fts, 6.0, 1.0) AS score
                  FROM articles_fts
                  JOIN articles a ON a.id = articles_fts.rowid
                  WHERE articles_fts MATCH ? {clause}
                  ORDER BY score ASC, COALESCE(a.published, '') DESC
                  LIMIT ? OFFSET ?"""
        rows = conn.execute(sql, [match, *params, limit, offset]).fetchall()
        total = conn.execute(
            f"""SELECT COUNT(*) c FROM articles_fts
                JOIN articles a ON a.id = articles_fts.rowid
                WHERE articles_fts MATCH ? {clause}""",
            [match, *params],
        ).fetchone()["c"]
        return {"items": [dict(r) for r in rows], "total": total, "query": match}

    def get(self, article_id: int) -> dict | None:
        row = self._conn().execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        conn = self._conn()
        row = conn.execute(
            """SELECT COUNT(*) total,
                      MIN(COALESCE(published, substr(fetched_at,1,10))) oldest,
                      MAX(COALESCE(published, substr(fetched_at,1,10))) newest,
                      COUNT(DISTINCT COALESCE(source,'')) sources,
                      SUM(CASE WHEN COALESCE(published, substr(fetched_at,1,10))
                               >= date('now','-90 days') THEN 1 ELSE 0 END) last90
               FROM articles"""
        ).fetchone()
        return dict(row)

    # ------------------------------------------------------------------ feeds
    def feeds(self) -> list[dict]:
        rows = self._conn().execute(
            """SELECT id, url, name, area, enabled, builtin, last_checked,
                      last_status, last_count, last_error
               FROM feeds ORDER BY area, name COLLATE NOCASE"""
        ).fetchall()
        return [dict(r, enabled=bool(r["enabled"]), builtin=bool(r["builtin"])) for r in rows]

    def enabled_feeds(self) -> list[tuple[str, str, str]]:
        rows = self._conn().execute(
            "SELECT name, url, area FROM feeds WHERE enabled = 1 ORDER BY area, name"
        ).fetchall()
        return [(r["name"], r["url"], r["area"]) for r in rows]

    def feed_add(self, url: str, name: str = "", area: str = "Custom") -> dict:
        host = url.split("//", 1)[-1].split("/")[0].replace("www.", "")
        with self._write_lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR IGNORE INTO feeds (url, name, area, enabled, builtin)"
                " VALUES (?,?,?,1,0)",
                (url, (name or host).strip(), (area or "Custom").strip()),
            )
            conn.commit()
        row = conn.execute("SELECT * FROM feeds WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else {}

    def feed_update(self, feed_id: int, **fields) -> None:
        allowed = {"name", "area", "enabled", "last_checked", "last_status", "last_count", "last_error"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        with self._write_lock:
            conn = self._conn()
            assignments = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(
                f"UPDATE feeds SET {assignments} WHERE id = ?",
                [*[int(v) if k == "enabled" else v for k, v in sets.items()], int(feed_id)],
            )
            conn.commit()

    def feed_set_enabled(self, enabled: bool, feed_id: int | None = None) -> None:
        with self._write_lock:
            conn = self._conn()
            if feed_id is None:
                conn.execute("UPDATE feeds SET enabled = ?", (int(enabled),))
            else:
                conn.execute("UPDATE feeds SET enabled = ? WHERE id = ?", (int(enabled), int(feed_id)))
            conn.commit()

    def feed_delete(self, feed_id: int) -> None:
        """Custom feeds can be removed; built-ins can only be disabled."""
        with self._write_lock:
            conn = self._conn()
            conn.execute("DELETE FROM feeds WHERE id = ? AND builtin = 0", (int(feed_id),))
            conn.commit()

    def footprint(self) -> dict:
        """How much disk the library actually occupies."""
        disk = 0
        for suffix in ("", "-wal", "-shm"):
            path = Path(self.path + suffix)
            if path.exists():
                disk += path.stat().st_size
        conn = self._conn()
        row = conn.execute(
            """SELECT COALESCE(SUM(LENGTH(text)),0) AS body,
                      COALESCE(SUM(LENGTH(title)),0) AS titles,
                      COUNT(*) AS n
               FROM articles"""
        ).fetchone()
        # FTS5 keeps its index in shadow tables; sum them for a true breakdown.
        index_bytes = 0
        for table, column in (("articles_fts_data", "block"), ("articles_fts_docsize", "sz")):
            try:
                got = conn.execute(
                    f"SELECT COALESCE(SUM(LENGTH({column})),0) b FROM {table}"
                ).fetchone()["b"]
                index_bytes += int(got or 0)
            except sqlite3.Error:
                pass
        n = row["n"] or 0
        body = int(row["body"] or 0)
        return {
            "disk_bytes": disk,
            "text_bytes": body + int(row["titles"] or 0),
            "index_bytes": index_bytes,
            "avg_article_bytes": int((body + int(row["titles"] or 0)) / n) if n else 0,
            "articles": n,
        }

    def sources(self) -> list[dict]:
        rows = self._conn().execute(
            """SELECT COALESCE(NULLIF(source,''), 'unknown') AS source, COUNT(*) AS n
               FROM articles GROUP BY 1 ORDER BY n DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
