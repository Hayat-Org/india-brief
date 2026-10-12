from datetime import datetime, timedelta, timezone

import pytest

from newsbot import ai, cards, config, media, meta, pipeline, telegram
from newsbot.pipeline import Entry
from tests.fakes import FakeDB


def entry(title, url, source, kind="news", minutes_ago=30, snippet="Officials said on Monday."):
    return Entry(title, url, snippet, source, kind, "",
                 datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


@pytest.fixture
def env(monkeypatch):
    """Stub every external service; record what would have been sent."""
    sent = {"telegram": [], "reviews": [], "posts": []}
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(config, "THREADS_USER_ID", "t1")
    monkeypatch.setattr(config, "FB_PAGE_ID", "f1")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "tok")
    monkeypatch.setattr(config, "IG_USER_ID", "i1")
    monkeypatch.setattr(config, "IG_TOKEN", "tok")
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(media, "resolve_url", lambda u: u)
    monkeypatch.setattr(media, "fetch_text", lambda u: "Floods in Assam affected 2,500 people.")
    monkeypatch.setattr(media, "upload_card", lambda b, n: f"https://cards/{n}")
    monkeypatch.setattr(telegram, "send_text", lambda t: sent["telegram"].append(t))
    monkeypatch.setattr(telegram, "send_review",
                        lambda a, d, u, f: sent["reviews"].append((a["id"], f)))
    monkeypatch.setattr(telegram, "answer_callback", lambda *a: None)
    monkeypatch.setattr(telegram, "clear_buttons", lambda *a: None)
    monkeypatch.setattr(meta, "post_threads", lambda t, l, k: sent["posts"].append("threads") or "t-1")
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None: sent["posts"].append("facebook") or "f-1")
    monkeypatch.setattr(meta, "post_instagram", lambda c, u, alt="": sent["posts"].append("instagram") or "i-1")
    monkeypatch.setattr(meta, "post_instagram_carousel",
                        lambda c, urls, alts=None: sent["posts"].append("instagram-carousel") or "i-2")
    monkeypatch.setattr(meta, "delete_facebook", lambda pid: sent["posts"].append("fb-deleted:" + pid))

    def fake_score(a):
        return {"is_india": True, "is_opinion": False, "is_promotional": False,
                "is_factcheck": False, "category": "Weather & Disaster", "state": "Assam",
                "significance": 9, "impact": 9, "interest": 8, "combined": 8.9,
                "sensitive": False, "sensitive_reason": ""}

    def fake_draft(a, body, developing):
        return {"threads": "Floods in Assam have affected 2,500 people, officials said.",
                "facebook": "Officials said floods in Assam affected 2,500 people.",
                "instagram": "Assam floods: 2,500 people affected, officials said.",
                "card_headline": "Assam floods affect 2,500 people", "hashtags": ["assamfloods"]}

    monkeypatch.setattr(ai, "score_article", fake_score)
    monkeypatch.setattr(ai, "write_post", fake_draft)
    return sent


def seed(db):
    return pipeline.ingest(db, [
        entry("Floods hit Assam, 2,500 affected", "https://a/1", "The Hindu"),
        entry("Assam floods: 2,500 people affected", "https://b/1", "NDTV"),
        entry("Fire breaks out in Mumbai building", "https://c/1", "HT"),
        entry("Aries horoscope today", "https://d/1", "TOI"),
        entry("Old story", "https://e/1", "TOI", minutes_ago=60 * 48),
    ])


def test_ingest_dedups_and_corroborates(env):
    db = FakeDB()
    stats = seed(db)
    assert stats["new"] == 2 and stats["merged"] == 1 and stats["junk"] == 1 and stats["old"] == 1
    flood = next(r for r in db.rows.values() if "Assam" in r["title"])
    assert flood["corroboration"] == 2
    # Re-running ingest adds nothing
    again = pipeline.ingest(db, [entry("Floods hit Assam, 2,500 affected", "https://a/1", "The Hindu")])
    assert again["new"] == 0


def test_same_outlet_does_not_inflate_corroboration(env):
    db = FakeDB()
    pipeline.ingest(db, [entry("Floods hit Assam, 2,500 affected", "https://a/1", "The Hindu")])
    pipeline.ingest(db, [entry("Floods hit Assam; 2,500 affected", "https://a/2", "The Hindu")])
    assert next(iter(db.rows.values()))["corroboration"] == 1


def test_scoring_and_review_mode_flow(env):
    db = FakeDB()
    seed(db)
    assert pipeline.score_pending(db)["candidates"] == 2
    out = pipeline.run_slot(db)
    assert out["processed"] == 1  # same category -> only one per slot
    assert len(env["reviews"]) == 1 and env["posts"] == []  # review mode: nothing posted yet
    pending = db.list_by_status("pending_review")[0]
    cb = {"id": "x", "data": f"a:{pending['id']}", "message": {"chat": {"id": 42}, "message_id": 1}}
    assert pipeline.handle_callback(db, cb) == "published"
    assert sorted(env["posts"]) == ["facebook", "instagram", "threads"]
    assert db.get_article(pending["id"])["status"] == "published"
    # Tapping twice does nothing
    assert pipeline.handle_callback(db, cb) == "stale"


