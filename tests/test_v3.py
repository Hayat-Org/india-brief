"""Freshness, feed fallback, batch scoring, run reports, error hints, Spotlight lane, retry."""
from datetime import datetime, timedelta, timezone

from newsbot import ai, config, meta, pipeline, runlog, sources, telegram
from newsbot.pipeline import Entry
from newsbot.sources import Feed
from tests.fakes import FakeDB
from tests.test_pipeline import entry, env  # noqa: F401  (env is a fixture)


def hours_ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def add_candidate(db, title, kind="niche", hours_seen=5, sig=8, imp=8, interest=5, category="Environment",
                  state="Assam", sources_=None):
    return db.insert_article({
        "url": "https://x/" + title.replace(" ", "-"), "title": title, "title_norm": title.lower(),
        "snippet": "Officials said on Monday.", "source": "Mongabay India", "source_kind": kind,
        "sources": sources_ if sources_ is not None else [{"name": "Mongabay India", "url": "u", "kind": kind}],
        "corroboration": 1, "category": category, "state": state,
        "significance": sig, "impact": imp, "interest": interest, "combined": 6.5,
        "published_at": hours_ago(3), "created_at": hours_ago(hours_seen), "status": "candidate"})


def test_48_hour_rule_uses_published_time(env):
    db = FakeDB()
    stats = pipeline.ingest(db, [
        entry("Bridge collapse in Bihar injures 12", "https://a/1", "The Hindu", minutes_ago=47 * 60),
        entry("Rail line reopens in Kerala after floods", "https://a/2", "NDTV", minutes_ago=49 * 60)])
    assert stats["new"] == 1 and stats["old"] == 1
    # an already-stored story that is now too old is discarded instead of scored
    old = db.insert_article({"url": "u", "title": "t", "title_norm": "t", "status": "new",
                             "published_at": hours_ago(60), "sources": [], "created_at": hours_ago(1)})
    out = pipeline.score_pending(db)
    assert out["stale"] == 1 and db.get_article(old["id"])["status"] == "discarded"


def test_feed_fallback_when_blocked(monkeypatch):
    feed = Feed("PIB", "https://pib/rss", "govt", "National", site="pib.gov.in")
    calls = []

    def fake_get(url, timeout=25):
        calls.append(url)
        if "pib/rss" in url:
            raise RuntimeError("blocked")
        return b"x"

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(pipeline, "_get", fake_get)
    monkeypatch.setattr(pipeline, "_parse", lambda c, f, limit=15, now=None: [
        Entry("Govt launches scheme", "https://n/1", "", "PIB", "govt", "", now)])
    monkeypatch.setattr(config, "USE_GOOGLE_NEWS", True)
    res, items = pipeline.fetch_feed_result(feed)
    assert res.ok and res.via == "fallback" and len(items) == 1 and len(calls) == 2
    monkeypatch.setattr(config, "USE_GOOGLE_NEWS", False)
    res, items = pipeline.fetch_feed_result(feed)
    assert not res.ok and items == [] and res.error


def test_score_batch_maps_results_by_story(monkeypatch):
    arts = [{"id": 11, "title": "A", "snippet": "", "source": "x"}, {"id": 12, "title": "B", "snippet": "", "source": "y"}]
    reply = {"results": [{"id": 2, "is_india": True, "category": "Health", "state": "Kerala",
                          "significance": 7, "impact": 6, "interest": 5},
                         {"id": 1, "is_india": True, "category": "Economy & Business", "state": "National",
                          "significance": 9, "impact": 8, "interest": 7}]}
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: reply)
    out = ai.score_batch(arts)
    assert out[11]["category"] == "Economy & Business" and out[12]["state"] == "Kerala"


def test_run_report_contents_and_quiet_poll(env):
    log = runlog.start("ingest", "Ingest run")
    log.add("Feeds: 30/32 working")
    log.warn("feed down · PIB: HTTP 403")
    log.error("AI scoring failed for 3 stories")
    text = log.render()
    assert "Ingest run" in text and "Feeds: 30/32" in text and "PIB" in text and "AI scoring failed" in text
    assert log.icon() == "❌" and log.should_send()
    log.finish(None)
    assert env["telegram"] and "Ingest run" in env["telegram"][-1]

    env["telegram"].clear()
    quiet = runlog.start("poll", "Telegram actions", quiet_unless_activity=True)
    quiet.finish(None)
    assert env["telegram"] == []  # nothing happened, nothing sent
    loud = runlog.start("poll", "Telegram actions", quiet_unless_activity=True)
    loud.mark_activity()
    loud.finish(None)
    assert len(env["telegram"]) == 1


def test_report_level_important_skips_uneventful_runs(env, monkeypatch):
    monkeypatch.setattr(config, "REPORT_LEVEL", "important")
    log = runlog.start("publish", "Publish slot")
    log.add("nothing happened")
    log.finish(None)
    assert env["telegram"] == []
    log = runlog.start("publish", "Publish slot")
    log.error("boom")
    log.finish(None)
    assert len(env["telegram"]) == 1


def test_heartbeats_roundtrip():
    db = FakeDB()
    runlog.save_heartbeat(db, "ingest", True, "30 feeds")
    lines = runlog.heartbeat_lines(db)
    assert any("ingest" in l and "30 feeds" in l for l in lines)
    assert any("publish" in l and "not reported" in l for l in lines)


def test_explain_error_hints():
    assert "security check" in meta.explain_error("x: HTTP 400 code=190 sub=459 y")
    assert "expired" in meta.explain_error("x: HTTP 400 code=190 sub=463 Error validating access token")
    assert "IG_USER_ID" in meta.explain_error("x: HTTP 400 code=100 sub=33 Unsupported get request")
    assert "limit" in meta.explain_error("x: HTTP 400 code=9 sub=2207042 limit").lower()
    assert meta.explain_error("something unknown") == ""


