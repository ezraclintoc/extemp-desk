# Extemp Desk

![vibe coded](https://img.shields.io/badge/vibe--coded-100%25-f0b429)

Local article filing for NCFCA extemporaneous speaking. Three things, the ones that
matter at the prep table:

| | What it does |
|---|---|
| **Auto-File** | Bulk-downloads every recent article from the sources you enabled, grouped by topic area |
| **Query-File** | Searches Bing News, GDELT and DuckDuckGo at once, ranks the hits, files the good ones, and can chase the articles they cite |
| **Article Extractor** | Turns any article URL into clean body text — no ads, no nav, no images |

Everything is stored in a local SQLite database on your machine. Once you have filed,
**you never need the internet again** — search and reading run entirely offline, which
is exactly what NCFCA requires during prep.

No account, no subscription, no cloud, nothing leaves your laptop.

> ### 100% vibe coded
>
> Every line here was written by an AI coding agent (Codebuff) in a single session, from
> a competitor's plain-English feature list. There is no human-written code, no design
> document, and no test suite written by a person. It has been run, and the numbers in
> [COMPARISON.md](COMPARISON.md) come from real runs against live feeds — but "it works
> on my machine" is the only warranty you get. Read the code before trusting it with
> your season, and file a bug when it eats your library.

---

## Install

Needs Python 3.10+ and a normal laptop (Windows / macOS / Linux). On Windows, install
Python from python.org and use `py` wherever this says `python3`.

```bash
git clone https://github.com/ezraclintoc/extemp-desk.git
cd extemp-desk
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python3 app.py --open
```

It serves on `http://127.0.0.1:8765/`, prints how many articles you have and how much
disk they use. `--open` launches your browser; or just visit the URL. Stop with Ctrl-C.

Your library lives in `data/library.db`. **Back it up before a tournament** — copying
that one file (plus `library.db-wal` if present, or just stop the app first) moves your
whole library to another computer.

---

## The workflow that matches an NCFCA season

NCFCA rules for Extemp: two topics, each about a newsworthy event from the **past 90
days**; 20 minutes prep; 7-minute speech; and during prep you may use published material
you **previously downloaded**, but **not the internet** — and not "previously prepared
notes or analysis, including debate briefs or speech outlines."

That shapes how you should use this tool:

1. **A few days before a tournament**, open *Sources*, tick what you want, then *File
   articles → Auto-File* with *last 90 days*. A few thousand articles land in the library.
2. **Fill topic gaps with Query-File.** Auto-File gives you the news cycle; Query-File is
   for the things you expect to draw — `semiconductor export controls China`,
   `federal reserve rate cut`, `farm bill subsidies`. Tick *Chase citations* to also pull
   the sources those articles link to.
3. **File one-offs** with the Article Extractor whenever you read something good.
4. **At the tournament**, open the Search tab. Search is instant and offline: type a few
   keywords, keep the *90 days* filter on, and read.

The 90-day filter is on by default because NCFCA topics come from that window; anything
older that sneaks in through a citation is skipped rather than filed.

> **Compliance note.** This tool only stores and searches published articles — it does not
> generate outlines, briefs, or analysis, because NCFCA bars using pre-prepared analysis
> during prep. It is a filing cabinet with a search box. Whether a device is allowed in
> the prep room, and how it must be used, is governed by the current NCFCA rules
> (ncfca.org/rules) — read them yourself each season, they change.

---

## Sources tab

Auto-File pulls from whatever is ticked. The 39 built-ins are grouped by area (US
Politics, Economy, Foreign, Science/Tech, Health, Courts/Law, Policy) and all start
enabled; untick anything you consider low value so the run is faster and your library is
sharper. See [COMPARISON.md](COMPARISON.md) for how this list was chosen and where it
still loses to Extemp Genie.

- **Add source** — paste any RSS/Atom URL. Name and area are optional; the host is used
  as the name otherwise. Added sources persist in the library database.
- **Test** — fetches the feed and reports how many recent items it carries, or the error.
  Test after adding; a feed that 404s looks exactly like a feed with no news.
- **Test all** — checks every source in parallel (about 10 seconds for 39). Results are
  cached on each row with the date, so you can see at a glance what has rotted.
- **Enable all / Disable all** — bulk toggles.

Built-in feeds can be disabled but not deleted. Custom ones you can remove.

## Query-File

Type one query per line; each line is a separate search and all results are merged into
one ranked batch before anything is downloaded.

| Syntax | Meaning |
|---|---|
| `tariff China` | all words required |
| `"supply chain"` | exact phrase |
| `tariff -sports` | exclude a word |
| `site:reuters.com federal reserve` | restrict to a domain (`site:`/`domain:`) |

Each engine is asked in syntax it actually understands (GDELT gets `domainis:`,
Bing and DuckDuckGo get `site:`), rather than the raw string with fingers crossed.

What "robust" means here, concretely:

- **Retries** with backoff on 429/5xx/timeouts, and one retry per page download.
- **Engine policy per engine.** GDELT throttles to one call every 5–7 seconds, so it gets
  its own 7-second spacing and a single attempt.
- **Circuit breaker.** An engine that refuses the first query is skipped for the rest of
  the run instead of costing you its timeout on every query. The job log says which
  engines answered, how many hits, and how long each took.
- **Ranking before truncation.** Hits are scored on phrase/term matches in the headline,
  domain match, recency and exclusions; the top N are downloaded, not the first N that
  arrive.
- **Cross-engine duplicate collapsing** (same host + near-identical headline) plus
  **store-level dedupe**: the same story reached by two URLs, or found again next week
  under a slightly different headline, is not filed twice.
- **Query relaxation.** If an operator-heavy query returns literally nothing, it retries
  once without the operators rather than reporting zero.

Missing a story because a search engine misbehaved is the failure mode that actually
loses rounds, so the run degrades to fewer sources instead of failing.

## Storage

The header shows the library's size on disk, and the File tab breaks it down: total disk,
article text, average per article, and search-index size. Rough scale from real
runs — roughly 4–6 KB of text per article, and the full-text index costs about the same
again, so **1,000 articles ≈ 20 MB**. A season of aggressive filing stays comfortably
under a few hundred MB.

Everything lives in one SQLite file, so backup is `cp data/library.db backup.db`.

## Search syntax

| You type | You get |
|---|---|
| `tariff China` | articles containing both, ranked with title matches heavily weighted |
| `"supply chain"` | that exact phrase |
| `tariff -sports` | tariff, minus articles that mention sports |
| `congress` | also matches *congressional*, *congressman* (prefix matching) |

Sources and dates are filterable from the controls above the results. `/` focuses the
search box from anywhere.

## Limits worth knowing

- **Paywalls.** WSJ, NYT, WaPo, The Hill and similar block automated fetching. Those
  pages are counted as `blocked` — honestly reported, not silently dropped — and you get
  nothing from them unless you add them with a subscription-accessible feed. Wire
  services, NPR, BBC, Guardian, Politico, think tanks and government sources file cleanly.
- **Chromebooks.** This is a Python program, so it will not run on a managed school
  Chromebook. Run it on a Windows/Mac/Linux laptop and bring that to prep, or file on one
  machine and copy `data/library.db` to another.
- Auto-File at 40 articles/feed across 39 feeds is ~1,500 pages; expect that run to take a
  few minutes. Per-feed caps keep it sane.

## Layout

```
app.py                 entry point, CLI flags (--port --db --open --check-feeds)
extempdesk/db.py       SQLite schema, FTS5 search ranking, dedupe, feed config
extempdesk/extract.py  reader-mode extraction (trafilatura) + citation links
extempdesk/filer.py    Auto-File (RSS) and Query-File (engines, ranking, dedupe)
extempdesk/sources.py  the 39 default feeds
extempdesk/jobs.py     background jobs + progress log
extempdesk/server.py   JSON API + static files, bound to 127.0.0.1
static/                the whole UI, no build step, no CDN
COMPARISON.md          source coverage vs Extemp Genie, with measured numbers
```
