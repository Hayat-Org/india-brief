"""Popularity signal: what India is actually searching for right now (Google Trends daily RSS).

Free, no key. If the feed is unavailable the system simply runs without the bonus.
Popularity here only lifts ranking a little; it never overrides safety gates, and it is
topic-neutral (no party/religion/community is favoured).
"""
import re

import requests

from . import config
from .dedup import STOP, normalize_title

TRENDS_URL = "https://trends.google.com/trending/rss?geo=IN"


def _parse_traffic(text: str) -> int:
    m = re.match(r"\s*([\d,.]+)\s*([KkMm]?)", text or "")
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()])


def parse_trends(xml_text: str) -> dict:
    """Return {normalised search term: approx searches}."""
    out = {}
    for item in re.findall(r"<item>(.*?)</item>", xml_text or "", flags=re.S):
        t = re.search(r"<title>(.*?)</title>", item, flags=re.S)
        if not t:
            continue
        term = normalize_title(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)))
        traffic = re.search(r"approx_traffic>([^<]+)<", item)
        if term:
            out[term] = _parse_traffic(traffic.group(1)) if traffic else 0
    return out


def fetch_trends() -> dict:
    try:
        r = requests.get(TRENDS_URL, headers={"User-Agent": config.USER_AGENT}, timeout=20)
        r.raise_for_status()
        return parse_trends(r.text)
    except Exception as ex:
        print(f"trends unavailable: {str(ex)[:100]}")
        return {}


def trend_boost(title: str, trends: dict) -> float:
    """Small ranking bonus when the headline matches a trending search."""
    if not trends:
        return 0.0
    words = {w for w in normalize_title(title).split() if w not in STOP}
    best = 0
    matched = False
    for term, traffic in trends.items():
        toks = [w for w in term.split() if w not in STOP]
        if not toks or not all(t in words for t in toks):
            continue
        if len(toks) == 1 and len(toks[0]) < 6:  # avoid matching tiny/common words
            continue
        matched, best = True, max(best, traffic)
    if not matched:
        return 0.0
    return config.TREND_BONUS_BIG if best >= 100_000 else config.TREND_BONUS
