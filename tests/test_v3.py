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


def test_spotlight_lane_picks_undercovered_story_and_posts_it_attributed(env, monkeypatch):
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
    assert out == "published" and row["draft"]["spotlight"] is True
    monkeypatch.setattr(config, "SPOTLIGHT_AUTO", False)
    row2 = add_candidate(db, "Another specialist story", category="Health")
    art = {**db.get_article(row2["id"]), "spotlight": True}
    assert pipeline.process_candidate(db, art) == "skipped_safety"  # strict setting: single specialist source is not posted


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
    db.set_setting("mode2", "auto")
    row = add_candidate(db, "RBI notifies new rules for small lenders", kind="govt", category="Economy", state="National")
    art = {**db.get_article(row["id"]), "spotlight": True}
    assert pipeline.process_candidate(db, art) == "published"


def test_retry_only_failed_platforms(env, monkeypatch):
    db = FakeDB()
    db.set_setting("mode2", "auto")
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


def test_old_url_already_stored_does_not_crash_ingest(env):
    db = FakeDB()
    db.insert_article({"url": "https://a/old", "title": "Old", "title_norm": "old", "status": "discarded",
                       "sources": [], "created_at": hours_ago(200)})
    db.recent_articles = lambda hours=72: []  # the story is outside the 72h look-back window
    stats = pipeline.ingest(db, [entry("Flood hits Assam, thousands evacuated", "https://a/old", "The Hindu")])
    assert stats["new"] == 0 and stats["dup"] == 1


# ---- photos -----------------------------------------------------------------------------
from newsbot import cards, photos  # noqa: E402


def _cand(**kw):
    base = {"title": "File:Majuli sunset.jpg", "url": "https://upload/x.jpg", "width": 1400, "mime": "image/jpeg",
            "license": "CC BY 4.0", "artist": "A. Photographer", "nonfree": False, "restrictions": "",
            "text": "Majuli island at sunset", "page": "https://commons/x"}
    base.update(kw)
    return base


def test_license_rules():
    for ok in ("CC0", "CC BY 4.0", "CC BY 2.0", "Public domain", "CC BY 3.0 in"):
        assert photos.license_ok(ok), ok
    for bad in ("CC BY-SA 4.0", "CC BY-NC 4.0", "CC BY-ND 2.0", "GFDL", "", "Attribution"):
        assert not photos.license_ok(bad), bad


def test_pick_commons_rejects_risky_files():
    risky = [_cand(license="CC BY-SA 4.0"), _cand(nonfree=True), _cand(restrictions="personality"),
             _cand(width=600), _cand(mime="image/gif"), _cand(text="Portrait of the minister"),
             _cand(text="Crowd at a rally")]
    assert photos.pick_commons(risky) is None
    good = photos.pick_commons(risky + [_cand(artist="B. Author")])
    assert good and "B. Author" in good["credit"] and "CC BY 4.0" in good["credit"] and good["source"] == "commons"


def test_find_photo_refuses_people_and_survives_errors(monkeypatch):
    monkeypatch.setattr(config, "USE_PHOTOS", True)
    assert photos.find_photo("Prime Minister speech") is None  # people are never searched for
    assert photos.find_photo("") is None

    def boom(q, limit=12):
        raise RuntimeError("network down")

    monkeypatch.setattr(photos, "commons_candidates", boom)
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    assert photos.find_photo("Majuli island") is None  # never raises


def test_card_with_photo_and_credit_renders():
    from PIL import Image
    import io
    photo = Image.new("RGB", (1600, 900), (40, 120, 60))
    jpg = cards.render_card("Majuli wetlands shrink as mining runoff spreads", "Environment", "Mongabay India",
                            "Assam", photo=photo, photo_credit="A. Photographer / Wikimedia Commons, CC BY 4.0")
    img = Image.open(io.BytesIO(jpg))
    assert img.size == (1080, 1350)


