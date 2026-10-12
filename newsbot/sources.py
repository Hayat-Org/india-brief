"""News sources. `kind` drives trust handling:
  govt       official releases (reported as 'the government says')
  news       mainstream outlets
  factcheck  fact-checkers (their verdicts are cited, never overridden by the AI)
  state_gn   Google News search scoped to one state/UT (broad regional coverage)

Feed URLs can change; `python -m newsbot.health weekly` reports dead feeds on Telegram.
"""
from dataclasses import dataclass


@dataclass
class Feed:
    name: str
    url: str
    kind: str = "news"
    state: str = ""


NATIONAL_FEEDS = [
    Feed("PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "govt"),
    Feed("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    Feed("Indian Express", "https://indianexpress.com/section/india/feed/"),
    Feed("NDTV", "https://feeds.feedburner.com/ndtvnews-india-news"),
    Feed("Times of India", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
    Feed("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    Feed("India Today", "https://www.indiatoday.in/rss/1206514"),
    Feed("News18", "https://www.news18.com/rss/india.xml"),
    Feed("The Print", "https://theprint.in/feed/"),
    Feed("Scroll", "https://feeds.feedburner.com/ScrollinArticles.rss"),
    Feed("Mint", "https://www.livemint.com/rss/news"),
    Feed("Business Standard", "https://www.business-standard.com/rss/home_page_top_stories.rss"),
    Feed("Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
    Feed("Alt News", "https://www.altnews.in/feed/", "factcheck"),
    Feed("BOOM", "https://www.boomlive.in/feed", "factcheck"),
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
    from urllib.parse import quote_plus
    return (
        f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )


# Publisher-direct regional feeds (preferred over Google News where they exist).
REGIONAL_FEEDS = [
    Feed("The Hindu (Tamil Nadu)", "https://www.thehindu.com/news/national/tamil-nadu/feeder/default.rss", "news", "Tamil Nadu"),
    Feed("The Hindu (Kerala)", "https://www.thehindu.com/news/national/kerala/feeder/default.rss", "news", "Kerala"),
    Feed("The Hindu (Karnataka)", "https://www.thehindu.com/news/national/karnataka/feeder/default.rss", "news", "Karnataka"),
    Feed("The Hindu (Andhra Pradesh)", "https://www.thehindu.com/news/national/andhra-pradesh/feeder/default.rss", "news", "Andhra Pradesh"),
    Feed("The Hindu (Telangana)", "https://www.thehindu.com/news/national/telangana/feeder/default.rss", "news", "Telangana"),
]


def all_feeds(use_google_news: bool = None) -> list:
    """Google News feeds are discovery-only (headline + source + link). Their terms for
    republication are unclear, so they can be switched off with USE_GOOGLE_NEWS=0."""
    from . import config
    if use_google_news is None:
        use_google_news = config.USE_GOOGLE_NEWS
    feeds = [f for f in NATIONAL_FEEDS if use_google_news or f.name != "Google News"]
    feeds += REGIONAL_FEEDS
    if use_google_news:
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
