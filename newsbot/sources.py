"""News sources. `kind` drives trust handling:
  govt       official releases (reported as 'the government says')
  news       mainstream outlets
  factcheck  fact-checkers (their verdicts are cited, never overridden by the AI)
  state_gn   Google News search scoped to one state/UT (broad regional coverage)
  niche      specialist / regional / independent outlets (environment, rural, data, courts,
             North-East, J&K, civic). Stories only these carry, and no big national outlet has
             picked up yet, can become a "Spotlight" post.

Feed URLs can change; `python -m newsbot.health weekly` reports dead feeds on Telegram.
"""
from dataclasses import dataclass


@dataclass
class Feed:
    name: str
    url: str
    kind: str = "news"
    state: str = ""
    site: str = ""  # domain used for a Google News "site:" fallback when the direct feed is blocked


NATIONAL_FEEDS = [
    Feed("PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "govt", site="pib.gov.in"),
    Feed("RBI", "https://www.rbi.org.in/pressreleases_rss.xml", "govt"),
    Feed("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    Feed("Indian Express", "https://indianexpress.com/section/india/feed/", site="indianexpress.com"),
    Feed("NDTV", "https://feeds.feedburner.com/ndtvnews-india-news"),
    Feed("Times of India", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
    Feed("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    Feed("India Today", "https://www.indiatoday.in/rss/1206514"),
    Feed("News18", "https://www.news18.com/rss/india.xml"),
    Feed("The Print", "https://theprint.in/feed/", site="theprint.in"),
    Feed("Scroll", "https://feeds.feedburner.com/ScrollinArticles.rss"),
    Feed("Mint", "https://www.livemint.com/rss/news"),
    Feed("Business Standard", "https://www.business-standard.com/rss/home_page_top_stories.rss", site="business-standard.com"),
    Feed("Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
    Feed("Alt News", "https://www.altnews.in/feed/", "factcheck"),
    Feed("BOOM", "https://www.boomlive.in/feed", "factcheck", site="boomlive.in"),
    # Topic searches (national coverage beyond headlines)
    Feed("Google News", "https://news.google.com/rss/headlines/section/geo/India?hl=en-IN&gl=IN&ceid=IN:en"),
]

TOPIC_QUERIES = {
    "Economy": "India economy RBI inflation GDP",
    "Sports": "India cricket OR hockey OR football OR Olympics",
    "Science & Tech": "ISRO OR India technology OR India science",
    "Health": "India health ministry OR AIIMS OR disease outbreak India",
    "Weather & Disaster": "India IMD weather OR flood OR cyclone OR earthquake",
    "Courts": "Supreme Court of India OR High Court order",
    "Defence": "Indian Army OR Indian Navy OR Indian Air Force",
    "Agriculture": "India farmers OR monsoon OR MSP",
}

STATES_AND_UTS = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Andaman and Nicobar", "Lakshadweep", "Dadra and Nagar Haveli and Daman and Diu",
]


def _gn(query: str) -> str:
    """Google News search feed. The 'when:' window follows ARTICLE_MAX_AGE_HOURS."""
    from urllib.parse import quote_plus
    from . import config
    days = max(1, -(-config.ARTICLE_MAX_AGE_HOURS // 24))
    return (
        f"https://news.google.com/rss/search?q={quote_plus(f'{query} when:{days}d')}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )


def gn_site_url(domain: str) -> str:
    return _gn(f"site:{domain}")


def fallback_feed(feed: Feed):
    """Google News 'site:' stand-in for a feed that is blocked or down (discovery only)."""
    if not feed.site:
        return None
    return Feed(f"{feed.name} (via Google News)", gn_site_url(feed.site), feed.kind, feed.state)


# Publisher-direct regional feeds (preferred over Google News where they exist).
REGIONAL_FEEDS = [
    Feed("The Hindu (Tamil Nadu)", "https://www.thehindu.com/news/national/tamil-nadu/feeder/default.rss", "news", "Tamil Nadu"),
    Feed("The Hindu (Kerala)", "https://www.thehindu.com/news/national/kerala/feeder/default.rss", "news", "Kerala"),
    Feed("The Hindu (Karnataka)", "https://www.thehindu.com/news/national/karnataka/feeder/default.rss", "news", "Karnataka"),
    Feed("The Hindu (Andhra Pradesh)", "https://www.thehindu.com/news/national/andhra-pradesh/feeder/default.rss", "news", "Andhra Pradesh"),
    Feed("The Hindu (Telangana)", "https://www.thehindu.com/news/national/telangana/feeder/default.rss", "news", "Telangana"),
]


# Specialist, regional and independent outlets that big national outlets often miss.
# Direct RSS where it exists (checked), otherwise a Google News "site:" search (discovery only).
# No partisan outlets: the goal is under-covered *facts*, not under-covered opinions.
NICHE_FEEDS = [
    Feed("Mongabay India", "https://india.mongabay.com/feed/", "niche", site="india.mongabay.com"),
    Feed("Bar & Bench", "https://www.barandbench.com/feed", "niche", site="barandbench.com"),
    Feed("EastMojo", "https://www.eastmojo.com/feed/", "niche", site="eastmojo.com"),
    Feed("Northeast Now", "https://nenow.in/feed", "niche", site="nenow.in"),
    Feed("The Sentinel (Assam)", "https://www.sentinelassam.com/feed", "niche", "Assam", site="sentinelassam.com"),
    Feed("Kashmir Observer", "https://kashmirobserver.net/feed/", "niche", "Jammu and Kashmir", site="kashmirobserver.net"),
    Feed("Citizen Matters", "https://citizenmatters.in/feed", "niche", site="citizenmatters.in"),
    Feed("The Better India", "https://www.thebetterindia.com/feed/", "niche", site="thebetterindia.com"),
    Feed("Telangana Today", "https://telanganatoday.com/feed", "niche", "Telangana", site="telanganatoday.com"),
    Feed("Gaon Connection", "https://en.gaonconnection.com/feed/", "niche", site="en.gaonconnection.com"),
    # Region-specific (added 19 Oct). Each has a Google News site: fallback if the direct feed is wrong or blocked.
    Feed("Imphal Free Press", "https://www.ifp.co.in/feed", "niche", "Manipur", site="ifp.co.in"),
    Feed("The Shillong Times", "https://theshillongtimes.com/feed/", "niche", "Meghalaya", site="theshillongtimes.com"),
    Feed("Greater Kashmir", "https://www.greaterkashmir.com/feed", "niche", "Jammu and Kashmir", site="greaterkashmir.com"),
    Feed("Daily Excelsior", "https://www.dailyexcelsior.com/feed/", "niche", "Jammu and Kashmir", site="dailyexcelsior.com"),
]
# Fact-checkers beyond Alt News and BOOM. Their verdicts are cited, never overridden by the AI.
FACTCHECK_FEEDS = [
    Feed("Factly", "https://factly.in/feed/", "factcheck", site="factly.in"),
    Feed("Newschecker", "https://newschecker.in/feed", "factcheck", site="newschecker.in"),
    Feed("Vishvas News", "https://www.vishvasnews.com/english/feed/", "factcheck", site="vishvasnews.com"),
    Feed("FactCrescendo", "https://english.factcrescendo.com/feed/", "factcheck", site="english.factcrescendo.com"),
]
# Only reachable through Google News site: search (no direct feed checked)
NICHE_SITE_ONLY = [
    ("Down To Earth", "downtoearth.org.in", ""),
    ("The News Minute", "thenewsminute.com", ""),
    ("LiveLaw", "livelaw.in", ""),
    ("IndiaSpend", "indiaspend.com", ""),
    ("People's Archive of Rural India", "ruralindiaonline.org", ""),
    # Region-specific, reached through Google News site: search
    ("The Tribune (Punjab)", "tribuneindia.com", "Punjab"),
    ("Deccan Herald", "deccanherald.com", "Karnataka"),
    ("Herald Goa", "heraldgoa.in", "Goa"),
    ("OdishaTV", "odishatv.in", "Odisha"),
    ("Sambad English", "sambadenglish.com", "Odisha"),
    ("Nagaland Post", "nagalandpost.com", "Nagaland"),
    ("Arunachal Times", "arunachaltimes.in", "Arunachal Pradesh"),
    ("Tripura Times", "tripuratimes.com", "Tripura"),
    ("Sikkim Express", "sikkimexpress.com", "Sikkim"),
    ("Reach Ladakh", "reachladakh.com", "Ladakh"),
    ("Free Press Journal (MP)", "freepressjournal.in", ""),
    ("The Pioneer (Uttarakhand)", "dailypioneer.com", ""),
    ("The New Indian Express", "newindianexpress.com", ""),
    ("The Assam Tribune", "assamtribune.com", "Assam"),
]


CELEBRITY_DIRECT = [
    Feed("Times of India (Entertainment)", "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms", site="timesofindia.indiatimes.com"),
    Feed("Hindustan Times (Entertainment)", "https://www.hindustantimes.com/feeds/rss/entertainment/rssfeed.xml", site="hindustantimes.com"),
    Feed("Indian Express (Entertainment)", "https://indianexpress.com/section/entertainment/feed/", site="indianexpress.com"),
    Feed("The Hindu (Entertainment)", "https://www.thehindu.com/entertainment/feeder/default.rss", site="thehindu.com"),
    Feed("Times of India (Sports)", "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms", site="timesofindia.indiatimes.com"),
    Feed("Hindustan Times (Cricket)", "https://www.hindustantimes.com/feeds/rss/cricket/rssfeed.xml", site="hindustantimes.com"),
    Feed("Indian Express (Sports)", "https://indianexpress.com/section/sports/feed/", site="indianexpress.com"),
    Feed("The Hindu (Sport)", "https://www.thehindu.com/sport/feeder/default.rss", site="thehindu.com"),
]
CELEBRITY_TOPICS = {
    "Film": "Bollywood OR Tollywood OR Kollywood OR Malayalam film actor",
    "Cricket": "Indian cricketer OR BCCI OR IPL player",
    "Music": "Indian singer OR music composer concert OR album",
    "Business": "Indian billionaire OR industrialist OR startup founder",
}


def celebrity_feeds(use_google_news: bool = None) -> list:
    """Small, fast set for the 20-minute celebrity watch: entertainment, sports and business sections,
    topical searches, and searches for the best-known names (see celebrities.py)."""
    from . import celebrities, config
    if use_google_news is None:
        use_google_news = config.USE_GOOGLE_NEWS
    feeds = list(CELEBRITY_DIRECT)
    if use_google_news:
        for topic, q in CELEBRITY_TOPICS.items():
            feeds.append(Feed(f"GN:{topic}", _gn(q), "news"))
        groups = [celebrities.ENTERTAINMENT[:20], celebrities.ENTERTAINMENT[20:40], celebrities.SPORTS[:20],
                  celebrities.SPORTS[20:40], celebrities.BUSINESS[:10]]
        for n, names in enumerate(groups, 1):
            feeds.append(Feed(f"GN:Names {n}", _gn(" OR ".join(f'"{x}"' for x in names)), "news"))
    return feeds


def all_feeds(use_google_news: bool = None) -> list:
    """Google News feeds are discovery-only (headline + source + link). Their terms for
    republication are unclear, so they can be switched off with USE_GOOGLE_NEWS=0."""
    from . import config
    if use_google_news is None:
        use_google_news = config.USE_GOOGLE_NEWS
    feeds = [f for f in NATIONAL_FEEDS if use_google_news or f.name != "Google News"]
    feeds += REGIONAL_FEEDS
    feeds += NICHE_FEEDS
    feeds += FACTCHECK_FEEDS
    if use_google_news:
        for name, domain, state in NICHE_SITE_ONLY:
            feeds.append(Feed(f"{name} (via Google News)", gn_site_url(domain), "niche", state))
        for topic, q in TOPIC_QUERIES.items():
            feeds.append(Feed(f"GN:{topic}", _gn(q), "news"))
        for st in STATES_AND_UTS:
            feeds.append(Feed(f"GN:{st}", _gn(f"{st} news"), "state_gn", state=st))
    return feeds


CATEGORIES = [
    "Politics", "Government & Policy", "Economy & Business", "Crime & Law", "Courts",
    "Weather & Disaster", "Health", "Education", "Science & Tech", "Sports",
    "Entertainment", "Environment", "Infrastructure & Transport", "International",
    "Society", "Agriculture", "Defence", "Fact Check",
]