def test_credit_goes_into_every_caption_and_photo_is_posted(env, monkeypatch):
    from PIL import Image
    db = FakeDB()
    seen = {}
    monkeypatch.setattr(photos, "find_photo", lambda q: {"image": Image.new("RGB", (1400, 900), (10, 90, 40)),
                                                          "credit": "A. Photographer / Wikimedia Commons, CC BY 4.0",
                                                          "source": "commons"})
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None, image_url=None: seen.update(fb=(t, image_url)) or "f-1")
    monkeypatch.setattr(meta, "post_threads", lambda t, l, k, image_url=None: seen.update(th=(t, image_url)) or "t-1")
    row = add_candidate(db, "Floods in Assam", kind="news", category="Weather & Disaster",
                        sources_=[{"name": "The Hindu", "url": "u", "kind": "news"},
                                  {"name": "NDTV", "url": "u2", "kind": "news"}])
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "published"
    assert "A. Photographer" in seen["fb"][0] and seen["fb"][1].startswith("https://cards/")
    assert seen["th"][1].startswith("https://cards/")
    assert db.get_article(row["id"])["draft"]["photo_source"] == "commons"


def test_post_without_photo_still_carries_the_card_image(env, monkeypatch):
    db = FakeDB()
    seen = {}
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None, image_url=None: seen.update(fb=image_url) or "f-1")
    row = add_candidate(db, "Floods in Assam", kind="news", category="Weather & Disaster",
                        sources_=[{"name": "The Hindu", "url": "u", "kind": "news"},
                                  {"name": "NDTV", "url": "u2", "kind": "news"}])
    pipeline.process_candidate(db, db.get_article(row["id"]))
    assert seen["fb"] and seen["fb"].startswith("https://cards/")


def test_fallback_photo_query():
    assert "Assam" in pipeline.fallback_photo_query({"state": "Assam", "category": "Health"})
    assert pipeline.fallback_photo_query({"state": "National", "category": "Courts"}).startswith("Supreme Court")
    assert pipeline.fallback_photo_query({"state": "National", "category": "Crime & Law"}) == ""


def test_slot_replaces_skipped_story_with_next_best(env):
    db = FakeDB()
    risky = add_candidate(db, "Rape case story one", kind="news", category="Crime & Law", state="Punjab",
                          sources_=[{"name": "The Hindu", "url": "u", "kind": "news"}])
    ok = add_candidate(db, "Health story two", kind="news", category="Health", state="Kerala",
                       sources_=[{"name": "The Hindu", "url": "u", "kind": "news"}])
    db.update_article(risky["id"], {"combined": 9.5})
    out = pipeline.run_slot(db)
    assert db.get_article(risky["id"])["status"] == "discarded"
    assert db.get_article(ok["id"])["status"] == "published"
    assert "skipped_safety" in out["outcomes"] and "published" in out["outcomes"]


# ---- tiers, careful mode, cleanup ----------------------------------------------------------
from newsbot import media, safety  # noqa: E402

TWO = [{"name": "The Hindu", "url": "u", "kind": "news"}, {"name": "NDTV", "url": "u2", "kind": "news"}]


def test_classify_tiers():
    assert safety.classify("Rape accused held in Delhi", category="Crime & Law")[0] == "careful"
    assert "sexual offence" in safety.classify("Rape accused held in Delhi", category="Crime & Law")[1]
    assert safety.classify("Communal clash in town", category="Society")[0] == "careful"
    assert safety.classify("Exit polls predict result", category="Politics")[0] == "careful"
    assert safety.classify("Man arrested for theft in Pune", category="Crime & Law")[0] == "careful"
    assert safety.classify("Blast in market kills five", category="Weather & Disaster")[0] == "careful"
    assert safety.classify("Parliament passes bill", category="Politics")[0] == "careful"
    assert safety.classify("Flood hits Assam", category="Weather & Disaster")[0] == "clear"
    assert safety.classify("Flood hits Assam", category="Weather & Disaster", ai_sensitive=True)[0] == "careful"


def test_election_silence_blocks_political_only(monkeypatch):
    monkeypatch.setattr(config, "ELECTION_SILENCE", [["2000-01-01", "2999-12-31"]])
    assert safety.classify("Parliament passes bill", category="Politics")[0] == "blocked"
    assert safety.classify("Flood hits Assam", category="Weather & Disaster")[0] == "clear"


