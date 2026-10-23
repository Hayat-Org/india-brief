"""Core pipeline. Every external dependency is reached through a module attribute
(ai, media, cards, meta, telegram) so tests can replace them."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

from . import celebrities, ai, cards, config, dedup, hashtags, media, meta, photos, runlog, safety, telegram
from . import trends as trendlib
from .sources import Feed, fallback_feed

IST = timezone(timedelta(hours=5, minutes=30))
KIND_RANK = {"govt": 0, "factcheck": 0, "niche": 1, "news": 1, "state_gn": 2}
MAINSTREAM_KINDS = ("news", "state_gn")        # big national/regional outlets and Google News queries
SPOTLIGHT_SOURCE_KINDS = ("niche", "govt")     # sources a Spotlight story may come from
MAX_UNDATED_PER_FEED = 0                       # items without a date cannot be proven fresh, so they are skipped


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(value) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def story_time(a: dict) -> datetime:
    """When the story was PUBLISHED (falls back to when we first saw it)."""
    raw = a.get("published_at") or a.get("created_at")
    return parse_ts(raw) if raw else now_utc()


def story_age_hours(a: dict, now: datetime = None) -> float:
    return max(0.0, ((now or now_utc()) - story_time(a)).total_seconds() / 3600)


def is_too_old(a: dict, now: datetime = None) -> bool:
    """The freshness rule: nothing published more than ARTICLE_MAX_AGE_HOURS ago is ever posted."""
    return story_age_hours(a, now) > config.ARTICLE_MAX_AGE_HOURS


def ist_day_start(now: datetime) -> datetime:
    n = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return n.astimezone(timezone.utc)


def stories(posted: list) -> list:
    """Daily caps and balance rules count news posts only, not the evening digest."""
    return [p for p in posted if p.get("source_kind") != "digest" and not (p.get("draft") or {}).get("story_only")]


def celeb_name(a: dict) -> str:
    """The well-known person this story is about ('' if none): the AI's pick saved at scoring, else the roster."""
    return ((a.get("draft") or {}).get("celebrity")
            or celebrities.match(f"{a.get('title') or ''} {a.get('snippet') or ''}"))


def is_fast_track(p: dict) -> bool:
    return bool((p.get("draft") or {}).get("fast_track"))


def story_only_used_today(db, now: datetime = None) -> int:
    now = now or now_utc()
    return sum(1 for p in db.list_posted_since(iso(ist_day_start(now))) if (p.get("draft") or {}).get("story_only"))


def celeb_followup(db, a: dict, star: str, now: datetime = None) -> bool:
    """True when this is just another development about a person already posted about in the last 24 hours
    (so it gets a Story at most). A critical update (death, critical illness, conviction, sentence,
    retirement) is allowed one more post per person per day."""
    now = now or now_utc()
    since = iso(now - timedelta(hours=24))
    prior = [p for p in db.list_posted_since(since) + db.list_by_status("pending_review")
             if p.get("source_kind") != "digest" and p["id"] != a["id"]
             and not (p.get("draft") or {}).get("story_only")
             and celebrities.same_person((p.get("draft") or {}).get("celebrity", ""), star)]
    if not prior:
        return False
    head = f"{a.get('title') or ''} {a.get('snippet') or ''}"
    if celebrities.is_critical_update(head) and not any((p.get("draft") or {}).get("critical_update") for p in prior):
        return False
    return True


def celeb_single_source_ok(a: dict, name: str = "") -> bool:
    """Can ONE source carry this celebrity story? A mainstream/official/fact-check source can, unless it is a
    rumour. A death or illness report from anything else (regional, specialist, Google News regional search)
    waits for a second outlet: celebrity death hoaxes are common."""
    if (a.get("corroboration") or 1) >= 2 or a.get("source_kind") in ("govt", "factcheck"):
        return True
    head = f"{a.get('title') or ''} {a.get('snippet') or ''}"
    if celebrities.is_rumour(head):
        return False
    if celebrities.is_death_story(head):
        return a.get("source_kind", "news") == "news"
    return True


def is_paused(db) -> bool:
    return db.get_setting("paused", "0") == "1"


def get_mode(db) -> str:
    # Key is "mode2": an older "mode" value (from when approval was the default) is deliberately ignored.
    return db.get_setting("mode2", config.DEFAULT_MODE)


def queue_summary(db) -> str:
    try:
        c = db.status_counts(72)
        posted = stories(db.list_posted_since(iso(ist_day_start(now_utc()))))
        return (f"Queue: {c.get('new', 0)} waiting to be scored · {c.get('candidate', 0)} candidates · "
                f"{c.get('pending_review', 0)} awaiting your tap · {len(posted)}/{config.MAX_POSTS_PER_DAY} posted today")
    except Exception as ex:
        return f"Queue: unavailable ({str(ex)[:80]})"


# ---------------------------------------------------------------------------
# feed fetching
# ---------------------------------------------------------------------------
@dataclass
class Entry:
    title: str
    url: str
    snippet: str
    source: str
    kind: str
    state: str
    published: datetime


@dataclass
class FeedResult:
    name: str
    kind: str
    ok: bool = False
    items: int = 0
    via: str = "direct"        # direct | fallback (Google News site: search)
    error: str = ""
    direct_error: str = ""


FEED_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-IN,en;q=0.9",
}


def describe_error(ex: Exception) -> str:
    if isinstance(ex, requests.HTTPError) and ex.response is not None:
        return f"HTTP {ex.response.status_code}"
    if isinstance(ex, requests.Timeout):
        return "timed out"
    if isinstance(ex, requests.ConnectionError):
        return "connection failed"
    return f"{type(ex).__name__}: {str(ex)[:80]}"