def test_instagram_check_flags_wrong_id(monkeypatch):
    monkeypatch.setattr(config, "IG_USER_ID", "111")
    monkeypatch.setattr(config, "IG_TOKEN", "t")
    monkeypatch.setattr(config, "FB_PAGE_ID", "p")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "t")

    class R:
        status_code = 200
        text = "{}"

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(url, **kw):
        if url.endswith("/111"):
            return R({"id": "111"})  # no username: this is the Page, not Instagram
        return R({"instagram_business_account": {"id": "999", "username": "indiainshort.daily"}})

    monkeypatch.setattr(meta.requests, "get", fake_get)
    msg = meta._check_instagram()
    assert "IG_USER_ID is wrong" in msg and "999" in msg and "indiainshort.daily" in msg


def test_spotlight_lane_picks_undercovered_story_and_holds_for_review(env):
    db = FakeDB()
    spot = add_candidate(db, "Mining runoff poisons wetlands near Majuli")
    add_candidate(db, "Mainstream heatwave update", kind="news", category="Weather & Disaster",
                  sources_=[{"name": "The Hindu", "url": "u", "kind": "news"}], interest=9)
    plan = pipeline.plan_slot(db)
    picked_ids = {p["id"]: p.get("spotlight") for p in plan["picked"]}
    assert picked_ids.get(spot["id"]) is True
    assert any("Spotlight" in n for n in plan["notes"])
    out = pipeline.process_candidate(db, next(p for p in plan["picked"] if p["id"] == spot["id"]))
    row = db.get_article(spot["id"])
    assert out == "sent_for_review" and row["draft"]["spotlight"] is True
    assert "spotlight" in (row["hold_reason"] or "")


def test_spotlight_requires_no_mainstream_coverage_and_waiting_time():
    now = datetime.now(timezone.utc)
    db = FakeDB()
    covered = db.get_article(add_candidate(db, "Covered", sources_=[
        {"name": "Mongabay", "url": "u", "kind": "niche"}, {"name": "The Hindu", "url": "u2", "kind": "news"}])["id"])
    fresh = db.get_article(add_candidate(db, "Fresh", hours_seen=0.2)["id"])
    weak = db.get_article(add_candidate(db, "Weak", sig=4, imp=4)["id"])
    good = db.get_article(add_candidate(db, "Good")["id"])
    assert not pipeline.is_spotlight_candidate(covered, now)
    assert not pipeline.is_spotlight_candidate(fresh, now)
    assert not pipeline.is_spotlight_candidate(weak, now)
    assert pipeline.is_spotlight_candidate(good, now)


def test_spotlight_daily_cap(env):
    db = FakeDB()
    add_candidate(db, "Story one")
    done = add_candidate(db, "Story two", category="Health")
    db.update_article(done["id"], {"status": "published", "posted_at": datetime.now(timezone.utc).isoformat(),
                                   "draft": {"spotlight": True}})
    plan = pipeline.plan_slot(db)
    assert not any(p.get("spotlight") for p in plan["picked"])


def test_govt_spotlight_not_held_when_auto(env):
    db = FakeDB()
    db.set_setting("mode", "auto")
    row = add_candidate(db, "RBI notifies new rules for small lenders", kind="govt", category="Economy", state="National")
    art = {**db.get_article(row["id"]), "spotlight": True}
    assert pipeline.process_candidate(db, art) == "published"


def test_retry_only_failed_platforms(env, monkeypatch):
    db = FakeDB()
    db.set_setting("mode", "auto")
    calls = {"n": 0}

    def flaky(c, u, alt=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("code=190 sub=459 security check")
        return "i-1"

    monkeypatch.setattr(meta, "post_instagram", flaky)
    row = add_candidate(db, "Floods in Assam", kind="news", category="Weather & Disaster",
                        sources_=[{"name": "The Hindu", "url": "u", "kind": "news"},
                                  {"name": "NDTV", "url": "u2", "kind": "news"}])
    pipeline.process_candidate(db, db.get_article(row["id"]))
    stored = db.get_article(row["id"])
    assert stored["results"]["instagram"]["ok"] is False and "hint" in stored["results"]["instagram"]
    env["posts"].clear()
    assert pipeline.retry_failed(db, stored) == "published"
    assert env["posts"] == []  # threads/facebook were not posted again
    assert db.get_article(row["id"])["results"]["instagram"]["ok"] is True


def test_run_slot_explains_empty_result(env):
    db = FakeDB()
    plan = pipeline.plan_slot(db)
    assert plan["picked"] == [] and any("No scored candidates" in n for n in plan["notes"])


def test_sources_list_has_niche_and_fallbacks(monkeypatch):
    monkeypatch.setattr(config, "USE_GOOGLE_NEWS", True)
    feeds = sources.all_feeds()
    assert any(f.kind == "niche" for f in feeds)
    assert sources.fallback_feed(next(f for f in feeds if f.site)) is not None


def test_poll_loop_handles_taps_and_saves_offset(env, monkeypatch):
    from newsbot import poll
    db = FakeDB()
    updates = [[{"update_id": 5, "message": {"chat": {"id": 42}, "text": "/status"}}], []]
    monkeypatch.setattr(telegram, "get_updates", lambda off: updates.pop(0) if updates else [])
    monkeypatch.setattr(pipeline, "handle_message", lambda d, m: "handled")
    log = runlog.start("poll", "t", quiet_unless_activity=True)
    assert poll.handle_updates(db, log, 0) == 6
    assert db.get_setting("tg_offset") == "6" and log.activity