def test_careful_issues():
    ok = {"threads": "Police said a man was arrested.", "facebook": "According to police, a man was held.",
          "instagram": "Officials said an arrest was made."}
    assert safety.careful_issues(ok, "") == []
    bad = {"threads": "The murderer was caught.", "facebook": "Police said accused Ramesh Kumar was held.",
           "instagram": "A man was arrested."}
    found = " ".join(safety.careful_issues(bad, ""))
    assert "guilt label" in found and "names an accused" in found and "no attribution" not in found
    assert safety.careful_issues({"threads": "Student died by suicide; he hanged himself.", "facebook": "x", "instagram": "x",
                                  "careful_reasons": ["suicide/self-harm"]}, "")
    assert "Tele-MANAS" in safety.HELPLINE_NOTE and "14416" in safety.HELPLINE_NOTE
    assert not any("guilt label" in i for i in safety.careful_issues(
        {"threads": "The court said the killer was sentenced.", "facebook": "Police said x", "instagram": "Police said x"},
        "He was convicted and sentenced."))


def test_careful_story_posts_with_strong_source_and_neutral_note(env, monkeypatch):
    db = FakeDB()
    seen = {}
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, careful=False, reasons=None: seen.update(careful=careful) or {
        "threads": "Police said a man was arrested in Pune after a theft complaint.",
        "facebook": "Police said a man was arrested in Pune after a theft complaint.",
        "instagram": "Police said a man was arrested in Pune after a theft complaint.",
        "card_headline": "Man arrested in Pune theft case", "hashtags": [], "photo_query": "Pune city"})
    monkeypatch.setattr(photos, "find_photo", lambda q: seen.update(photo_asked=True))
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None, image_url=None: seen.update(fb=t) or "f-1")
    row = add_candidate(db, "Man arrested in Pune theft case", kind="news", category="Crime & Law", state="Maharashtra",
                        sources_=TWO)
    db.update_article(row["id"], {"corroboration": 2, "snippet": "Police said on Monday a man was arrested in Pune."})
    monkeypatch.setattr(media, "fetch_text", lambda u: "Police said a man was arrested in Pune after a theft complaint.")
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "published"
    assert seen["careful"] is True and "photo_asked" not in seen
    assert "Allegations are not proven" in seen["fb"]
    assert db.get_article(row["id"])["draft"]["careful"] is True


def test_careful_story_single_source_is_skipped(env):
    db = FakeDB()
    row = add_candidate(db, "Man arrested for rape in Pune", kind="news", category="Crime & Law",
                        sources_=[{"name": "The Hindu", "url": "u", "kind": "news"}])
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "skipped_safety"
    assert "single source" in db.get_article(row["id"])["hold_reason"]


def test_careful_daily_cap_defers(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(config, "MAX_CAREFUL_PER_DAY", 1)
    done = add_candidate(db, "Earlier crime story", category="Crime & Law", kind="news", sources_=TWO)
    db.update_article(done["id"], {"status": "published", "posted_at": datetime.now(timezone.utc).isoformat(),
                                   "draft": {"careful": True, "careful_reasons": ["crime/legal story"]}})
    row = add_candidate(db, "Another crime story", category="Crime & Law", kind="news", sources_=TWO)
    db.update_article(row["id"], {"corroboration": 2})
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "deferred"
    assert db.get_article(row["id"])["status"] == "candidate"


def test_election_blackout_story_is_never_drafted(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(config, "ELECTION_SILENCE", [["2000-01-01", "2999-12-31"]])
    monkeypatch.setattr(ai, "write_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not draft")))
    row = add_candidate(db, "Parliament passes bill", category="Politics", kind="news", sources_=TWO)
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "skipped_safety"
    assert db.get_article(row["id"])["hold_reason"].startswith("blocked")


