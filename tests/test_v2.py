"""Tests for weighting, trend signal, compliance guards, retract and the daily digest."""
from datetime import datetime, timedelta, timezone

from newsbot import ai, cards, config, digest, pipeline, safety, sources, trends
from tests.fakes import FakeDB
from tests.test_pipeline import entry, env, seed  # noqa: F401  (env is a fixture)

TRENDS_XML = """<rss><channel>
<item><title>Chandrayaan</title><ht:approx_traffic>200K+</ht:approx_traffic></item>
<item><title>India vs England</title><ht:approx_traffic>50K+</ht:approx_traffic></item>
<item><title>Rain</title><ht:approx_traffic>1M+</ht:approx_traffic></item>
</channel></rss>"""


def test_brand_defaults():
    assert config.BRAND_NAME == "India In Short"
    assert config.BRAND_HANDLE == "@indiainshort.daily"


def test_weights_sum_to_one_and_popularity_is_lifted():
    assert abs(config.W_SIGNIFICANCE + config.W_IMPACT + config.W_INTEREST - 1.0) < 1e-9
    assert config.W_INTEREST > 0.25  # popularity weighted above the first version


def test_trend_parsing_and_boost():
    t = trends.parse_trends(TRENDS_XML)
    assert t["chandrayaan"] == 200_000 and t["india vs england"] == 50_000
    assert trends.trend_boost("Chandrayaan mission update from ISRO", t) == config.TREND_BONUS_BIG
    assert trends.trend_boost("India vs England: toss delayed by rain", t) == config.TREND_BONUS
    assert trends.trend_boost("Rain lashes the city", t) == 0.0   # one short generic word
    assert trends.trend_boost("Budget session begins", t) == 0.0
    assert trends.trend_boost("anything", {}) == 0.0


def _cand(i, combined, **kw):
    base = {"id": i, "combined": combined, "significance": 6, "impact": 6, "interest": 6,
            "created_at": datetime.now(timezone.utc).isoformat(), "corroboration": 1,
            "category": f"cat{i}", "state": "National", "trend_boost": 0, "status": "candidate"}
    base.update(kw)
    return base


def test_critical_popular_important_rank_higher():
    now = datetime.now(timezone.utc)
    plain = pipeline.effective_score(_cand(1, 7.0), now, [])
    trending = pipeline.effective_score(_cand(2, 7.0, trend_boost=1.0), now, [])
    critical = pipeline.effective_score(_cand(3, 7.0, significance=9), now, [])
    important = pipeline.effective_score(_cand(4, 7.0, impact=9), now, [])
    assert trending > plain and critical > plain and important > plain
    assert trending - plain <= 1.01  # lift stays modest