def test_callback_from_stranger_is_ignored(env):
    db = FakeDB()
    seed(db)
    pipeline.score_pending(db)
    pipeline.run_slot(db)
    pid = db.list_by_status("pending_review")[0]["id"]
    cb = {"id": "x", "data": f"a:{pid}", "message": {"chat": {"id": 999}, "message_id": 1}}
    assert pipeline.handle_callback(db, cb) == "ignored"
    assert env["posts"] == []


def test_reject(env):
    db = FakeDB()
    seed(db)
    pipeline.score_pending(db)
    pipeline.run_slot(db)
    pid = db.list_by_status("pending_review")[0]["id"]
    cb = {"id": "x", "data": f"r:{pid}", "message": {"chat": {"id": 42}, "message_id": 1}}
    assert pipeline.handle_callback(db, cb) == "rejected"
    assert env["posts"] == []


def test_auto_mode_posts_clean_story_but_holds_sensitive(env):
    db = FakeDB()
    db.set_setting("mode", "auto")
    seed(db)
    pipeline.score_pending(db)
    flood = next(r for r in db.rows.values() if "Assam" in r["title"])
    pipeline.process_candidate(db, db.get_article(flood["id"]))
    assert sorted(env["posts"]) == ["facebook", "instagram", "threads"]

    env["posts"].clear()
    fire = next(r for r in db.rows.values() if "Mumbai" in r["title"])
    db.update_article(fire["id"], {"sensitive": True, "sensitive_reason": "violence"})
    assert pipeline.process_candidate(db, db.get_article(fire["id"])) == "sent_for_review"
    assert env["posts"] == []


def test_fact_issue_forces_review_even_in_auto(env, monkeypatch):
    db = FakeDB()
    db.set_setting("mode", "auto")
    seed(db)
    pipeline.score_pending(db)
    monkeypatch.setattr(ai, "write_post", lambda a, b, d: {
        "threads": "Floods hit 9,000 people.", "facebook": "Floods hit 9,000 people.",
        "instagram": "Floods hit 9,000 people.", "card_headline": "Floods hit 9,000", "hashtags": []})
    flood = next(r for r in db.rows.values() if "Assam" in r["title"])
    assert pipeline.process_candidate(db, db.get_article(flood["id"])) == "sent_for_review"
    assert env["posts"] == []


def test_breaking_needs_two_sources_and_respects_cap(env):
    db = FakeDB()
    db.set_setting("mode", "auto")
    seed(db)
    pipeline.score_pending(db)
    out = pipeline.breaking_pass(db)
    assert len(out) == 1  # Assam story has 2 outlets; Mumbai fire has only 1
    assert sorted(env["posts"]) == ["facebook", "instagram", "threads"]


def test_pause_stops_everything(env):
    db = FakeDB()
    seed(db)
    pipeline.score_pending(db)
    db.set_setting("paused", "1")
    assert pipeline.run_slot(db) == {"paused": True}
    assert pipeline.breaking_pass(db) == []


def test_daily_cap(env, monkeypatch):
    db = FakeDB()
    db.set_setting("mode", "auto")
    monkeypatch.setattr(config, "MAX_POSTS_PER_DAY", 0)
    seed(db)
    pipeline.score_pending(db)
    assert pipeline.select_for_slot(db) == []


def test_owner_commands(env):
    db = FakeDB()
    msg = lambda t: {"chat": {"id": 42}, "text": t}
    pipeline.handle_message(db, msg("/auto"))
    assert db.get_setting("mode") == "auto"
    pipeline.handle_message(db, msg("/pause"))
    assert pipeline.is_paused(db)
    assert pipeline.handle_message(db, {"chat": {"id": 7}, "text": "/resume"}) == "ignored"
    assert pipeline.is_paused(db)


def test_build_texts_limits_and_credit():
    a = {"category": "Weather & Disaster", "state": "Assam",
         "sources": [{"name": "The Hindu"}, {"name": "NDTV"}], "source": "The Hindu"}
    draft = {"threads": "x" * 600, "facebook": "y" * 900, "instagram": "z" * 1500,
             "hashtags": ["assamfloods"]}
    t = pipeline.build_texts(draft, a)
    assert len(t["threads"]) <= 490 and "via The Hindu, NDTV" in t["threads"]
    assert "Source: The Hindu, NDTV" in t["facebook"]
    assert "#WeatherDisaster" in t["instagram"] and "#Assam" in t["instagram"]
    assert t["instagram"].split("\n")[-1].count("#") <= 5


def test_card_renders():
    jpg = cards.render_card("Assam floods affect 2,500 people across three districts",
                            "Weather & Disaster", "The Hindu, NDTV", "Assam", developing=True)
    assert jpg[:2] == b"\xff\xd8" and len(jpg) > 5000
