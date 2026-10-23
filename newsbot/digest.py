"""Evening 'Today in short' digest: one swipeable Instagram carousel + Facebook + Threads post
built ONLY from stories already posted (and therefore already vetted) today."""
from datetime import datetime

from . import cards, config, media, pipeline, telegram


def _fit_threads(header: str, headlines: list, footer: str) -> str:
    """Threads allows 500 chars. Headlines are never cut mid-sentence: list as many whole headlines as fit and
    say how many more are in the swipeable slides, so the count always matches the cover."""
    total = len(headlines)
    for n in range(total, 0, -1):
        body = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines[:n], 1))
        more = f"\n+{total - n} more in the slides" if n < total else ""
        text = f"{header}\n\n{body}{more}\n\n{footer}"
        if len(text) <= 490:
            return text
    return header[:490]


def run_digest(db, now: datetime = None) -> str:
    now = now or pipeline.now_utc()
    if pipeline.is_paused(db):
        return "paused"
    day = now.astimezone(pipeline.IST).date().isoformat()
    url = f"digest:{day}"
    if any(r.get("url") == url for r in db.recent_articles(36)):
        return "exists"

    posted = [p for p in pipeline.stories(db.list_posted_since(pipeline.iso(pipeline.ist_day_start(now))))
              if p.get("draft")]
    posted.sort(key=lambda p: float(p.get("combined") or 0), reverse=True)
    top = posted[: config.DIGEST_MAX_STORIES]
    if len(top) < config.DIGEST_MIN_STORIES:
        return "too_few"

    headlines = [(s["draft"].get("card_headline") or s["title"]).strip() for s in top]
    sources = [pipeline.source_line(s) for s in top]

    blobs = [cards.render_digest_cover(len(top), now)]
    for i, s in enumerate(top, 1):
        blobs.append(cards.render_digest_slide(i, len(top), headlines[i - 1],
                                               s.get("category") or "Society", sources[i - 1]))
    slides = [media.upload_card(b, f"digest-{day}-{i}.jpg") for i, b in enumerate(blobs)]
    alts = ["Today in short: cover"] + [f"{h}. Source: {src}" for h, src in zip(headlines, sources)]

    header = "Today in short: the stories that mattered across India"
    threads_header = f"Today in short: {len(top)} stories that mattered across India. Swipe for each one."
    threads = _fit_threads(threads_header, headlines, "Sources are on our Facebook page.\n\n#IndiaNews")
    fb_lines = [f"{i}. {h} ({src})\n{(s['draft'].get('link') or s['url'])}"
                for i, (h, src, s) in enumerate(zip(headlines, sources, top), 1)]
    facebook = f"{header}\n\n" + "\n\n".join(fb_lines) + "\n\n#India #IndiaNews"
    ig_lines = "\n".join(f"{i}. {h} ({src})" for i, (h, src) in enumerate(zip(headlines, sources), 1))
    instagram = (f"{header}\n\n{ig_lines}\n\nSwipe for each story. Links to the original reports are on our "
                 f"Facebook page.\n\n#India #IndiaNews #TodayInShort #News")

    draft = {"threads": threads, "facebook": facebook, "instagram": instagram[:2100],
             "slides": slides, "alts": alts, "card_headline": f"Today in short: {day}"}
    row = db.insert_article({
        "url": url, "title": f"Today in short: {day}", "title_norm": f"digest {day}",
        "snippet": "", "source": config.BRAND_NAME, "source_kind": "digest",
        "sources": [{"name": config.BRAND_NAME, "url": url}], "corroboration": 1,
        "category": "Digest", "state": "National", "combined": 0,
        "status": "approved", "draft": draft, "card_url": slides[0],
    })
    if row is None:
        return "exists"
    if pipeline.get_mode(db) != "auto":
        telegram.send_review(row, draft, slides[0], [f"Digest of {len(top)} stories already posted today"])
        db.update_article(row["id"], {"status": "pending_review",
                                      "review_sent_at": pipeline.iso(pipeline.now_utc())})
        return "sent_for_review"
    return pipeline.publish_article(db, row)