def test_politics_cap(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(config, "MAX_POSTS_PER_SLOT", 5)
    monkeypatch.setattr(config, "MAX_PENDING_REVIEW", 10)
    for i in range(3):
        db.insert_article({"url": f"u{i}", "title": f"t{i}", "title_norm": f"t{i}", "source": "x",
                           "status": "candidate", "combined": 8, "category": "Politics", "state": "National"})
    db.insert_article({"url": "u9", "title": "sports", "title_norm": "sports", "source": "x",
                       "status": "candidate", "combined": 6, "category": "Sports", "state": "National"})
    picked = pipeline.select_for_slot(db)
    assert sum(1 for a in picked if a["category"] == "Politics") <= config.MAX_POLITICS_PER_DAY


def test_crime_stories_are_skipped_in_auto(env):
    db = FakeDB()
    db.set_setting("mode2", "auto")
    row = db.insert_article({"url": "c1", "title": "Floods in Assam affected", "title_norm": "x",
                             "snippet": "Floods in Assam affected 2,500 people.", "source": "NDTV",
                             "source_kind": "news", "status": "candidate", "combined": 7,
                             "category": "Crime & Law", "state": "Assam", "sources": [{"name": "NDTV", "url": "c1"}]})
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "skipped_safety"
    assert env["posts"] == []


def test_personal_data_and_poll_rules():
    facts = "Officials said on Monday. Assam"
    draft = {"threads": "Call 9876543210 for help in Assam.", "facebook": "Mail help@example.com in Assam.",
             "instagram": "Assam help line.", "card_headline": "Assam help"}
    issues = safety.check_draft(draft, facts)
    assert sum("personal data" in i for i in issues) == 2
    assert safety.is_sensitive("Exit polls predict a close contest")
    assert safety.is_sensitive("Opinion poll shows lead") == "election poll publication rules"


def test_google_news_switch():
    on, off = sources.all_feeds(True), sources.all_feeds(False)
    assert any(f.kind == "state_gn" for f in on)
    assert not any(f.name.startswith("GN:") or f.name == "Google News" for f in off)
    assert len(off) > 15  # direct publisher feeds remain


def _published(db, n, **kw):
    for i in range(n):
        row = db.insert_article({
            "url": f"https://pub/{i}", "title": f"Story number {i}", "title_norm": f"story {i}",
            "source": "The Hindu", "source_kind": "news", "sources": [{"name": "The Hindu", "url": "x"}],
            "status": "published", "combined": 8 - i * 0.3, "category": ["Sports", "Health", "Economy & Business",
            "Weather & Disaster", "Education", "Science & Tech"][i % 6], "state": "National",
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "draft": {"card_headline": f"Headline {i}", "link": f"https://pub/{i}"},
            "results": {"facebook": {"ok": True, "id": f"fb{i}"}}, **kw})
    return db


def test_digest_review_then_publish(env):
    db = _published(FakeDB(), 4)
    db.set_setting("mode2", "review")
    assert digest.run_digest(db) == "sent_for_review"
    assert len(env["reviews"]) == 1
    assert digest.run_digest(db) == "exists"          # only one digest per day
    d = db.list_by_status("pending_review")[0]
    assert d["source_kind"] == "digest" and len(d["draft"]["slides"]) == 5  # cover + 4
    assert len(d["draft"]["threads"]) <= 490
    cb = {"id": "x", "data": f"a:{d['id']}", "message": {"chat": {"id": 42}, "message_id": 1}}
    assert pipeline.handle_callback(db, cb) == "published"
    assert "instagram-carousel" in env["posts"] and "threads" in env["posts"] and "facebook" in env["posts"]


def test_digest_needs_enough_stories_and_does_not_use_daily_cap(env, monkeypatch):
    assert digest.run_digest(_published(FakeDB(), 2)) == "too_few"
    db = _published(FakeDB(), 4)
    db.set_setting("mode2", "auto")
    digest.run_digest(db)
    posted = db.list_posted_since(pipeline.iso(pipeline.ist_day_start(pipeline.now_utc())))
    assert len(posted) == 5 and len(pipeline.stories(posted)) == 4  # digest excluded from caps


def test_retract_deletes_facebook_and_warns_about_others(env):
    db = _published(FakeDB(), 1)
    pid = next(iter(db.rows))
    pipeline.handle_message(db, {"chat": {"id": 42}, "text": f"/retract {pid}"})
    assert "fb-deleted:fb0" in env["posts"]
    assert db.get_article(pid)["status"] == "retracted"
    assert any("by hand" in t for t in env["telegram"])
    # strangers cannot retract
    db2 = _published(FakeDB(), 1)
    pipeline.handle_message(db2, {"chat": {"id": 7}, "text": "/retract 1"})
    assert db2.get_article(1)["status"] == "published"


def test_digest_cards_render():
    assert cards.render_digest_cover(5)[:2] == b"\xff\xd8"
    assert cards.render_digest_slide(1, 5, "RBI keeps repo rate unchanged at 5.5%", "Economy & Business",
                                     "PIB, Mint")[:2] == b"\xff\xd8"


def test_empty_env_value_means_default(monkeypatch):
    import os
    monkeypatch.setattr(os, "environ", {**os.environ, "BRAND_NAME": "", "DEFAULT_MODE": " "})
    assert config.env("BRAND_NAME", "India In Short") == "India In Short"
    assert config.env("DEFAULT_MODE", "review") == "review"


def test_unknown_mode_never_autoposts(env):
    db = FakeDB()
    db.set_setting("mode2", "")          # blank or garbage must behave like review mode
    row = db.insert_article({"url": "m1", "title": "Floods in Assam affected", "title_norm": "x",
                             "snippet": "Floods in Assam affected 2,500 people.", "source": "NDTV",
                             "source_kind": "news", "status": "candidate", "combined": 7,
                             "category": "Weather & Disaster", "state": "Assam",
                             "sources": [{"name": "NDTV", "url": "m1"}]})
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "sent_for_review"
    assert env["posts"] == []
