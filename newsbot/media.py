"""URL resolving, article-text fetching (for fact context only) and card upload."""
import re

import requests
from bs4 import BeautifulSoup

from . import config

HEADERS = {"User-Agent": config.USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}


def resolve_url(url: str) -> str:
    """Turn a news.google.com wrapper link into the real publisher URL (best effort)."""
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder(url, interval=1)
        if res.get("status") and res.get("decoded_url"):
            return res["decoded_url"]
    except Exception:
        pass
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if "news.google.com" not in r.url:
            return r.url
    except Exception:
        pass
    return url


def fetch_text(url: str, limit: int = 4000) -> str:
    """Read the article body so the AI works from facts, not just a headline.
    Used only as private context. It is never republished."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "form"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paras if len(p) > 40)
        return re.sub(r"\s+", " ", text)[:limit]
    except Exception:
        return ""


def upload_card(jpeg: bytes, name: str) -> str:
    """Upload to the public Supabase Storage bucket; Instagram needs a public image URL."""
    if config.DRY_RUN or not config.SUPABASE_URL:
        return f"https://example.invalid/{name}"
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{name}"
    r = requests.post(url, data=jpeg, timeout=60, headers={
        **config.supabase_auth_headers(),
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    })
    if r.status_code >= 300:
        raise RuntimeError(f"card upload failed {r.status_code}: {r.text[:200]}")
    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_BUCKET}/{name}"


def card_object_name(url: str) -> str:
    """The file name inside our storage bucket for one of our public card URLs ('' if it is not ours)."""
    marker = f"/storage/v1/object/public/{config.SUPABASE_BUCKET}/"
    return url.split(marker, 1)[1] if marker in (url or "") else ""


def delete_cards(names: list) -> int:
    """Bulk-delete images from the bucket. Returns how many were removed."""
    names = [n for n in names if n]
    if not names or config.DRY_RUN or not config.SUPABASE_URL:
        return 0
    r = requests.delete(f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}", timeout=60,
                        headers={**config.supabase_auth_headers(), "Content-Type": "application/json"},
                        json={"prefixes": names})
    if r.status_code >= 300:
        raise RuntimeError(f"image cleanup failed {r.status_code}: {r.text[:200]}")
    return len(r.json() or [])