def test_sexual_offence_story_is_drafted_carefully_with_topic_rules(env, monkeypatch):
    db = FakeDB()
    seen = {}
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, careful=False, reasons=None: seen.update(r=reasons) or {
        "threads": "Police said a man was arrested in Delhi on a complaint of sexual assault.",
        "facebook": "Police said a man was arrested in Delhi on a complaint of sexual assault.",
        "instagram": "Police said a man was arrested in Delhi on a complaint of sexual assault.",
        "card_headline": "Arrest made in Delhi sexual assault case", "hashtags": [], "photo_query": ""})
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None, image_url=None: "f-1")
    row = add_candidate(db, "Sexual assault accused held in Delhi", category="Crime & Law", kind="news", sources_=TWO)
    db.update_article(row["id"], {"corroboration": 2, "snippet": "Police said a man was arrested in Delhi on a complaint of sexual assault."})
    monkeypatch.setattr(media, "fetch_text", lambda u: "Police said a man was arrested in Delhi on a complaint of sexual assault.")
    assert pipeline.process_candidate(db, db.get_article(row["id"])) == "published"
    assert "sexual offence" in seen["r"] and "survivor" in safety.topic_rules(seen["r"])


def test_suicide_story_gets_helpline_and_source_url_not_outlet_name():
    d = {"threads": "A student died in Kota.", "facebook": "A student died in Kota.", "instagram": "A student died in Kota.",
         "hashtags": ["Kota"], "careful": True, "careful_reasons": ["suicide/self-harm"], "short_url": "https://is.gd/abc123"}
    a = {"category": "Education", "state": "Rajasthan", "sources": [{"name": "The Hindu"}], "source": "The Hindu"}
    t = pipeline.build_texts(d, a)
    for k in ("threads", "facebook", "instagram"):
        assert "Source: https://is.gd/abc123" in t[k] and "The Hindu" not in t[k]
    assert "14416" in t["facebook"] and "14416" in t["threads"]


def test_card_image_has_no_source_ai_or_credit_lines():
    from newsbot import cards
    import inspect
    src = inspect.getsource(cards.render_card)
    assert "Read the full story" not in src and "AI-assisted" not in src and "Representative image" not in src
    assert 'f"Source: {source}"' not in src


def test_google_wrapper_urls_are_not_used_as_source_link():
    assert media.short_url("https://news.google.com/rss/articles/CBMi0AFBVV95") == ""
    assert media.clean_url("https://x.com/a?utm_source=f&id=3#top") == "https://x.com/a?id=3"


def test_undated_feed_items_are_skipped(monkeypatch):
    import types

    class FP:
        @staticmethod
        def parse(content):
            return types.SimpleNamespace(entries=[
                {"title": "Dated story from outlet", "link": "https://x/1", "summary": "", "published": "Mon, 12 Oct 2026 10:00:00 GMT"},
                {"title": "Undated story from outlet", "link": "https://x/2", "summary": ""}])

    import sys, types
    had = sys.modules.get("feedparser")
    sys.modules["feedparser"] = FP
    monkeypatch.setattr(pipeline, "_entry_time", lambda e: datetime.now(timezone.utc) if e.get("published") else None)
    try:
        items = pipeline._parse(b"", Feed("X", "u", "news"))
    finally:
        if had is None:
            del sys.modules["feedparser"]
        else:
            sys.modules["feedparser"] = had
    assert [i.url for i in items] == ["https://x/1"]


def test_cleanup_deletes_old_rows_keeps_published_and_removes_images(env, monkeypatch):
    db = FakeDB()
    base = f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_BUCKET}/"
    removed = []
    monkeypatch.setattr(media, "delete_cards", lambda names: removed.extend(names) or len(names))
    old = add_candidate(db, "Old discarded", hours_seen=100)
    db.update_article(old["id"], {"status": "discarded", "card_url": base + "a.jpg"})
    kept = add_candidate(db, "Old published", hours_seen=100)
    db.update_article(kept["id"], {"status": "published", "card_url": base + "b.jpg", "draft": {"slides": [base + "s1.jpg"]}})
    fresh = add_candidate(db, "Fresh", hours_seen=10)
    out = pipeline.cleanup_old(db)
    assert out["rows"] == 1 and sorted(removed) == ["a.jpg", "b.jpg", "s1.jpg"]
    assert old["id"] not in db.rows and kept["id"] in db.rows and fresh["id"] in db.rows
    assert db.get_article(kept["id"])["card_url"] is None


