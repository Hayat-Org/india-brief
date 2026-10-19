"""Copyright-safe photos for posts.

We never copy a publisher's own photograph. Allowed sources, tried in order:
  1. Wikimedia Commons, only files licensed CC0, public domain or CC BY (attribution only: no
     ShareAlike/NoDerivs/NonCommercial, nothing marked non-free or restricted).
  2. Openverse (no key): only CC0, public-domain-mark and CC BY images (the same licence rule).
  3. Pixabay or Pexels (free licences, optional keys: PIXABAY_API_KEY / PEXELS_API_KEY).
  4. No photo: the post uses our own designed card.
Photos are chosen from a place or scene keyword, never a person, and are always credited and
labelled as a representative image. Anything uncertain is rejected.
"""
import io
import re

import requests

from . import config

COMMONS = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "IndiaInShortBot/1.0 (https://github.com/indiainshortdesk-lab/india-brief; indiainshort.desk@gmail.com)"}
MIN_WIDTH = 1000

# Photos of people raise privacy and misrepresentation problems, so anything that looks like a portrait is skipped.
PERSON_WORDS = re.compile(
    r"\b(portrait|people|person|man|men|woman|women|boy|girl|child|children|kid|crowd|rally|protest|"
    r"politician|minister|chief minister|prime minister|president|actor|actress|cricketer|player|"
    r"leader|candidate|police|soldier|army|victim|accused|arrested|wedding|funeral|selfie|face|"
    r"born|died|speaks?|speech|meeting|visit|inaugurat\w*|"
    r"sadhu|saint|monk|guru|baba|swami|yogi|yoga|priest|pandit|imam|nun|devotee|pilgrim|worship|prayer|"
    r"meditat\w*|religio\w*|holy|ascetic|beard\w*|elderly|old man|old woman|tourist|farmer|worker|labou?rer|"
    r"vendor|beggar|student|students|family|couple|baby|bride|groom|dancer|musician|model|portraits?|"
    r"human|humans|lady|gentleman|guy|youth|teen\w*|adult|hands?|eyes)\b", re.I)


def clean_query(q: str) -> str:
    q = re.sub(r"[^A-Za-z0-9 ,'\-]", " ", q or "")
    return re.sub(r"\s+", " ", q).strip()[:60]


def license_ok(short_name: str) -> bool:
    """CC0, public domain and plain CC BY are allowed. SA / ND / NC / unknown are not."""
    n = (short_name or "").strip().lower()
    if not n:
        return False
    if n.startswith("cc0") or n in ("public domain", "pd") or n.startswith("public domain") or n.startswith("pd-"):
        return True
    m = re.match(r"^cc[ -]by[ -](\d(\.\d)?)(\s+\w+)?$", n)  # "CC BY 4.0" (optionally a country code like "in")
    if m and not re.search(r"-(sa|nd|nc)|\b(sa|nd|nc)\b", n):
        return True
    return False


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def commons_candidates(query: str, limit: int = 12) -> list:
    r = requests.get(COMMONS, headers=HEADERS, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit,
        "prop": "imageinfo|categories", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1400,
        "cllimit": 30, "clshow": "!hidden"})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        val = lambda k: (meta.get(k) or {}).get("value", "")
        cats = " ".join(c.get("title", "") for c in p.get("categories", []))
        text = " ".join([p.get("title", ""), _text(val("ImageDescription")), cats])
        out.append({
            "title": p.get("title", ""), "url": info.get("thumburl") or info.get("url"),
            "width": info.get("thumbwidth") or info.get("width") or 0, "mime": info.get("mime", ""),
            "license": val("LicenseShortName"), "artist": _text(val("Artist")) or "Unknown author",
            "nonfree": val("NonFree").lower() == "true", "restrictions": val("Restrictions").strip(),
            "text": text, "page": f"https://commons.wikimedia.org/wiki/{p.get('title', '').replace(' ', '_')}"})
    return out


