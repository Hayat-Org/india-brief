"""Core pipeline. Every external dependency is reached through a module attribute
(ai, media, cards, meta, telegram) so tests can replace them."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from . import ai, cards, config, dedup, media, meta, safety, telegram
from . import trends as trendlib
from .sources import Feed

IST = timezone(timedelta(hours=5, minutes=30))
KIND_RANK = {"govt": 0, "factcheck": 0, "news": 1, "state_gn": 2}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def ist_day_start(now: datetime) -> datetime:
    n = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return n.astimezone(timezone.utc)


def stories(posted: list) -> list:
    """Daily caps and balance rules count news posts only, not the evening digest."""
    return [p for p in posted if p.get("source_kind") != "digest"]


def is_paused(db) -> bool:
    return db.get_setting("paused", "0") == "1"


def get_mode(db) -> str:
    return db.get_setting("mode", config.DEFAULT_MODE)


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


def fetch_feed(feed: Feed, limit: int = 15) -> list:
    import feedparser  # imported here so the rest of the pipeline is testable without it
    r = requests.get(feed.url, headers={"User-Agent": config.USER_AGENT}, timeout=20)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    out = []
    for e in parsed.entries:
        title, link = (e.get("title") or "").strip(), (e.get("link") or "").strip()
        if not title or not link:
            continue
        src = ""
        if isinstance(e.get("source"), dict):
            src = (e["source"].get("title") or "").strip()
        src = src or feed.name.replace("GN:", "Google News")
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*ts[:6], tzinfo=timezone.utc) if ts else now_utc()
        summary = BeautifulSoup(e.get("summary") or "", "html.parser").get_text(" ", strip=True)
        out.append(Entry(dedup.strip_source(title, src), link, summary[:600], src,
                         feed.kind, feed.state, published))
    out.sort(key=lambda x: x.published, reverse=True)
    return out[:limit]


def collect_entries(feeds: list, workers: int = 8):
    """Fetch all feeds in parallel. Returns (entries, failed_feed_names)."""
    entries, failed = [], []

    def work(feed):
        try:
            return feed, fetch_feed(feed), None
        except Exception as ex:
            return feed, [], str(ex)[:100]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for feed, items, err in pool.map(work, feeds):
            if err or not items:
                failed.append(f"{feed.name}: {err or 'no items'}")
            entries.extend(items)
    return entries, failed


# ---------------------------------------------------------------------------
# ingest: dedup + cross-source corroboration
# ---------------------------------------------------------------------------
def ingest(db, entries: list, now: datetime = None) -> dict:
    now = now or now_utc()
    recent = db.recent_articles(72)
    known_urls = {r["url"] for r in recent if r.get("url")}
    stats = {"seen": len(entries), "new": 0, "merged": 0, "dup": 0, "junk": 0, "old": 0}
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
                sources = list(match.get("sources") or []) + [{"name": e.source, "url": e.url}]
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
            "sources": [{"name": e.source, "url": e.url}], "corroboration": 1,
            "published_at": iso(e.published), "status": "new",
        })
        known_urls.add(e.url)
        recent.insert(0, row)
        stats["new"] += 1
    return stats


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def score_pending(db, now: datetime = None, trends: dict = None) -> dict:
    now = now or now_utc()
    new = db.list_by_status("new", limit=500)
    stats = {"scored": 0, "candidates": 0, "discarded": 0, "errors": 0, "stale": 0}
    fresh = []
    for a in new:
        if now - parse_ts(a["created_at"]) > timedelta(hours=config.ARTICLE_MAX_AGE_HOURS):
            db.update_article(a["id"], {"status": "discarded", "hold_reason": "stale"})
            stats["stale"] += 1
        else:
            fresh.append(a)
    # Stories several outlets cover, then official/fact-check sources, get scored first.
    fresh.sort(key=lambda a: (-(a.get("corroboration") or 1),
                              KIND_RANK.get(a.get("source_kind"), 2)))
    for a in fresh[: config.MAX_SCORE_PER_RUN]:
        try:
            s = ai.score_article(a)
        except Exception as ex:
            stats["errors"] += 1
            print(f"score error #{a['id']}: {str(ex)[:150]}")
            continue
        stats["scored"] += 1
        keyword_reason = safety.is_sensitive(f"{a['title']} {a.get('snippet') or ''}")
        sensitive = s["sensitive"] or bool(keyword_reason)
        reason = s["sensitive_reason"] or keyword_reason or ""
        values = {
            "category": s["category"], "state": s["state"],
            "significance": s["significance"], "impact": s["impact"], "interest": s["interest"],
            "combined": s["combined"], "sensitive": sensitive, "sensitive_reason": reason,
            "is_factcheck": s["is_factcheck"],
            "trend_boost": trendlib.trend_boost(a["title"], trends or {}),
        }
        if not s["is_india"] or s["is_opinion"] or s["is_promotional"] or s["combined"] < config.MIN_SCORE:
            values["status"] = "discarded"
            stats["discarded"] += 1
        else:
            values["status"] = "candidate"
            stats["candidates"] += 1
        db.update_article(a["id"], values)
    return stats


def expire_old(db, now: datetime = None) -> int:
    """Old candidates are no longer news; stale review items expire."""
    now, n = now or now_utc(), 0
    for a in db.list_by_status("candidate"):
        if now - parse_ts(a["created_at"]) > timedelta(hours=config.ARTICLE_MAX_AGE_HOURS):
            db.update_article(a["id"], {"status": "discarded", "hold_reason": "stale"})
            n += 1
    for a in db.list_by_status("pending_review"):
        sent = parse_ts(a.get("review_sent_at") or a["created_at"])
        if now - sent > timedelta(hours=config.REVIEW_EXPIRY_HOURS):
            db.update_article(a["id"], {"status": "expired"})
            n += 1
    return n


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
    src = source_line(a)
    cat_tag = _tag(a.get("category") or "")
    topic = [_tag(t) for t in draft.get("hashtags", [])]
    state = a.get("state") or "National"
    state_tag = [] if state == "National" else [_tag(state)]
    threads = shorten(draft["threads"], 400) + f"\n\nvia {src}"
    if topic:
        threads += f"\n\n{topic[0]}"
    facebook = (shorten(draft["facebook"], 700) + f"\n\nSource: {src}\n"
                + " ".join(["#India", cat_tag] + topic[:1]))
    ig_tags = list(dict.fromkeys(topic[:2] + state_tag + [cat_tag, "#India"]))[:5]  # Meta: keep to <=5
    instagram = (shorten(draft["instagram"], 1000)
                 + f"\n\nSource: {src}. Read the full story at the original publisher.\n\n"
                 + " ".join(ig_tags))
    return {"threads": threads[:490], "facebook": facebook, "instagram": instagram[:2100]}


def threads_token(db) -> str:
    return db.get_setting("threads_token", "") or config.THREADS_ACCESS_TOKEN


def publish_article(db, a: dict) -> str:
    """Post to every configured platform independently; one failure never blocks the others."""
    draft = a["draft"]
    is_digest = a.get("source_kind") == "digest"
    if is_digest:  # digest texts are already final; links live inside the Facebook text
        texts = {k: draft[k] for k in ("threads", "facebook", "instagram")}
        link = None
    else:
        texts = build_texts(draft, a)
        link = (draft.get("link") or a["url"]) if config.ATTACH_LINKS else None
    alt = f"{draft.get('card_headline') or a['title']}. Source: {source_line(a)}"
    platforms = config.enabled_platforms()
    results = {}
    steps = {
        "threads": lambda: meta.post_threads(texts["threads"], link, threads_token(db)),
        "facebook": lambda: meta.post_facebook(texts["facebook"], link),
        "instagram": (lambda: meta.post_instagram_carousel(texts["instagram"], draft["slides"],
                                                           draft.get("alts")))
        if is_digest else (lambda: meta.post_instagram(texts["instagram"], a["card_url"], alt)),
    }
    for name, fn in steps.items():
        if name not in platforms:
            results[name] = {"ok": False, "skipped": True}
            continue
        try:
            if config.DRY_RUN:
                print(f"[dry-run] would post to {name}:\n{texts[name]}\n")
                results[name] = {"ok": True, "id": "dry-run"}
            else:
                results[name] = {"ok": True, "id": fn()}
        except Exception as ex:
            results[name] = {"ok": False, "error": str(ex)[:300]}
        time.sleep(0 if config.DRY_RUN else 3)  # polite pacing between API calls
    ok = [k for k, v in results.items() if v.get("ok")]
    status = "published" if ok else "failed"
    db.update_article(a["id"], {
        "status": status, "results": results,
        "posted_at": iso(now_utc()) if ok else None,
    })
    lines = [f"{'✅' if v.get('ok') else ('⏭' if v.get('skipped') else '⚠️')} {k}"
             + (f": {v.get('error')}" if v.get("error") else "") for k, v in results.items()]
    telegram.send_text(f"{'Posted' if ok else 'FAILED'} #{a['id']}: {a['title'][:90]}\n" + "\n".join(lines))
    return status


def process_candidate(db, a: dict, breaking: bool = False) -> str:
    """Draft -> safety checks -> card -> (Telegram review | publish)."""
    mode = get_mode(db)
    link = media.resolve_url(a["url"])
    body = media.fetch_text(link)
    developing = (a.get("corroboration") or 1) < 2 and a.get("source_kind") not in ("govt", "factcheck")
    facts = f"{a['title']}\n{a.get('snippet') or ''}\n{body}"

    try:
        draft = ai.write_post(a, body, developing)
        issues = safety.check_draft(draft, facts)
        if issues:  # one retry; keep the cleaner draft
            try:
                second = ai.write_post(a, body, developing)
                second_issues = safety.check_draft(second, facts)
                if len(second_issues) < len(issues):
                    draft, issues = second, second_issues
            except Exception:
                pass
    except Exception as ex:
        attempts = (a.get("attempts") or 0) + 1
        db.update_article(a["id"], {"attempts": attempts,
                                    "status": "failed" if attempts >= 3 else a["status"]})
        print(f"draft error #{a['id']}: {str(ex)[:150]}")
        return "draft_failed"

    draft["link"], draft["developing"] = link, developing
    flags, hold = [], []
    sens = a.get("sensitive") or safety.is_sensitive(f"{a['title']} {a.get('snippet') or ''}")
    if sens:
        flags.append(f"Sensitive topic: {a.get('sensitive_reason') or sens}")
        hold.append("sensitive topic")
    if a.get("category") == "Crime & Law":
        flags.append("Crime story: may name accused persons. Check wording (alleged/accused)")
        hold.append("crime/legal")
    if safety.in_election_silence() and a.get("category") in ("Politics", "Government & Policy"):
        flags.append("Election-silence window: political post")
        hold.append("election silence")
    if issues:
        flags.extend(issues[:6])
        hold.append("facts check")
    if developing:
        flags.append("Developing: single outlet so far")

    card = cards.render_card(draft["card_headline"] or a["title"], a.get("category") or "Society",
                             source_line(a), a.get("state") or "National", developing)
    card_url = media.upload_card(card, f"{a['id']}-{int(time.time())}.jpg")

    update = {"draft": draft, "card_url": card_url, "breaking": breaking,
              "hold_reason": "; ".join(hold) or None}
    merged = {**a, **update}
    if mode != "auto" or hold:  # anything other than an explicit "auto" means human review
        telegram.send_review(merged, draft, card_url, flags)
        db.update_article(a["id"], {**update, "status": "pending_review",
                                    "review_sent_at": iso(now_utc())})
        return "sent_for_review"
    db.update_article(a["id"], {**update, "status": "approved"})
    return publish_article(db, {**merged, "status": "approved"})


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def effective_score(a: dict, now: datetime, posted_today: list) -> float:
    age_h = (now - parse_ts(a["created_at"])).total_seconds() / 3600
    corr = a.get("corroboration") or 1
    score = float(a["combined"]) + 0.4 * (min(corr, 4) - 1) - 0.12 * age_h
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


def select_for_slot(db, now: datetime = None) -> list:
    now = now or now_utc()
    posted_today = stories(db.list_posted_since(iso(ist_day_start(now))))
    pending = db.list_by_status("pending_review")
    allowed = min(config.MAX_POSTS_PER_SLOT, config.MAX_POSTS_PER_DAY - len(posted_today))
    if get_mode(db) != "auto":
        allowed = min(allowed, config.MAX_PENDING_REVIEW - len(pending))
    if allowed <= 0:
        return []
    politics = sum(1 for p in posted_today + stories(pending) if p.get("category") == "Politics")
    cands = [a for a in db.list_by_status("candidate") if a.get("combined") is not None]
    cands.sort(key=lambda a: effective_score(a, now, posted_today), reverse=True)
    picked, used_cats, used_states = [], set(), set()
    for a in cands:
        if len(picked) >= allowed:
            break
        if a.get("category") in used_cats:
            continue
        if a.get("category") == "Politics" and politics >= config.MAX_POLITICS_PER_DAY:
            continue  # keep the feed balanced across topics
        st = a.get("state")
        if st not in (None, "National") and st in used_states:
            continue
        picked.append(a)
        if a.get("category") == "Politics":
            politics += 1
        used_cats.add(a.get("category"))
        used_states.add(st)
    return picked


def run_slot(db, now: datetime = None) -> dict:
    if is_paused(db):
        return {"paused": True}
    expire_old(db, now)
    outcomes = [process_candidate(db, a) for a in select_for_slot(db, now)]
    return {"processed": len(outcomes), "outcomes": outcomes}


def breaking_pass(db, now: datetime = None) -> list:
    """Fast-track: very high score AND confirmed by 2+ outlets. Capped per day."""
    now = now or now_utc()
    if is_paused(db):
        return []
    posted = stories(db.list_posted_since(iso(ist_day_start(now))))
    used = sum(1 for p in posted if p.get("breaking"))
    used += sum(1 for p in db.list_by_status("pending_review") if p.get("breaking"))
    out = []
    cands = [a for a in db.list_by_status("candidate")
             if (a.get("combined") or 0) >= config.BREAKING_SCORE
             and (a.get("corroboration") or 1) >= config.BREAKING_MIN_SOURCES]
    cands.sort(key=lambda a: a["combined"], reverse=True)
    for a in cands:
        if used >= config.MAX_BREAKING_PER_DAY or len(posted) + len(out) >= config.MAX_POSTS_PER_DAY:
            break
        out.append(process_candidate(db, a, breaking=True))
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
    if not article or article["status"] != "pending_review":
        telegram.answer_callback(cb["id"], "Already handled or expired.")
        return "stale"
    msg = cb.get("message") or {}
    if action == "r":
        db.update_article(article["id"], {"status": "rejected"})
        telegram.answer_callback(cb["id"], "Rejected.")
        telegram.clear_buttons(chat, msg.get("message_id"))
        return "rejected"
    if action == "a":
        telegram.answer_callback(cb["id"], "Approved. Posting…")
        telegram.clear_buttons(chat, msg.get("message_id"))
        db.update_article(article["id"], {"status": "approved"})
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


HELP = ("Commands:\n/status  queue and today's posts\n/pause  stop all posting\n/resume  resume\n"
        "/retract <id>  take down a published post (deletes on Facebook)\n"
        "/review  approve every post here first\n/auto  auto-post non-sensitive stories")


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
        db.set_setting("mode", "review")
        telegram.send_text("Review mode ON: every post waits for your approval.")
    elif cmd == "/auto":
        db.set_setting("mode", "auto")
        telegram.send_text("Auto mode ON: sensitive or flagged stories are still held for you.")
    elif cmd == "/retract":
        retract(db, parts[1] if len(parts) > 1 else "")
    elif cmd == "/status":
        posted = stories(db.list_posted_since(iso(ist_day_start(now_utc()))))
        telegram.send_text(
            f"Mode: {get_mode(db)}{'  (PAUSED)' if is_paused(db) else ''}\n"
            f"Posted today: {len(posted)}/{config.MAX_POSTS_PER_DAY}\n"
            f"Awaiting review: {len(db.list_by_status('pending_review'))}\n"
            f"Candidates in queue: {len(db.list_by_status('candidate'))}\n"
            f"Platforms: {', '.join(config.enabled_platforms()) or 'none configured'}")
    elif cmd in ("/start", "/help"):
        telegram.send_text(HELP)
    else:
        return "ignored"
    return cmd