def test_retention_never_below_age_limit():
    assert config.DATA_RETENTION_HOURS >= config.ARTICLE_MAX_AGE_HOURS + 24


def test_openverse_filters_licence_and_people(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [
                {"license": "by-sa", "url": "u1", "width": 2000, "title": "Sunset"},
                {"license": "by", "url": "u2", "width": 2000, "title": "Portrait of a man"},
                {"license": "by", "url": "u3", "width": 500, "title": "Small"},
                {"license": "by", "license_version": "4.0", "url": "u4", "width": 2000, "title": "Majuli sunset",
                 "creator": "C. Creator", "provider": "flickr"}]}
    monkeypatch.setattr(photos.requests, "get", lambda *a, **k: R())
    hit = photos.openverse("Majuli")
    assert hit["url"] == "u4" and "C. Creator" in hit["credit"] and "CC BY 4.0" in hit["credit"]


# ---- tokens --------------------------------------------------------------------------------
def test_fbtoken_command_saves_permanent_token_and_deletes_message(env, monkeypatch):
    db = FakeDB()
    deleted = []
    monkeypatch.setattr(telegram, "delete_message", lambda c, m: deleted.append((c, m)))
    monkeypatch.setattr(meta, "exchange_page_token", lambda t: {
        "page_id": "P1", "page_name": "indiainshort.news", "page_token": "PERMANENT",
        "ig_id": "17841400000000000", "ig_username": "indiainshort.daily"})
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "old")
    monkeypatch.setattr(config, "IG_TOKEN", "old")
    monkeypatch.setattr(config, "IG_USER_ID", "wrong")
    msg = {"chat": {"id": 42}, "message_id": 7, "text": "/fbtoken EAAShortLived123"}
    assert pipeline.handle_message(db, msg) == "/fbtoken"
    assert deleted == [(42, 7)]
    assert db.get_setting("fb_page_token") == "PERMANENT" and db.get_setting("ig_user_id") == "17841400000000000"
    assert config.FB_PAGE_TOKEN == "PERMANENT" and config.IG_TOKEN == "PERMANENT" and config.IG_USER_ID == "17841400000000000"
    assert "EAAShortLived123" not in " ".join(env["telegram"])  # the token is never echoed back