def pick_commons(cands: list):
    for c in cands:
        if c["mime"] not in ("image/jpeg", "image/png") or not c["url"]:
            continue
        if c["width"] < MIN_WIDTH or c["nonfree"] or c["restrictions"]:
            continue
        if not license_ok(c["license"]) or PERSON_WORDS.search(c["text"]):
            continue
        author = c["artist"][:60]
        return {"url": c["url"], "credit": f"{author} / Wikimedia Commons, {c['license']}",
                "source": "commons", "page": c["page"]}
    return None


def pexels(query: str):
    if not config.PEXELS_API_KEY:
        return None
    r = requests.get("https://api.pexels.com/v1/search", timeout=25,
                     headers={"Authorization": config.PEXELS_API_KEY},
                     params={"query": query, "per_page": 15, "orientation": "portrait"})
    r.raise_for_status()
    for p in r.json().get("photos", []):
        if PERSON_WORDS.search(f"{p.get('alt', '')} {query}") or p.get("width", 0) < MIN_WIDTH:
            continue
        src = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if src:
            return {"url": src, "credit": f"{p.get('photographer', 'Photographer')} / Pexels", "source": "pexels",
                    "page": p.get("url", "")}
    return None


def openverse(query: str):
    """Keyless. Filters to licences we can use with credit only (CC0, public domain mark, CC BY)."""
    r = requests.get("https://api.openverse.org/v1/images/", headers=HEADERS, timeout=25, params={
        "q": query, "license": "cc0,pdm,by", "category": "photograph", "extension": "jpg,jpeg,png",
        "mature": "false", "page_size": 15})
    r.raise_for_status()
    for p in r.json().get("results", []):
        lic = (p.get("license") or "").lower()
        if lic not in ("cc0", "pdm", "by"):
            continue
        text = f"{p.get('title', '')} {' '.join(t.get('name', '') for t in p.get('tags') or [])}"
        if PERSON_WORDS.search(text) or (p.get("width") or 0) < MIN_WIDTH or not p.get("url"):
            continue
        label = {"cc0": "CC0", "pdm": "Public domain", "by": f"CC BY {p.get('license_version', '')}".strip()}[lic]
        creator = (p.get("creator") or "Unknown author")[:60]
        return {"url": p["url"], "credit": f"{creator} / {p.get('provider') or p.get('source') or 'Openverse'}, {label}",
                "source": "openverse", "page": p.get("foreign_landing_url", "")}
    return None


def pixabay(query: str):
    if not config.PIXABAY_API_KEY:
        return None
    # Only the non-people categories are searched, and tags are checked for person/religion words.
    for category in ("places", "buildings", "nature", "travel", "backgrounds"):
        r = requests.get("https://pixabay.com/api/", timeout=25, params={
            "key": config.PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "vertical",
            "category": category, "safesearch": "true", "min_width": MIN_WIDTH, "per_page": 20})
        r.raise_for_status()
        for p in r.json().get("hits", []):
            tags = p.get("tags", "")
            if not tags or PERSON_WORDS.search(tags):
                continue
            url = p.get("largeImageURL") or p.get("webformatURL")
            if url:
                return {"url": url, "credit": f"{p.get('user', 'Photographer')} / Pixabay", "source": "pixabay",
                        "page": p.get("pageURL", "")}
    return None


def download(url: str):
    from PIL import Image
    r = requests.get(url, headers=HEADERS, timeout=40)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    img.load()
    return img.convert("RGB")


def find_photo(query: str):
    """Returns {"image": PIL.Image, "credit": str, "source": str} or None. Never raises."""
    query = clean_query(query)
    if not query or PERSON_WORDS.search(query) or not config.USE_PHOTOS:
        return None
    for finder in (lambda: pick_commons(commons_candidates(query)), lambda: openverse(query),
                   lambda: pixabay(query), lambda: pexels(query)):
        try:
            hit = finder()
            if hit:
                hit["image"] = download(hit["url"])
                return hit
        except Exception as ex:
            print(f"photo lookup skipped ({query}): {str(ex)[:120]}")
    return None
