"""Duplicate / same-story detection (free, no embeddings needed).

Two headlines are the same story when their text is similar AND their key
entities (capitalised words and numbers) agree. The entity check stops
"Fire in Mumbai building" and "Fire in Pune building" from merging.
"""
import html
import re
from difflib import SequenceMatcher

STOP = set(
    "the a an of in on at to for and or is are was were be been by with from as that this "
    "it its after over into amid says said new news live updates update".split()
)


def strip_source(title: str, source: str) -> str:
    """Google News titles look like 'Headline - Outlet'; remove the outlet."""
    title = html.unescape(title or "").strip()
    if source:
        for sep in (" - ", " | ", " – ", " — "):
            tail = sep + source
            if title.lower().endswith(tail.lower()):
                return title[: -len(tail)].strip()
    return title


def normalize_title(title: str) -> str:
    t = html.unescape(title or "").lower()
    t = re.sub(r"[^a-z0-9ऀ-ॿ\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(norm: str) -> set:
    return {w for w in norm.split() if w not in STOP and len(w) > 2}


def _entities(title: str) -> set:
    caps = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", title or "")
    nums = re.findall(r"\d[\d,.]*", title or "")
    return {c.lower() for c in caps} | {n.replace(",", "") for n in nums}


def is_same_story(title_a: str, title_b: str) -> bool:
    na, nb = normalize_title(title_a), normalize_title(title_b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    if ratio < 0.78 and jac < 0.55:
        return False
    ea, eb = _entities(title_a), _entities(title_b)
    if ea and eb:
        overlap = len(ea & eb) / min(len(ea), len(eb))
        if overlap < 0.6:
            return False
    return True


def find_match(title: str, recent: list):
    """First already-known article that is the same story, else None."""
    for r in recent:
        if is_same_story(title, r.get("title", "")):
            return r
    return None