def test_fbtoken_failure_is_reported_without_secret(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(telegram, "delete_message", lambda c, m: None)

    def boom(t):
        raise meta.PublishError("token exchange: HTTP 400 code=190 access_token=EAAsecret123 bad")

    monkeypatch.setattr(meta, "exchange_page_token", boom)
    pipeline.handle_message(db, {"chat": {"id": 42}, "message_id": 1, "text": "/fbtoken EAAsecret123"})
    assert any("Could not connect" in t for t in env["telegram"])
    assert "EAAsecret123" not in " ".join(env["telegram"])


def test_fbtoken_from_stranger_is_ignored(env):
    db = FakeDB()
    assert pipeline.handle_message(db, {"chat": {"id": 999}, "message_id": 1, "text": "/fbtoken x"}) == "ignored"


def test_threads_posts_to_me_not_to_a_possibly_wrong_id(monkeypatch):
    urls = []

    class R:
        status_code = 200
        text = "{}"
        def json(self): return {"id": "123"}

    monkeypatch.setattr(meta.requests, "post", lambda url, **k: urls.append(url) or R())
    monkeypatch.setattr(meta.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "THREADS_USER_ID", "999-wrong")
    meta.post_threads("hi", None, "tok", "https://img/x.jpg")
    assert all("/me/" in u and "999-wrong" not in u for u in urls) and len(urls) == 2


def test_token_error_hint_mentions_fbtoken():
    assert "/fbtoken" in meta.explain_error("x: HTTP 400 code=190 sub=463 Session has expired")


# ---- hashtags and platform checks -------------------------------------------------------------
from newsbot import hashtags  # noqa: E402


def test_hashtag_string_is_never_split_into_letters():
    # Regression: the AI returned "durga puja kolkata" as one string and the posts ended with "#D #U ..."
    tags = hashtags.normalize("durga puja, kolkata stampede")
    assert tags == ["DurgaPuja", "KolkataStampede"]
    assert all(len(t) > 2 for t in hashtags.normalize("durga puja kolkata"))
    assert hashtags.normalize(["x", "D", "12345", "viral", "news"]) == []


def test_every_platform_always_gets_hashtags():
    a = {"state": "National", "category": "Courts"}
    for draft in ({}, {"hashtags": None}, {"hashtags": ""}, {"hashtags": ["a"]}):
        t = hashtags.for_platforms(draft, a)
        assert len(t["threads"]) == 1 and len(t["facebook"]) == 3 and len(t["instagram"]) >= 3, (draft, t)


def test_platform_limits_and_careful_tags():
    draft = {"hashtags": ["Durga Puja", "Kolkata stampede", "crowd safety", "Puja pandal", "Kolkata Police"]}
    a = {"state": "West Bengal", "category": "Weather & Disaster"}
    t = hashtags.for_platforms(draft, a)
    assert len(t["instagram"]) == 5 and len(t["threads"]) == 1 and len(t["facebook"]) == 3
    assert "#IndiaInShort" in t["instagram"] and "#IndiaNews" in t["instagram"] and "#WestBengal" in t["instagram"]
    careful = hashtags.for_platforms({**draft, "careful": True}, a)
    assert "#IndiaInShort" not in careful["instagram"] and "#Weather" not in careful["instagram"]
    assert "#WestBengal" in careful["instagram"]


def test_inline_hashtags_are_stripped_and_tags_always_fit_on_threads():
    a = {"state": "Assam", "category": "Health", "sources": [{"name": "The Hindu"}], "source": "The Hindu"}
    draft = {"threads": "Outbreak spreads #health #viral " + "x " * 400, "facebook": "f #a", "instagram": "i #b #c",
             "hashtags": ["Nipah virus", "Kerala"]}
    t = pipeline.build_texts(draft, a)
    assert "#health" not in t["threads"] and "#viral" not in t["threads"]
    assert len(t["threads"]) <= 490 and t["threads"].strip().split("\n")[-1].startswith("#NipahVirus")
    assert "#a" not in t["facebook"].split() and "#b" not in t["instagram"].split()


def test_engagement_bait_shouting_and_emoji_checks():
    facts = "Officials said on Monday the bridge reopened."
    ok = {"threads": "Officials said the bridge reopened.", "facebook": "Officials said the bridge reopened.",
          "instagram": "Officials said the bridge reopened.", "card_headline": "Bridge reopens"}
    assert safety.check_draft(ok, facts) == []
    for bad_text, needle in (("Share this with your friends! Officials said the bridge reopened.", "engagement bait"),
                             ("Tag a friend. Officials said the bridge reopened.", "engagement bait"),
                             ("Follow us for more. Officials said the bridge reopened.", "engagement bait"),
                             ("BRIDGE REOPENED TODAY after Officials said it.", "shouting"),
                             ("Officials said the bridge reopened 🎉🎉🎉🔥", "emoji")):
        d = {**ok, "threads": bad_text}
        assert any(needle in i for i in safety.check_draft(d, facts + " BRIDGE REOPENED TODAY")), bad_text


def test_careful_stories_reject_emotive_wording():
    d = {"threads": "Police said a tragic stampede killed 19.", "facebook": "Police said 19 died.",
         "instagram": "Police said 19 died."}
    assert any("emotive" in i for i in safety.careful_issues(d, ""))


def test_story_card_is_vertical_and_clean():
    from newsbot import cards
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(cards.render_story("Test headline for the story", "Economy & Business")))
    assert img.size == (1080, 1920)


