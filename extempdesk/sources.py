"""Default source list, grouped by the topic areas NCFCA extemp draws from."""

from __future__ import annotations

# (display name, feed URL, area)
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    # ---------------------------------------------------------------- US politics
    ("NPR Politics", "https://feeds.npr.org/1014/rss.xml", "US Politics"),
    ("NPR National", "https://feeds.npr.org/1003/rss.xml", "US Politics"),
    ("BBC US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "US Politics"),
    ("Guardian US News", "https://www.theguardian.com/us-news/rss", "US Politics"),
    ("Politico Politics", "https://rss.politico.com/politics-news.xml", "US Politics"),
    ("Politico Congress", "https://rss.politico.com/congress.xml", "US Politics"),
    ("The Hill", "https://thehill.com/feed/", "US Politics"),
    ("NYT Politics", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "US Politics"),
    ("Washington Post Politics", "https://feeds.washingtonpost.com/rss/politics", "US Politics"),
    ("Axios", "https://api.axios.com/feed/", "US Politics"),
    # ------------------------------------------------------------------- economy
    ("NPR Business", "https://feeds.npr.org/1006/rss.xml", "Economy"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "Economy"),
    ("Guardian Business", "https://www.theguardian.com/uk/business/rss", "Economy"),
    ("NYT Economy", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "Economy"),
    ("Federal Reserve Press", "https://www.federalreserve.gov/feeds/press_all.xml", "Economy"),
    # ------------------------------------------------------------------ foreign
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "Foreign"),
    ("Guardian World", "https://www.theguardian.com/world/rss", "Foreign"),
    ("NPR World", "https://feeds.npr.org/1019/rss.xml", "Foreign"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Foreign"),
    ("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "Foreign"),
    ("Foreign Policy", "https://foreignpolicy.com/feed/", "Foreign"),
    ("Foreign Affairs", "https://www.foreignaffairs.com/rss.xml", "Foreign"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "Foreign"),
    ("State Dept Releases", "https://www.state.gov/rss-feed/press-releases/feed/", "Foreign"),
    ("Defense Dept", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?max=10&ContentType=1&Site=945", "Foreign"),
    # ------------------------------------------------------------- science/tech
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "Science/Tech"),
    ("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "Science/Tech"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "Science/Tech"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "Science/Tech"),
    ("NPR Science", "https://feeds.npr.org/1007/rss.xml", "Science/Tech"),
    ("NPR Health", "https://feeds.npr.org/1128/rss.xml", "Health"),
    ("KFF Health News", "https://kffhealthnews.org/feed/", "Health"),
    # -------------------------------------------------------------- courts / law
    ("SCOTUSblog", "https://www.scotusblog.com/feed/", "Courts/Law"),
    ("ABA Journal", "https://www.abajournal.com/rss/news", "Courts/Law"),
    # ---------------------------------------------------------- analysis / policy
    ("Pew Research", "https://www.pewresearch.org/feed/", "Policy"),
    ("RAND", "https://www.rand.org/news.xml", "Policy"),
    ("Reason", "https://reason.com/feed/", "Policy"),
    ("Brookings", "https://www.brookings.edu/feed/?post_type=article", "Policy"),
    ("The Atlantic", "https://www.theatlantic.com/feed/all/", "Policy"),
]

AREAS = sorted({area for _, _, area in DEFAULT_FEEDS})
