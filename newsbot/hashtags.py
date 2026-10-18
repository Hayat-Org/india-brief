"""Hashtags: mandatory on every post, shaped per platform.

Instagram: at most 5 per caption (Meta's limit since Dec 2025) - 2 topic, 1 place, #IndiaNews, #IndiaInShort.
Facebook: 3 relevant tags. Threads: exactly 1 topic tag.
Stories about deaths or crime get topic + place + #IndiaNews only: no brand or category tag, so a tragedy
is never used for promotion.
"""
import re

from . import config

BANNED = {"follow", "followme", "followback", "like", "likeforlike", "like4like", "l4l", "f4f", "tagsforlikes",
          "viral", "trending", "fyp", "foryou", "explore", "instagood", "love", "share", "shareit", "news",
          "breaking", "breakingnews", "instalike", "follow4follow", "sub4sub"}

CATEGORY_TAG = {
    "Politics": "IndianPolitics", "Government & Policy": "Policy", "Economy & Business": "IndianEconomy",
    "Courts": "Courts", "Weather & Disaster": "Weather", "Health": "Health", "Education": "Education",
    "Science & Tech": "Tech", "Sports": "Sports", "Entertainment": "Entertainment", "Environment": "Environment",
    "Infrastructure & Transport": "Infrastructure", "International": "World", "Society": "Society",
    "Agriculture": "Agriculture", "Defence": "Defence", "Fact Check": "FactCheck",
}


def camel(phrase: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", phrase or "")
    def one(w):
        if w.isupper() and len(w) <= 5:      # acronyms: ISRO, RBI
            return w
        if not w.islower() and not w.isupper():  # already CamelCase, e.g. WestBengal
            return w[:1].upper() + w[1:]
        return w[:1].upper() + w[1:].lower()
    return "".join(one(w) for w in words)


def normalize(raw, limit: int = 8) -> list:
    """Clean whatever the AI returned (list, comma/space separated string, with or without #) into
    unique CamelCase tags without the '#'. A string is split into words, never into letters."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [x for x in re.split(r"[,\n;]+", raw) if x.strip()]
        if len(items) == 1:  # "durga puja kolkata stampede" with no commas: one tag per word
            items = re.split(r"[\s#]+", items[0])
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        return []
    out, seen = [], set()
    for item in items:
        tag = camel(item.replace("#", " "))
        key = tag.lower()
        if not (3 <= len(tag) <= 30) or tag.isdigit() or key in BANNED or key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def strip_inline(text: str) -> str:
    """Remove hashtags the AI wrote inside the body so the count we add stays exact."""
    return re.sub(r"[ \t]*#\w+", "", text or "").strip()


def brand_tag() -> str:
    return camel(config.BRAND_NAME) or "IndiaInShort"


def for_platforms(draft: dict, article: dict) -> dict:
    """{"threads": ["#X"], "facebook": [...], "instagram": [...]} - never empty."""
    topic = normalize(draft.get("hashtags"))
    state = article.get("state") or "National"
    place = camel(state) if state != "National" else ""
    careful = bool(draft.get("careful"))
    category = "" if careful else CATEGORY_TAG.get(article.get("category") or "", "")
    taken = {t.lower() for t in topic}
    place = place if place.lower() not in taken else ""

    def pick(*groups, size):
        out, seen = [], set()
        for g in groups:
            for t in ([g] if isinstance(g, str) else g):
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    out.append("#" + t)
        return out[:size]

    brand = "" if careful else brand_tag()
    threads = pick(topic[:1], place, category, "India", size=1)
    facebook = pick(topic[:1], place or category, topic[1:2], "IndiaNews", "India", size=3)
    instagram = pick(topic[:2], place, "IndiaNews", brand, category, topic[2:], "India", size=5)
    return {"threads": threads, "facebook": facebook, "instagram": instagram}