def _get(url: str, timeout: int = 25) -> bytes:
    """GET with one retry for timeouts, dropped connections, 429 and 5xx. 403/404 are final."""
    for attempt in (1, 2):
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT, **FEED_HEADERS}, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            r.raise_for_status()
            return r.content
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 1:
                time.sleep(2)
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def _entry_time(e):
    ts = e.get("published_parsed") or e.get("updated_parsed")
    if ts:
        try:
            return datetime(*ts[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    raw = (e.get("published") or e.get("updated") or "").strip()
    for candidate in (raw, raw.replace(",", " ")):
        if candidate:
            try:
                return parse_ts(parsedate_to_datetime(candidate))
            except Exception:
                continue
    return None


def _parse(content: bytes, feed: Feed, limit: int = 15, now: datetime = None) -> list:
    import feedparser  # imported here so the rest of the pipeline is testable without it
    now = now or now_utc()
    dated, undated = [], []
    for e in feedparser.parse(content).entries:
        title, link = (e.get("title") or "").strip(), (e.get("link") or "").strip()
        if not title or not link:
            continue
        src = ""
        if isinstance(e.get("source"), dict):
            src = (e["source"].get("title") or "").strip()
        src = src or feed.name.replace("GN:", "Google News")
        ts = _entry_time(e)
        summary = BeautifulSoup(e.get("summary") or "", "html.parser").get_text(" ", strip=True)
        item = Entry(dedup.strip_source(title, src), link, summary[:600], src, feed.kind, feed.state,
                     min(ts, now) if ts else now)  # a date in the future is clamped to now
        (dated if ts else undated).append(item)
    dated.sort(key=lambda x: x.published, reverse=True)
    return (dated + undated[:MAX_UNDATED_PER_FEED])[:limit]


def fetch_feed_result(feed: Feed, limit: int = 15):
    """Fetch one feed; if it is blocked or empty, try its Google News 'site:' stand-in.
    Never raises. Returns (FeedResult, entries)."""
    res = FeedResult(feed.name, feed.kind)
    try:
        entries = _parse(_get(feed.url), feed, limit)
        if entries:
            res.ok, res.items = True, len(entries)
            return res, entries
        res.direct_error = "no items"
    except Exception as ex:
        res.direct_error = describe_error(ex)
    fb = fallback_feed(feed) if config.USE_GOOGLE_NEWS else None
    if fb is None:
        res.error = res.direct_error
        return res, []
    try:
        entries = _parse(_get(fb.url), fb, limit)
        if entries:
            res.ok, res.items, res.via, res.error = True, len(entries), "fallback", res.direct_error
            return res, entries
        res.error = f"{res.direct_error}; fallback returned no items"
    except Exception as ex:
        res.error = f"{res.direct_error}; fallback failed: {describe_error(ex)}"
    return res, []


def collect_entries(feeds: list, workers: int = 10):
    """Fetch all feeds in parallel. Returns (entries, [FeedResult])."""
    entries, results = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res, items in pool.map(fetch_feed_result, feeds):
            results.append(res)
            entries.extend(items)
    return entries, results


# ---------------------------------------------------------------------------
# ingest: dedup + cross-source corroboration
# ---------------------------------------------------------------------------
def mainstream_count(a: dict) -> int:
    """How many big mainstream outlets carry this story. Sources saved before this field
    existed have no 'kind' and are counted as mainstream (the cautious reading)."""
    return sum(1 for s in (a.get("sources") or []) if s.get("kind", "news") in MAINSTREAM_KINDS)


def ingest(db, entries: list, now: datetime = None) -> dict:
    now = now or now_utc()
    recent = db.recent_articles(72)
    known_urls = {r["url"] for r in recent if r.get("url")}
    stats = {"seen": len(entries), "new": 0, "merged": 0, "dup": 0, "junk": 0, "old": 0, "niche_new": 0,
             "new_ids": []}
    max_age = timedelta(hours=config.ARTICLE_MAX_AGE_HOURS)

    for e in sorted(entries, key=lambda x: x.published, reverse=True):
        if e.url in known_urls:
            stats["dup"] += 1
            continue
        if safety.is_junk(e.title):
            stats["junk"] += 1
            continue
        if now - e.published > max_age:
            stats["old"] += 1
            continue
        match = dedup.find_match(e.title, recent)
        if match:
            names = {s["name"] for s in (match.get("sources") or [])}
            if e.source not in names:  # a different outlet covering the same story
                sources = list(match.get("sources") or []) + [{"name": e.source, "url": e.url, "kind": e.kind}]
                match["sources"], match["corroboration"] = sources, len(sources)
                db.update_article(match["id"], {"sources": sources, "corroboration": len(sources)})
                stats["merged"] += 1
            else:
                stats["dup"] += 1
            known_urls.add(e.url)
            continue
        row = db.insert_article({
            "url": e.url, "title": e.title, "title_norm": dedup.normalize_title(e.title),
            "snippet": e.snippet, "source": e.source, "source_kind": e.kind,
            "feed_state": e.state or None,
            "sources": [{"name": e.source, "url": e.url, "kind": e.kind}], "corroboration": 1,
            "published_at": iso(e.published), "status": "new",
        })
        known_urls.add(e.url)
        if row is None:  # stored earlier than the 72h window we looked at
            stats["dup"] += 1
            continue
        recent.insert(0, row)
        stats["new"] += 1
        stats["new_ids"].append(row["id"])
        if e.kind in SPOTLIGHT_SOURCE_KINDS:
            stats["niche_new"] += 1
    return stats


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def score_priority(a: dict) -> float:
    """Which stories get scored first when more arrive than the per-run limit.
    Multi-outlet stories first, and specialist/official/fact-check sources get a boost so
    under-covered stories are not starved by the volume of mainstream headlines."""
    boost = 3 if celebrities.match(f"{a.get('title') or ''} {a.get('snippet') or ''}") else 0  # famous people first
    return min(a.get("corroboration") or 1, 4) + (1.5 if a.get("source_kind") in ("govt", "factcheck", "niche") else 0) + boost


def score_pending(db, now: datetime = None, trends: dict = None, only_ids: set = None, limit: int = None) -> dict:
    """Score new stories. `only_ids`/`limit` let the 20-minute celebrity run score just its own fresh items."""
    now = now or now_utc()
    new = db.list_by_status("new", limit=500)
    if only_ids is not None:
        new = [a for a in new if a["id"] in only_ids]
    stats = {"scored": 0, "candidates": 0, "discarded": 0, "errors": 0, "stale": 0, "backlog": 0,
             "below_min": 0, "not_india": 0, "opinion": 0, "promo": 0, "niche_candidates": 0, "celebrity": 0,
             "error_samples": [], "top": []}
    fresh = []
    for a in new:
        if is_too_old(a, now):
            db.update_article(a["id"], {"status": "discarded", "hold_reason": "stale"})
            stats["stale"] += 1
        else:
            fresh.append(a)
    fresh.sort(key=score_priority, reverse=True)  # stable: newest first among equals
    ist_hour = now.astimezone(timezone(timedelta(hours=5, minutes=30))).hour
    limit = limit or (config.MAX_SCORE_PER_RUN if ist_hour >= 7 else max(8, config.MAX_SCORE_PER_RUN // 3))  # quiet hours: little news
    todo = fresh[:limit]
    stats["backlog"] = len(fresh) - len(todo)
    size, failed_chunks = max(1, config.SCORE_BATCH_SIZE), 0

    for i in range(0, len(todo), size):
        chunk = todo[i:i + size]
        try:
            results = ai.score_batch(chunk)
            failed_chunks = 0
        except ai.AIRateLimited as ex:
            # Free daily/minute limit reached: not an error. Pause scoring, the rest waits for a later run.
            stats["rate_limited"] = ex.until or 0
            stats["backlog"] += max(0, len(todo) - i)
            break
        except Exception as ex:
            stats["errors"] += len(chunk)
            if len(stats["error_samples"]) < 3:
                stats["error_samples"].append(str(ex)[:400])
            failed_chunks += 1
            if failed_chunks >= 2:  # keys/quota/outage: stop burning time, try again next run
                stats["backlog"] += max(0, len(todo) - (i + size))
                break
            continue
        for a in chunk:
            s = results.get(a["id"])
            if s is None:
                stats["errors"] += 1
                if len(stats["error_samples"]) < 3:
                    stats["error_samples"].append(f"#{a['id']}: model returned no usable score")
                continue
            stats["scored"] += 1
            keyword_reason = safety.is_sensitive(f"{a['title']} {a.get('snippet') or ''}")
            sensitive = s["sensitive"] or bool(keyword_reason)
            reason = s["sensitive_reason"] or keyword_reason or ""
            star = s.get("celebrity") or celebrities.match(f"{a['title']} {a.get('snippet') or ''}")
            combined = min(10.0, round(s["combined"] + config.CELEBRITY_BONUS, 1)) if star else s["combined"]
            values = {
                "category": s["category"], "state": s["state"],
                "significance": s["significance"], "impact": s["impact"], "interest": s["interest"],
                "combined": combined, "sensitive": sensitive, "sensitive_reason": reason,
                "is_factcheck": s["is_factcheck"],
                "trend_boost": trendlib.trend_boost(a["title"], trends or {}),
            }
            if not s["is_india"]:
                values["status"] = "discarded"; stats["not_india"] += 1
            elif s["is_opinion"]:
                values["status"] = "discarded"; stats["opinion"] += 1
            elif s["is_promotional"]:
                values["status"] = "discarded"; stats["promo"] += 1
            elif combined < (config.CELEBRITY_MIN_SCORE if star else config.MIN_SCORE):
                values["status"] = "discarded"; stats["below_min"] += 1
            else:
                values["status"] = "candidate"
                stats["candidates"] += 1
                if star:
                    values["draft"] = {"celebrity": star}  # remembered for the fast-track (no extra column needed)
                    stats["celebrity"] += 1
                if a.get("source_kind") in SPOTLIGHT_SOURCE_KINDS and mainstream_count(a) == 0:
                    stats["niche_candidates"] += 1
                stats["top"].append((combined, a["title"], s["category"], s["state"],
                                     a.get("corroboration") or 1))
            if values["status"] == "discarded":
                stats["discarded"] += 1
            db.update_article(a["id"], values)
    stats["top"].sort(reverse=True)
    stats["top"] = stats["top"][:5]
    return stats


def expire_old(db, now: datetime = None) -> int:
    """Candidates older than the freshness window are no longer news; stale review items expire."""
    now, n = now or now_utc(), 0
    for a in db.list_by_status("candidate"):
        if is_too_old(a, now):
            db.update_article(a["id"], {"status": "discarded", "hold_reason": "stale"})
            n += 1
    for a in db.list_by_status("pending_review"):
        sent = parse_ts(a.get("review_sent_at") or a["created_at"])
        if now - sent > timedelta(hours=config.REVIEW_EXPIRY_HOURS):
            db.update_article(a["id"], {"status": "expired"})
            n += 1
    return n


def cleanup_old(db, now: datetime = None) -> dict:
    """Delete rows older than DATA_RETENTION_HOURS (published posts stay as the audit trail) and the
    card images of every story that old. Returns counts."""
    now = now or now_utc()
    cutoff = iso(now - timedelta(hours=config.DATA_RETENTION_HOURS))
    out = {"images": 0, "rows": 0}
    old = db.old_with_cards(cutoff)
    names = []
    for r in old:
        names.append(media.card_object_name(r.get("card_url") or ""))
        names += [media.card_object_name(u) for u in ((r.get("draft") or {}).get("slides") or [])]
        story = media.card_object_name((r.get("draft") or {}).get("story_url") or "")
        if story:
            names.append(story)
    try:
        out["images"] = media.delete_cards(names)
    except Exception as ex:  # images are best effort; rows are still cleaned
        runlog.warn(f"Image cleanup problem: {str(ex)[:160]}")
    for r in old:
        if r.get("status") == "published":  # row stays, but its image is gone
            db.update_article(r["id"], {"card_url": None})
    out["rows"] = db.purge_old(cutoff)
    return out


# ---------------------------------------------------------------------------
# drafting + publishing
# ---------------------------------------------------------------------------
def shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind("\n"))
    if m > limit * 0.5:
        return cut[: m + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _tag(s: str) -> str:
    return "#" + re.sub(r"[^A-Za-z0-9]", "", s.title().replace(" ", ""))


def source_line(a: dict) -> str:
    names = []
    for s in a.get("sources") or [{"name": a.get("source", "")}]:
        if s["name"] and s["name"] not in names:
            names.append(s["name"])
    return ", ".join(names[:3])


def build_texts(draft: dict, a: dict) -> dict:
    """Final text for each platform. Hashtags are mandatory and shaped per platform (see hashtags.py)."""
    src = source_line(a)
    spot = bool(draft.get("spotlight"))
    ref = draft.get("short_url") or src          # a short link to the publisher's page, else the outlet name
    reasons = draft.get("careful_reasons") or []
    helpline = safety.HELPLINE_NOTE if "suicide/self-harm" in reasons else ""
    tags = hashtags.for_platforms(draft, a)
    body = {k: hashtags.strip_inline(draft.get(k) or "") for k in ("threads", "facebook", "instagram")}
    credit = draft.get("photo_credit")
    careful_note = ("Allegations are not proven unless a court says so." if draft.get("careful") else "")
    photo_note = f"Representative photo: {credit}" if credit else ""

    # Threads: 500 characters in total, so the tail (source, credit, tag) is reserved first.
    tail = f"Source: {ref}"
    if helpline:
        tail = helpline + "\n" + tail
    if credit and len(tail) + len(credit) + 40 < 300:
        tail += f"\nPhoto: {credit}"
    tail += "\n\n" + " ".join(tags["threads"])
    threads = shorten(body["threads"], 486 - len(tail)) + "\n\n" + tail

    facebook = "\n\n".join(x for x in (
        shorten(body["facebook"], 700), careful_note, helpline, f"Source: {ref}", photo_note,
        " ".join(tags["facebook"])) if x)

    instagram = "\n\n".join(x for x in (
        shorten(body["instagram"], 1000), careful_note, helpline, f"Source: {ref}", photo_note,
        " ".join(tags["instagram"])) if x)
    return {"threads": threads[:490], "facebook": facebook, "instagram": instagram[:2100]}


def careful_used_today(db, now: datetime = None) -> int:
    """Crime/violence/legal posts already published or queued today (political ones have their own cap)."""
    now = now or now_utc()
    rows = stories(db.list_posted_since(iso(ist_day_start(now)))) + stories(db.list_by_status("pending_review"))
    return sum(1 for p in rows if (p.get("draft") or {}).get("careful") and not is_fast_track(p)
               and (p["draft"].get("careful_reasons") or []) != ["political story"])


def load_runtime_tokens(db) -> None:
    """Prefer the Facebook/Instagram credentials saved by /fbtoken over the GitHub secrets.
    Also restores which AI models were at their daily limit in an earlier run."""
    try:
        ai.load_state(db.get_setting("ai_blocked", ""))
        ai.STATE_SAVER = lambda raw: db.set_setting("ai_blocked", raw)
    except Exception as ex:
        print(f"AI limit memory not loaded: {str(ex)[:100]}")
    try:
        token = db.get_setting("fb_page_token", "")
        if token:
            config.FB_PAGE_TOKEN = config.IG_TOKEN = token
            if db.get_setting("fb_page_id", ""):
                config.FB_PAGE_ID = db.get_setting("fb_page_id", "")
            if db.get_setting("ig_user_id", ""):
                config.IG_USER_ID = db.get_setting("ig_user_id", "")
    except Exception as ex:
        print(f"saved tokens not loaded: {str(ex)[:100]}")


def save_fb_token(db, short_token: str) -> str:
    """Exchange a short-lived token for a permanent Page token and keep it in the database. Returns a summary."""
    got = meta.exchange_page_token(short_token)
    db.set_setting("fb_page_token", got["page_token"])
    db.set_setting("fb_page_id", got["page_id"])
    if got.get("ig_id"):
        db.set_setting("ig_user_id", got["ig_id"])
    load_runtime_tokens(db)
    ig = (f"Instagram @{got['ig_username']} (ID {got['ig_id']}) is linked." if got.get("ig_id")
          else "No Instagram account is linked to this Page yet, so Instagram posts will fail until you link it.")
    return f"Facebook Page '{got['page_name']}' connected with a permanent token. {ig}"


def threads_token(db) -> str:
    return db.get_setting("threads_token", "") or config.THREADS_ACCESS_TOKEN


def clean_source_link(link: str) -> str:
    """The publisher URL without tracking parameters ('' if it is still a Google News wrapper)."""
    if not link or "news.google.com" in link:
        return ""
    return media.clean_url(link)


def ig_cooldown_active(db) -> bool:
    try:
        until = db.get_setting("ig_cooldown_until", "")
        return bool(until) and now_utc() < parse_ts(until)
    except Exception:
        return False


def set_ig_cooldown(db) -> None:
    try:
        db.set_setting("ig_cooldown_until", iso(now_utc() + timedelta(minutes=config.IG_COOLDOWN_MINUTES)))
    except Exception:
        pass


def post_stories(a: dict, story_url: str, db=None) -> dict:
    """Instagram and Facebook Stories. Never blocks or fails the feed post: problems are logged as warnings."""
    out = {}
    platforms = config.enabled_platforms()
    for name, fn in (("instagram", meta.post_instagram_story), ("facebook", meta.post_facebook_story)):
        if name not in platforms:
            continue
        if name == "instagram" and db is not None and ig_cooldown_active(db):
            out[name] = {"ok": False, "error": "skipped: Instagram rate-limit cool-down"}
            continue
        try:
            out[name] = {"ok": True, "id": fn(story_url)}
        except Exception as ex:
            out[name] = {"ok": False, "error": str(ex)[:200]}
            if name == "instagram" and db is not None and meta.is_rate_limited(str(ex)):
                set_ig_cooldown(db)
            runlog.warn(f"#{a['id']} {name} story: {str(ex)[:200]}")
        time.sleep(2)
    if out:
        runlog.note(f"#{a['id']} stories → " + ", ".join(f"{k} {'✓' if v['ok'] else '✗'}" for k, v in out.items()))
    return out


def fb_comment_paused(db) -> bool:
    try:
        off = db.get_setting("fb_comment_off", "")
        return bool(off) and now_utc() - parse_ts(off) < timedelta(hours=12)
    except Exception:
        return False


def publish_article(db, a: dict, only: list = None, auto: bool = False) -> str:
    """Post to every configured platform independently; one failure never blocks the others.
    `only` limits the attempt to some platforms (used by Retry); earlier results are kept."""
    draft = a["draft"]
    is_digest = a.get("source_kind") == "digest"
    if is_digest:  # digest texts are already final; links live inside the Facebook text
        texts = {k: draft[k] for k in ("threads", "facebook", "instagram")}
        link = None
    else:
        texts = build_texts(draft, a)
        link = (draft.get("link") or a["url"]) if config.ATTACH_LINKS else None
        if draft.get("short_url"):
            link = None            # the short source link is already in the caption
    alt = f"{draft.get('card_headline') or a['title']}. Source: {source_line(a)}"
    image = draft["slides"][0] if is_digest and draft.get("slides") else a.get("card_url")  # every post carries a picture
    platforms = config.enabled_platforms()
    results = dict(a.get("results") or {}) if only else {}
    steps = {
        "threads": lambda: meta.post_threads(texts["threads"], link, threads_token(db), image),
        "facebook": lambda: meta.post_facebook(texts["facebook"], link, image),
        "instagram": (lambda: meta.post_instagram_carousel(texts["instagram"], draft["slides"],
                                                           draft.get("alts")))
        if is_digest else (lambda: meta.post_instagram(texts["instagram"], a["card_url"], alt)),
    }
    for name, fn in steps.items():
        if only and name not in only:
            continue
        if name not in platforms:
            results[name] = {"ok": False, "skipped": True}
            continue
        prev_tries = int((results.get(name) or {}).get("tries") or 0)
        if name == "instagram" and not config.DRY_RUN and ig_cooldown_active(db):
            results[name] = {"ok": False, "rate_limited": True, "tries": prev_tries,
                             "error": "waiting: Instagram rate-limit cool-down (retrying automatically)"}
            continue
        try:
            if config.DRY_RUN:
                print(f"[dry-run] would post to {name}:\n{texts[name]}\n")
                results[name] = {"ok": True, "id": "dry-run"}
            else:
                results[name] = {"ok": True, "id": fn()}
        except Exception as ex:
            err = str(ex)[:300]
            hint = meta.explain_error(err)
            limited = meta.is_rate_limited(err)
            results[name] = {"ok": False, "error": err, **({"hint": hint} if hint else {}),
                             **({"rate_limited": True} if limited else {}), "tries": prev_tries + 1}
            if limited and name == "instagram":
                set_ig_cooldown(db)
        time.sleep(0 if config.DRY_RUN else 3)  # polite pacing between API calls
    fb = results.get("facebook") or {}
    if fb.get("ok") and not is_digest and not config.DRY_RUN and not fb.get("comment") \
            and (not only or "facebook" in only) and not fb_comment_paused(db):
        full = clean_source_link(draft.get("link") or "")
        if full:
            try:
                meta.comment_facebook(fb["id"], f"Read the full story: {full}")
                fb["comment"] = True
            except Exception as ex:
                fb["comment"] = False
                if "permission" in str(ex).lower() or "(#200)" in str(ex) or "code=10" in str(ex):
                    db.set_setting("fb_comment_off", iso(now_utc()))  # stop retrying (and warning) for 12 hours
                    runlog.warn(f"#{a['id']} facebook link comment: {str(ex)[:160]} → add the pages_manage_engagement "
                                "permission, then run /fbtoken again. Not retried for 12 hours.")
                else:
                    runlog.warn(f"#{a['id']} facebook link comment: {str(ex)[:160]}")
    ok = [k for k, v in results.items() if v.get("ok")]
    failed = [k for k, v in results.items() if not v.get("ok") and not v.get("skipped")]
    update = {"status": "published" if ok else "failed", "results": results}
    if ok and not a.get("posted_at"):
        update["posted_at"] = iso(now_utc())
    db.update_article(a["id"], update)
    if ok and not only and not is_digest and draft.get("story_url") and config.STORIES and not config.DRY_RUN:
        post_stories(a, draft["story_url"], db)

    lines = []
    for k, v in results.items():
        if v.get("ok"):
            lines.append(f"✅ {k}")
        elif v.get("skipped"):
            lines.append(f"⏭ {k} (not configured)")
        else:
            lines.append(f"❌ {k}: {v.get('error')}" + (f"\n   Likely fix: {v['hint']}" if v.get("hint") else ""))
            if v.get("rate_limited"):  # temporary: the bot retries by itself, so it is a warning, not an error
                runlog.warn(f"#{a['id']} {k} rate-limited, will retry automatically (try {v.get('tries', 1)}/{config.IG_MAX_TRIES})")
            else:
                runlog.error(f"#{a['id']} {k}: {v.get('error')}" + (f" → {v['hint']}" if v.get("hint") else ""))
    head = "Posted" if ok and not failed else ("Partly posted" if ok else "FAILED")
    marks = ", ".join(f"{k} {'✓' if v.get('ok') else '✗'}" for k, v in results.items() if not v.get("skipped"))
    runlog.note(f"#{a['id']} → {head.lower()} ({marks})")
    runlog.activity()
    buttons = [[{"text": "🔁 Retry failed platforms", "callback_data": f"t:{a['id']}"}]] if failed else None
    if auto and not ok_now(results, only):
        return "failed"   # an automatic retry that is still rate-limited stays quiet
    telegram.send_text(f"{head} #{a['id']}: {a['title'][:90]}\n" + "\n".join(lines), buttons=buttons)
    return "published" if ok else "failed"


def ok_now(results: dict, only: list) -> bool:
    return any(results.get(k, {}).get("ok") for k in (only or results))


def retry_instagram_rate_limited(db, now: datetime = None) -> list:
    """Retry Instagram posts that failed only because of the app request limit (after the cool-down)."""
    now = now or now_utc()
    out = []
    if ig_cooldown_active(db):
        return out
    since = iso(now - timedelta(hours=config.IG_AUTO_RETRY_HOURS))
    for p in db.list_posted_since(since):
        r = (p.get("results") or {}).get("instagram") or {}
        if r.get("ok") or not r.get("rate_limited") or int(r.get("tries") or 0) >= config.IG_MAX_TRIES or not p.get("draft"):
            continue
        out.append(publish_article(db, p, only=["instagram"], auto=True))
        if ig_cooldown_active(db) or len(out) >= 3:
            break
    return out


def retry_failed(db, article: dict) -> str:
    todo = [k for k, v in (article.get("results") or {}).items() if not v.get("ok") and not v.get("skipped")]
    if not todo or not article.get("draft"):
        telegram.send_text(f"#{article['id']}: nothing to retry (no failed platform).")
        return "nothing"
    return publish_article(db, article, only=todo)


CATEGORY_PHOTO_QUERY = {
    "Government & Policy": "Parliament House New Delhi", "Politics": "Parliament House New Delhi",
    "Courts": "Supreme Court of India building", "Economy & Business": "Reserve Bank of India building",
    "Weather & Disaster": "monsoon rain India", "Health": "hospital building India",
    "Science & Tech": "ISRO launch pad Sriharikota", "Environment": "forest India landscape",
    "Infrastructure & Transport": "railway station India", "Agriculture": "paddy field India",
    "Sports": "cricket stadium India", "Education": "university campus India",
}


def fallback_photo_query(a: dict) -> str:
    """Used when the AI offered no photo keyword: a place for regional stories, else a generic landmark or scene."""
    state = a.get("state")
    if state and state != "National":
        return f"{state} India landscape"
    return CATEGORY_PHOTO_QUERY.get(a.get("category") or "", "")


def process_candidate(db, a: dict, breaking: bool = False, fast: bool = False, story_only: bool = False) -> str:
    """Draft -> safety checks -> card -> (Telegram review | publish). `fast` = celebrity fast-track."""
    tag = f"#{a['id']} ({a.get('combined')}) {a['title'][:70]}"
    star = celeb_name(a)
    if star and a.get("status") == "candidate" and not celeb_single_source_ok(a, star):
        runlog.note(f"{tag} → waiting for a second source ({star}: rumour or unconfirmed death/illness report)")
        return "deferred"
    if is_too_old(a):
        db.update_article(a["id"], {"status": "discarded", "hold_reason": "stale"})
        runlog.note(f"{tag} → skipped: published more than {config.ARTICLE_MAX_AGE_HOURS}h ago")
        return "stale"
    mode = get_mode(db)
    if star and a.get("status") == "candidate" and celeb_followup(db, a, star):
        story_only = True
    if mode != "auto":
        story_only = False  # review mode: every item is reviewed as a normal post
    if story_only and (not config.STORIES or story_only_used_today(db) >= config.STORY_ONLY_PER_DAY):
        db.update_article(a["id"], {"status": "discarded", "hold_reason": "follow-up on a topic already posted"})
        runlog.note(f"{tag} → skipped: follow-up on a topic already posted today")
        return "skipped_followup"
    spotlight = bool(a.get("spotlight"))
    link = media.resolve_url(a["url"])
    body = media.fetch_text(link)
    short = media.short_url(link)
    developing = (a.get("corroboration") or 1) < 2 and a.get("source_kind") not in ("govt", "factcheck")
    facts = f"{a['title']}\n{a.get('snippet') or ''}\n{body}"

    tier, tier_reasons = safety.classify(a["title"], a.get("snippet") or "", body, a.get("category") or "",
                                         bool(a.get("sensitive")), a.get("sensitive_reason") or "")
    careful = tier == "careful"
    if tier == "blocked" and mode == "auto":
        # Only the legal election blackout (RP Act s.126/126A, dates set in ELECTION_SILENCE) lands here.
        db.update_article(a["id"], {"status": "discarded", "hold_reason": "blocked: " + "; ".join(tier_reasons)})
        runlog.note(f"{tag} → held back by law: {'; '.join(tier_reasons)}")
        return "skipped_safety"
    if careful and mode == "auto":
        strong = (a.get("corroboration") or 1) >= 2 or a.get("source_kind") in ("govt", "factcheck")
        # One big mainstream outlet is enough for incidents, accidents, disasters and politics; sexual-offence,
        # minor and pending-case stories still need a second outlet or an official source.
        needs_two = {"sexual offence", "minor involved", "legal risk", "communal/mob violence"} & set(tier_reasons)
        if star:  # famous person: a pending case (FIR, arrest, court) can go out on one trusted source
            needs_two -= {"legal risk"}
            if not strong and not needs_two and celeb_single_source_ok(a, star):
                strong = True
        if not strong and not needs_two and mainstream_count(a) >= 1:
            strong = True
        if not strong:
            db.update_article(a["id"], {"status": "discarded", "hold_reason": "careful story, single source"})
            runlog.note(f"{tag} → not posted: sensitive story ({'; '.join(tier_reasons)}) needs 2+ outlets or an official source")
            return "skipped_safety"
        if tier_reasons != ["political story"] and not star and careful_used_today(db) >= config.MAX_CAREFUL_PER_DAY:
            runlog.note(f"{tag} → deferred: today's limit of {config.MAX_CAREFUL_PER_DAY} crime/violence/legal posts is used")
            return "deferred"

    kw = {"careful": True, "reasons": tier_reasons} if careful else {}
    if star:
        kw["celebrity"] = star

    def check(d):
        d = {**d, "careful_reasons": tier_reasons}
        found = safety.check_draft(d, facts, thin=not body)
        if star:
            for platform in ("threads", "facebook", "instagram", "card_headline"):
                if not celebrities.name_in_text(star, d.get(platform) or ""):
                    found.append(f"{platform}: does not name {star}")
        return found + (safety.careful_issues(d, facts, allow_name=star) if careful else [])

    try:
        draft = ai.write_post(a, body, developing, **kw)
        issues = check(draft)
        if issues:  # one retry; keep the cleaner draft
            try:
                second = ai.write_post(a, body, developing, **kw)
                second_issues = check(second)
                if len(second_issues) < len(issues):
                    draft, issues = second, second_issues
            except Exception:
                pass
    except ai.AIRateLimited as ex:
        runlog.warn(f"{tag} → drafting paused: AI free limit reached, will retry in a later slot")
        return "deferred"
    except Exception as ex:
        attempts = (a.get("attempts") or 0) + 1
        db.update_article(a["id"], {"attempts": attempts,
                                    "status": "failed" if attempts >= 3 else a["status"]})
        print(f"draft error #{a['id']}: {str(ex)[:150]}")
        runlog.error(f"{tag} → drafting failed (attempt {attempts}/3): {str(ex)[:300]}")
        return "draft_failed"

    draft["link"], draft["developing"], draft["spotlight"] = link, developing, spotlight
    draft["short_url"] = short
    draft["careful"], draft["careful_reasons"] = careful, tier_reasons
    if star:
        draft["celebrity"], draft["fast_track"] = star, bool(fast)
        draft["critical_update"] = celebrities.is_critical_update(f"{a['title']} {a.get('snippet') or ''}")
    if story_only:
        draft["story_only"] = True
    flags, hold = [], []
    if tier == "blocked":
        flags.append("Blocked by law/policy: " + "; ".join(tier_reasons))
        hold.append("blocked: " + "; ".join(tier_reasons))
    elif careful:
        flags.append("Careful story: " + "; ".join(tier_reasons) + ". Check attribution and wording.")
    if issues:
        flags.extend(issues[:6])
        hold.append("careful-mode checks" if careful else "facts check")
    if spotlight:
        flags.append("SPOTLIGHT: only specialist/regional/official sources so far; no big national outlet has "
                     "carried it. Check the original report before approving.")
        if not config.SPOTLIGHT_AUTO and a.get("source_kind") != "govt":
            hold.append("spotlight (single specialist source)")
    elif developing:
        flags.append("Developing: single outlet so far")

    # No photo for careful stories: a picture of a place could suggest where an alleged crime happened.
    photo = None if careful else photos.find_photo(draft.get("photo_query") or fallback_photo_query(a))
    if photo:
        draft["photo_credit"], draft["photo_source"] = photo["credit"], photo["source"]
        runlog.note(f"{tag} → photo from {photo['source']}")
    card = cards.render_card(draft["card_headline"] or a["title"], a.get("category") or "Society",
                             source_line(a), a.get("state") or "National", developing, spotlight=spotlight,
                             photo=photo["image"] if photo else None,
                             photo_credit=photo["credit"] if photo else "")
    card_url = media.upload_card(card, f"{a['id']}-{int(time.time())}.jpg")
    if config.STORIES:
        try:
            story = cards.render_story(draft["card_headline"] or a["title"], a.get("category") or "",
                                       a.get("state") or "National", photo["image"] if photo else None)
            draft["story_url"] = media.upload_card(story, f"{a['id']}-story-{int(time.time())}.jpg")
        except Exception as ex:
            runlog.warn(f"{tag} → story image not made: {str(ex)[:120]}")

    update = {"draft": draft, "card_url": card_url, "breaking": breaking,
              "hold_reason": "; ".join(hold) or None}
    merged = {**a, **update}
    if mode == "auto" and hold:
        # No approval step: anything the safety rules would hold is simply not posted.
        db.update_article(a["id"], {**update, "status": "discarded"})
        runlog.note(f"{tag} → not posted, safety rule: {'; '.join(hold)}")
        return "skipped_safety"
    if story_only and not hold and mode == "auto":
        # A follow-up development: no new feed post, just a Story (the earlier post stays the main one).
        db.update_article(a["id"], {**update, "status": "published", "posted_at": iso(now_utc()),
                                    "results": {"story_only": True}})
        if draft.get("story_url") and not config.DRY_RUN:
            post_stories(a, draft["story_url"], db)
        runlog.note(f"{tag} → follow-up: Story only, no new feed post")
        runlog.activity()
        telegram.send_text(f"Story only #{a['id']}: {a['title'][:90]}\n(follow-up on a topic already posted)")
        return "story_only"
    if mode != "auto":  # approvals were switched back on with /review
        telegram.send_review(merged, draft, card_url, flags)
        db.update_article(a["id"], {**update, "status": "pending_review",
                                    "review_sent_at": iso(now_utc())})
        why = "; ".join(hold) if hold else "review mode"
        runlog.note(f"{tag} → sent to you for review ({why})")
        runlog.activity()
        return "sent_for_review"
    db.update_article(a["id"], {**update, "status": "approved"})
    return publish_article(db, {**merged, "status": "approved"})


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def civic_value(a: dict) -> float:
    """How much the story matters to people, ignoring how popular it is: (significance + impact) / 2."""
    return (float(a.get("significance") or 0) + float(a.get("impact") or 0)) / 2


def is_spotlight_candidate(a: dict, now: datetime) -> bool:
    """A Spotlight story: important, from a specialist/regional/official source, and no big
    mainstream outlet has carried it (after giving them a couple of hours to do so)."""
    if a.get("source_kind") not in SPOTLIGHT_SOURCE_KINDS or mainstream_count(a) > 0:
        return False
    if civic_value(a) < config.SPOTLIGHT_MIN_VALUE:
        return False
    seen = (now - parse_ts(a["created_at"])).total_seconds() / 3600 if a.get("created_at") else 0
    return seen >= config.SPOTLIGHT_MIN_AGE_HOURS


def effective_score(a: dict, now: datetime, posted_today: list) -> float:
    age_h = story_age_hours(a, now)
    corr = a.get("corroboration") or 1
    score = float(a["combined"]) + 0.4 * (min(corr, 4) - 1) - 0.08 * age_h
    # Popular (trending in India), critical (high significance) and important (high impact)
    # stories get a small lift. Same rules for every topic, party and community.
    score += float(a.get("trend_boost") or 0)
    if float(a.get("significance") or 0) >= 8:
        score += config.CRITICAL_BONUS
    if float(a.get("impact") or 0) >= 8:
        score += config.IMPORTANT_BONUS
    if a.get("is_factcheck"):
        score += 0.8  # keep corrections visible
    posted_today = stories(posted_today)
    score -= 0.8 * sum(1 for p in posted_today if p.get("category") == a.get("category"))
    if a.get("state") not in (None, "National"):
        score -= 0.5 * sum(1 for p in posted_today if p.get("state") == a.get("state"))
    return score


def plan_slot(db, now: datetime = None, exclude: set = None, used_cats: set = None) -> dict:
    """Decide what this slot should post, and explain the decision.
    Returns {"picked": [...], "notes": [human-readable reasons]}."""
    now = now or now_utc()
    posted_today = stories(db.list_posted_since(iso(ist_day_start(now))))
    pending_all = db.list_by_status("pending_review")
    pending = stories(pending_all)
    fast_n = sum(1 for p in posted_today if is_fast_track(p))
    notes = [f"Today so far: {len(posted_today) - fast_n}/{config.MAX_POSTS_PER_DAY} posted"
             f"{f' (+{fast_n} celebrity fast-track)' if fast_n else ''} · "
             f"{len(pending_all)} waiting for your tap · mode: {get_mode(db)}"]
    regular = [p for p in posted_today if not is_fast_track(p)]  # celebrity fast-track posts have their own cap
    allowed = min(config.MAX_POSTS_PER_SLOT, config.MAX_POSTS_PER_DAY - len(regular))
    if config.MAX_POSTS_PER_DAY - len(regular) <= 0:
        notes.append(f"⛔ Daily limit reached ({config.MAX_POSTS_PER_DAY} posts). Nothing more today.")
        return {"picked": [], "notes": notes}
    if get_mode(db) != "auto":
        room = config.MAX_PENDING_REVIEW - len(pending_all)
        if room <= 0:
            notes.append(f"⛔ {len(pending_all)} posts are waiting for your approval (limit {config.MAX_PENDING_REVIEW}). "
                         "Tap Approve or Reject on them in Telegram and the next slot will continue.")
            return {"picked": [], "notes": notes}
        allowed = min(allowed, room)

    cands = [a for a in db.list_by_status("candidate")
             if a.get("combined") is not None and not is_too_old(a, now) and a["id"] not in (exclude or ())]
    if not cands:
        waiting = len(db.list_by_status("new"))
        notes.append(f"⛔ No scored candidates in the queue. {waiting} stories are waiting to be scored by the hourly "
                     "ingest job. If this stays at 0, open the latest Ingest report (it lists AI errors and feed problems).")
        return {"picked": [], "notes": notes}

    politics = sum(1 for p in posted_today + pending if p.get("category") == "Politics")
    cands.sort(key=lambda a: effective_score(a, now, posted_today), reverse=True)
    notes.append(f"{len(cands)} candidates; best: {cands[0]['combined']} · {cands[0].get('category')} · "
                 f"{cands[0]['title'][:70]}")
    picked, used_cats, used_states = [], set(used_cats or ()), set()

    def fits(a) -> bool:
        if a.get("category") in used_cats:
            return False
        if a.get("category") == "Politics" and politics >= config.MAX_POLITICS_PER_DAY:
            return False  # keep the feed balanced across topics
        st = a.get("state")
        return not (st not in (None, "National") and st in used_states)

    def take(a, spotlight=False):
        nonlocal politics
        picked.append({**a, "spotlight": True} if spotlight else a)
        if a.get("category") == "Politics":
            politics += 1
        used_cats.add(a.get("category"))
        used_states.add(a.get("state"))

    # Spotlight lane: at most SPOTLIGHT_PER_DAY a day, takes one place in this slot.
    spot_used = sum(1 for p in posted_today + pending if (p.get("draft") or {}).get("spotlight"))
    if spot_used < config.SPOTLIGHT_PER_DAY:
        spots = sorted((a for a in cands if is_spotlight_candidate(a, now)),
                       key=lambda a: civic_value(a) + float(a.get("trend_boost") or 0), reverse=True)
        for a in spots:
            if fits(a):
                take(a, spotlight=True)
                cands = [c for c in cands if c["id"] != a["id"]]
                notes.append(f"🔦 Spotlight pick: only specialist/official outlets carry it (value {civic_value(a):.1f})")
                break
    for a in cands:
        if len(picked) >= allowed:
            break
        if fits(a):
            take(a)
    if not picked:
        notes.append("⛔ Candidates exist but none passed the balance rules (category/state repeats or the Politics cap).")
    return {"picked": picked[:max(allowed, 0)], "notes": notes}


def select_for_slot(db, now: datetime = None) -> list:
    return plan_slot(db, now)["picked"]


def run_slot(db, now: datetime = None) -> dict:
    if is_paused(db):
        runlog.note("⏸ Paused: nothing will be posted. Send /resume in Telegram to continue.")
        return {"paused": True}
    expired = expire_old(db, now)
    if expired:
        runlog.note(f"Cleaned up {expired} expired items")
    outcomes, tried, slot_cats = [], set(), set()
    for round_no in range(1, 5):
        # A story the safety rules skip (or whose drafting failed) is replaced by the next best one.
        plan = plan_slot(db, now, exclude=tried, used_cats=slot_cats)
        if round_no == 1:
            for n in plan["notes"]:
                runlog.note(n)
        posted_now = sum(1 for o in outcomes if o in ("published", "sent_for_review"))
        picked = plan["picked"][: max(0, config.MAX_POSTS_PER_SLOT - posted_now)]
        if not picked:
            if not outcomes:
                runlog.note("Result: nothing was posted this slot (reason above).")
            break
        runlog.note(f"Selected {len(picked)} for this slot:")
        for a in picked:
            tried.add(a["id"])
            runlog.note(f"  #{a['id']} · {a['combined']} · {a.get('category')} · {a.get('state')}"
                        f"{' · SPOTLIGHT' if a.get('spotlight') else ''}\n    {a['title'][:90]}")
            outcomes.append(process_candidate(db, a))
            if outcomes[-1] in ("published", "sent_for_review"):
                slot_cats.add(a.get("category"))  # one story per topic in a slot
        if sum(1 for o in outcomes if o in ("published", "sent_for_review")) >= config.MAX_POSTS_PER_SLOT:
            break
    return {"processed": len(outcomes), "outcomes": outcomes}


def celebrity_pass(db, now: datetime = None) -> list:
    """Fast-track for well-known people: post as soon as the story is scored, without waiting for a slot.
    One database check for duplicates first; one trusted source is enough (see celeb_single_source_ok)."""
    now = now or now_utc()
    if not config.CELEBRITY_FAST or is_paused(db):
        return []
    todays = stories(db.list_posted_since(iso(ist_day_start(now)))) + stories(db.list_by_status("pending_review"))
    used = sum(1 for p in todays if is_fast_track(p))
    cands = [a for a in db.list_by_status("candidate")
             if celeb_name(a) and a.get("combined") is not None and not is_too_old(a, now)]
    cands.sort(key=lambda a: (a.get("corroboration") or 1, a["combined"]), reverse=True)
    out, waiting = [], 0
    for a in cands:
        star = celeb_name(a)
        if used >= config.CELEBRITY_PER_DAY:
            runlog.note(f"⭐ Celebrity fast-track: daily limit of {config.CELEBRITY_PER_DAY} reached; the rest wait for a normal slot")
            break
        if not celeb_single_source_ok(a, star):
            waiting += 1
            continue
        handled = [r for r in db.recent_articles(config.DATA_RETENTION_HOURS)
                   if r["id"] != a["id"] and r.get("status") in ("published", "approved", "pending_review")]
        dup = dedup.find_match(a["title"], handled)
        if dup:
            db.update_article(a["id"], {"status": "discarded", "hold_reason": f"duplicate of #{dup['id']}"})
            runlog.note(f"⭐ #{a['id']} {star}: already covered (#{dup['id']}), skipped")
            continue
        runlog.note(f"⭐ Celebrity fast-track: #{a['id']} ({a['combined']}) {star} · {a['title'][:80]}")
        result = process_candidate(db, a, fast=True)
        out.append(result)
        if result in ("published", "sent_for_review"):
            used += 1
        elif result == "deferred":
            break  # AI free limit: try again on the next run
    if waiting:
        runlog.note(f"⭐ {waiting} celebrity stor{'y' if waiting == 1 else 'ies'} waiting for a second source")
    return out


URGENT_WORDS = re.compile(
    r"\b(lockdown|curfew|evacuat\w*|earthquake|tsunami|cyclone|landslide|cloudburst|flash flood|terror attack|"
    r"blast|stampede|plane crash|train accident|derail\w*|war|ceasefire|missile|national emergency|"
    r"internet (shutdown|suspended)|section 144|outbreak|pandemic|nationwide|bank run|rbi (repo|rate)|"
    r"currency ban|demonetis\w*)\b", re.I)


def is_urgent(a: dict) -> bool:
    """Urgent/critical: very high significance, or high significance with an emergency word in the headline,
    carried by one trusted source (main outlet, official or fact-checker) or by 2+ outlets."""
    sig = float(a.get("significance") or 0)
    head = f"{a.get('title') or ''} {a.get('snippet') or ''}"
    big = sig >= config.URGENT_SIGNIFICANCE or (sig >= config.URGENT_WORD_SIGNIFICANCE and bool(URGENT_WORDS.search(head)))
    trusted = (a.get("corroboration") or 1) >= 2 or a.get("source_kind") in ("govt", "news", "factcheck")
    return big and trusted


def breaking_pass(db, now: datetime = None) -> list:
    """Fast-track for urgent/critical news: (score >= BREAKING_SCORE and 2+ outlets) or is_urgent (one trusted
    source is enough for a lockdown, major disaster or similar). Capped per day; careful rules still apply."""
    now = now or now_utc()
    if is_paused(db):
        return []
    posted = stories(db.list_posted_since(iso(ist_day_start(now))))
    used = sum(1 for p in posted if p.get("breaking"))
    used += sum(1 for p in db.list_by_status("pending_review") if p.get("breaking"))
    out = []
    recent = stories(db.list_posted_since(iso(now - timedelta(hours=config.URGENT_FOLLOWUP_HOURS))))

    def urgent_followup(a) -> bool:  # same state + category already posted recently = a development, not a new event
        st = a.get("state")
        return st not in (None, "", "National") and any(
            p.get("state") == st and p.get("category") == a.get("category") for p in recent)

    cands = [a for a in db.list_by_status("candidate")
             if (((a.get("combined") or 0) >= config.BREAKING_SCORE
                  and (a.get("corroboration") or 1) >= config.BREAKING_MIN_SOURCES) or is_urgent(a))
             and not celeb_name(a) and not is_too_old(a, now)]
    cands.sort(key=lambda a: a["combined"], reverse=True)
    for a in cands:
        if used >= config.MAX_BREAKING_PER_DAY or len(posted) + len(out) >= config.MAX_POSTS_PER_DAY:
            break
        runlog.note(f"⚡ Urgent fast-track: #{a['id']} ({a['combined']}, {a.get('corroboration')} outlet(s)) {a['title'][:80]}")
        out.append(process_candidate(db, a, breaking=True, story_only=urgent_followup(a)))
        used += 1
    return out


# ---------------------------------------------------------------------------
# Telegram approvals + owner commands
# ---------------------------------------------------------------------------
def _owner(chat_id) -> bool:
    return str(chat_id) == str(config.TELEGRAM_CHAT_ID)


def handle_callback(db, cb: dict) -> str:
    chat = (cb.get("message") or {}).get("chat", {}).get("id")
    if not _owner(chat):
        return "ignored"
    action, _, raw_id = (cb.get("data") or "").partition(":")
    article = db.get_article(int(raw_id)) if raw_id.isdigit() else None
    msg = cb.get("message") or {}
    if action == "t":  # retry the platforms that failed
        if not article or article["status"] not in ("failed", "published"):
            telegram.answer_callback(cb["id"], "Nothing to retry.")
            return "stale"
        telegram.answer_callback(cb["id"], "Retrying…")
        telegram.clear_buttons(chat, msg.get("message_id"))
        return retry_failed(db, article)
    if not article or article["status"] != "pending_review":
        telegram.answer_callback(cb["id"], "Already handled or expired.")
        return "stale"
    if action == "r":
        db.update_article(article["id"], {"status": "rejected"})
        telegram.answer_callback(cb["id"], "Rejected.")
        telegram.clear_buttons(chat, msg.get("message_id"))
        runlog.note(f"#{article['id']} rejected: {article['title'][:70]}")
        runlog.activity()
        return "rejected"
    if action == "a":
        telegram.answer_callback(cb["id"], "Approved. Posting…")
        telegram.clear_buttons(chat, msg.get("message_id"))
        db.update_article(article["id"], {"status": "approved"})
        runlog.note(f"#{article['id']} approved by you: {article['title'][:70]}")
        return publish_article(db, {**article, "status": "approved"})
    return "ignored"


def retract(db, raw_id: str) -> None:
    """Corrections policy: pull a post that turned out wrong. Instagram/Threads need a manual delete."""
    article = db.get_article(int(raw_id)) if raw_id.isdigit() else None
    if not article or article["status"] != "published":
        telegram.send_text("Usage: /retract <id>  (the id is shown on each post report)")
        return
    notes = []
    fb = (article.get("results") or {}).get("facebook") or {}
    if fb.get("ok") and fb.get("id") not in (None, "dry-run"):
        try:
            meta.delete_facebook(fb["id"])
            notes.append("Facebook: deleted")
        except Exception as ex:
            notes.append(f"Facebook: FAILED to delete ({str(ex)[:120]}). Delete it by hand.")
    notes.append("Instagram and Threads: please delete these posts by hand in the apps "
                 "(their APIs don't allow deleting). Consider a short correction post.")
    db.update_article(article["id"], {"status": "retracted"})
    telegram.send_text(f"Retracted #{article['id']}: {article['title'][:80]}\n" + "\n".join(notes))


HELP = ("Commands:\n/status  queue, today's posts and when each job last ran\n/pause  stop all posting\n/resume  resume\n"
        "/retry <id>  retry the platforms that failed for a post\n"
        "/retract <id>  take down a published post (deletes on Facebook)\n"
        "/fbtoken <token>  renew the Facebook/Instagram connection\n/review  approve every post here first\n/auto  post automatically (default); risky stories are skipped")


def status_text(db) -> str:
    c = db.status_counts(72)
    posted = stories(db.list_posted_since(iso(ist_day_start(now_utc()))))
    return "\n".join([
        f"Mode: {get_mode(db)}{'  (PAUSED)' if is_paused(db) else ''}",
        f"Posted today: {len(posted)}/{config.MAX_POSTS_PER_DAY}",
        f"Awaiting your tap: {c.get('pending_review', 0)}",
        f"Candidates in queue: {c.get('candidate', 0)} · waiting to be scored: {c.get('new', 0)}",
        f"Platforms: {', '.join(config.enabled_platforms()) or 'none configured'}",
        "", "Last runs:"] + runlog.heartbeat_lines(db))


def handle_message(db, msg: dict) -> str:
    if not _owner((msg.get("chat") or {}).get("id")):
        return "ignored"
    parts = (msg.get("text") or "").strip().split()
    cmd = parts[0].lower().split("@")[0] if parts else ""
    if cmd == "/pause":
        db.set_setting("paused", "1")
        telegram.send_text("⏸ Paused. Nothing will be posted until /resume.")
    elif cmd == "/resume":
        db.set_setting("paused", "0")
        telegram.send_text("▶️ Resumed.")
    elif cmd == "/review":
        db.set_setting("mode2", "review")
        telegram.send_text("Review mode ON: every post waits for your approval. Send /auto to post without approval.")
    elif cmd == "/auto":
        db.set_setting("mode2", "auto")
        telegram.send_text("Auto mode ON: clean stories post by themselves. Stories that trip a safety rule "
                           "(election blackout, failed fact or privacy checks) are skipped; sensitive topics are posted under strict careful rules.")
    elif cmd == "/fbtoken":
        token = parts[1] if len(parts) > 1 else ""
        telegram.delete_message((msg.get("chat") or {}).get("id"), msg.get("message_id"))  # the token is a secret
        if not token:
            telegram.send_text("Usage: /fbtoken <short-lived user token from the Graph API Explorer>")
        else:
            try:
                telegram.send_text("✅ " + save_fb_token(db, token) + "\nYour message with the token was deleted.")
            except Exception as ex:
                telegram.send_text("❌ Could not connect: " + runlog.redact(str(ex))[:300]
                                   + "\nTip: generate a fresh token in the Graph API Explorer with your Page ticked and try again.")
    elif cmd == "/retract":
        retract(db, parts[1] if len(parts) > 1 else "")
    elif cmd == "/retry":
        article = db.get_article(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
        if article and article["status"] in ("failed", "published"):
            retry_failed(db, article)
        else:
            telegram.send_text("Usage: /retry <id>  (a post that failed on at least one platform)")
    elif cmd == "/status":
        telegram.send_text(status_text(db))
    elif cmd in ("/start", "/help"):
        telegram.send_text(HELP)
    else:
        return "ignored"
    runlog.activity()
    return cmd