def test_stories_never_block_feed_post(env, monkeypatch):
    monkeypatch.setattr(config, "THREADS_USER_ID", "1")
    monkeypatch.setattr(config, "FB_PAGE_ID", "p"); monkeypatch.setattr(config, "FB_PAGE_TOKEN", "t")
    monkeypatch.setattr(config, "IG_USER_ID", "i"); monkeypatch.setattr(config, "IG_TOKEN", "t")
    monkeypatch.setattr(meta, "post_instagram_story", lambda u: (_ for _ in ()).throw(RuntimeError("no permission")))
    monkeypatch.setattr(meta, "post_facebook_story", lambda u: "fs-1")
    out = pipeline.post_stories({"id": 1}, "https://x/y.jpg")
    assert out["instagram"]["ok"] is False and out["facebook"]["ok"] is True


def test_person_and_religion_photos_are_rejected(monkeypatch):
    from newsbot import photos
    for tags in ("sadhu, india, holy man", "monk, prayer flags", "portrait, beard", "tourist, temple", "yogi, meditation"):
        assert photos.PERSON_WORDS.search(tags), tags
    assert not photos.PERSON_WORDS.search("india gate, delhi, monument, architecture")

    class R:
        def raise_for_status(self): pass
        def json(self): return {"hits": [{"tags": "sadhu, india", "largeImageURL": "u1", "user": "a"},
                                         {"tags": "india gate, delhi", "largeImageURL": "u2", "user": "b", "pageURL": "p"}]}
    seen = []
    monkeypatch.setattr(photos.config, "PIXABAY_API_KEY", "k")
    monkeypatch.setattr(photos.requests, "get", lambda url, **kw: seen.append(kw["params"]["category"]) or R())
    hit = photos.pixabay("india gate")
    assert hit["url"] == "u2" and seen[0] == "places" and "people" not in seen


def test_new_feeds_present_and_unique():
    from newsbot import sources
    feeds = sources.all_feeds()
    names = [f.name for f in feeds]
    assert len(names) == len(set(names))
    assert {"Factly", "Newschecker", "Vishvas News", "FactCrescendo"} <= set(names)
    assert any(f.kind == "factcheck" and f.name == "Factly" and f.site for f in feeds)
    assert any(n.startswith("Greater Kashmir") for n in names) and any("Herald Goa" in n for n in names)
    assert sum(1 for f in feeds if f.kind == "factcheck") >= 6


def test_facebook_first_comment_with_full_link_and_failure_is_not_fatal(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(config, "FB_PAGE_ID", "p"); monkeypatch.setattr(config, "FB_PAGE_TOKEN", "t")
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(meta, "post_facebook", lambda t, l=None, image_url=None: "p_1")
    monkeypatch.setattr(meta, "post_instagram", lambda *a, **k: "i-1")
    monkeypatch.setattr(meta, "post_threads", lambda *a, **k: "t-1")
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    seen = {}
    monkeypatch.setattr(meta, "comment_facebook", lambda pid, text: seen.update(pid=pid, text=text) or "c-1")
    row = add_candidate(db, "Clean story", kind="news", category="Health", state="Kerala", sources_=TWO)
    draft = {"threads": "A.", "facebook": "B.", "instagram": "C.", "card_headline": "H", "hashtags": ["Kerala"],
             "link": "https://example.com/a?utm_source=x&id=1", "short_url": "https://is.gd/abc"}
    db.update_article(row["id"], {"draft": draft, "card_url": "https://x/y.jpg", "status": "approved"})
    pipeline.publish_article(db, db.get_article(row["id"]))
    assert seen["pid"] == "p_1" and seen["text"] == "Read the full story: https://example.com/a?id=1"
    # a comment failure never fails the post
    monkeypatch.setattr(meta, "comment_facebook", lambda pid, text: (_ for _ in ()).throw(RuntimeError("(#200) permission")))
    row2 = add_candidate(db, "Clean story two", kind="news", category="Health", state="Goa", sources_=TWO)
    db.update_article(row2["id"], {"draft": draft, "card_url": "https://x/y.jpg", "status": "approved"})
    assert pipeline.publish_article(db, db.get_article(row2["id"])) == "published"
