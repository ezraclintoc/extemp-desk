# Source coverage: Extemp Desk vs Extemp Genie

Both tools do the same trick — download a corpus of articles ahead of time so you can
search it with the internet off. The difference is the corpus.

**How this was measured.** All Extemp Desk numbers come from runs on this machine on
2026-11-20: a full default Auto-File (every enabled feed, last 90 days, 20 articles per
feed) into an empty library, plus a per-source probe of the outlets Extemp Desk does not
carry. Genie's numbers are its own public claims (extempgenie.com, Nov 2026) — I could not
inspect its source list directly (the site would not load from this host and there is no
published list), and I have no licence to run it, so any cell marked *unknown* is exactly
that.

## The headline

| | Extemp Desk | Extemp Genie |
|---|---|---|
| Built-in sources | **39** | **69** (by its own count) |
| Custom sources | Yes, unlimited, with per-source test + health cache | Yes |
| Distinct outlets in one 90-day run | **21** (measured) | *unknown* |
| Articles per default run | **427** at 20/feed; ~1,500 at the 40/feed cap (measured/estimated) | "hundreds of thousands" claimed, cumulative across a backfill |
| Paywalled national dailies (NYT, WaPo, WSJ, Bloomberg, FT) | Blocked — HTTP 403 / empty feed (measured) | *unknown*; The Economist is included via subscription (vendor FAQ) |
| Query-File engines | 3 named: Bing News, GDELT, DuckDuckGo | "several search engines" (unspecified) |
| Citation chasing | Yes, depth-1, links captured during extraction | Yes |
| Offline search | Yes | Yes |
| Source health testing | Yes | *unknown* |
| Folders, evidence cards, practice questions | No (deliberately out of scope) | Yes |

So: **Genie has roughly 30 more configured sources than Extemp Desk, and at least one
premium paywalled source (The Economist) that Extemp Desk cannot reach at all.** That is
the real gap, and it is not a matter of feed plumbing — it is subscriptions and
bot-wall bypasses that a free local tool does not have.

## What the 21 outlets were

A full run filed 427 articles from 21 distinct pubs:

> BBC (World/US/Business/Technology/Science), The Guardian (US/Business/World), NPR
> (Politics/National/Business/World/Science/Health), POLITICO (Politics/Congress), Axios,
> Al Jazeera, Foreign Policy, UN News, U.S. State Department, Federal Reserve,
> SCOTUSblog, ABA Journal, Pew Research, RAND, Reason, The Verge, Ars Technica,
> KFF Health News, KJZZ News, plus BBC's own related feeds.

Balance-wise that is: 6 wire/broadcast internationals, 4 US political outlets, 1 business
wire, 3 government/agency primary sources, 4 think tanks, 2 science/tech, 2 health,
2 legal.

Run accounting for that same job: **427 filed · 88 blocked by publishers · 30 network
errors · 6 pages with no article body · 1 article older than the window.** Nothing was
thrown away silently; the job log itemises every category.

## Where Extemp Desk still loses, measured

I probed the standard extemp "must-cite" outlets that are *not* in the default list:

| Outlet | Feed | Article text | Verdict |
|---|---|---|---|
| New York Times | works | **403 Forbidden** | not fillable |
| Washington Post | works | read timeout | not fillable |
| Wall Street Journal | feed returns 0 recent items | — | not fillable |
| Bloomberg | works | **403** | not fillable |
| Financial Times | works | **403** | not fillable |
| The Economist | works | **403** | not fillable |
| The Hill | works | **403** | not fillable |
| defense.gov | works | **403** | not fillable |
| Christian Science Monitor | **404** (RSS retired) | — | no feed |
| CSIS | **404** | — | no feed |
| Council on Foreign Relations | **404** | — | no feed |

Those are the holes. If you cite NYT or WaPo in every speech, Genie's paid corpus — or a
school library login — is doing something Extemp Desk structurally cannot.

## Where the comparison actually paid off

Three probes did work end-to-end and are now in the default list (39 sources instead of
36):

| Outlet | Feed | Extract |
|---|---|---|
| Foreign Affairs | ok | 3,251 words |
| The Atlantic | ok | 7,246 words |
| Brookings | ok (needed `?post_type=article`; the plain `/feed/` is malformed XML) | 825 words |

That is the honest way to close a coverage gap: probe, then keep what survives. Adding
NYT or The Economist to the list would only produce `blocked` counts, and the Source tab's
**Test** button tells you that in five seconds.

## Practical reading

- If your circuit rewards primary documents, agencies and think tanks, 39 sources covering
  State, Defense (feed only), the Fed, Pew, RAND, Brookings, Foreign Affairs, Foreign
  Policy and Reason is a defensible corpus, and it is free.
- If your circuit expects you to quote the NYT/WaPo/Wall Street Journal front page, Genie's
  paid source pool has real value that no amount of RSS plumbing replaces.
- Query-File partly compensates: it pulls from the live web rather than a fixed list, so if
  a story exists anywhere reachable it can still land in your library — 427 articles from
  feeds is the floor, not the ceiling.

*Numbers reproducible with `python3 app.py --check-feeds` (feed health) and a default
Auto-File run; the library then reports its own size and outlet count in the header.*
