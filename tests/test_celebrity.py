"""Celebrity fast-track: detection, lower bar, immediate posting, duplicate check, source rules, name in post."""
from datetime import datetime, timedelta, timezone

from newsbot import ai, celebrities, config, hashtags, pipeline, safety, sources
from tests.fakes import FakeDB
from tests.test_pipeline import entry, env  # noqa: F401  (env is a fixture)


def ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def star_row(db, title, kind="news", corro=1, category="Entertainment", combined=6.0, name=None, snippet="Officials said on Monday.",
             status="candidate", url=None):
    return db.insert_article({
        "url": url or "https://x/" + title.replace(" ", "-"), "title": title, "title_norm": title.lower(),
        "snippet": snippet, "source": "NDTV", "source_kind": kind,
        "sources": [{"name": "NDTV", "url": "u", "kind": kind}] * corro, "corroboration": corro,
        "category": category, "state": "National", "significance": 5, "impact": 4, "interest": 8,
        "combined": combined, "published_at": ago(1), "created_at": ago(1), "status": status,
        "draft": {"celebrity": name} if name else None})


def draft_for(name):
    def fake(a, body, developing, **kw):
        return {"threads": f"{name} said on Monday that the film will release soon.",
                "facebook": f"{name} confirmed on Monday that the film will release soon.",
                "instagram": f"{name} has confirmed the film release, officials said on Monday.",
                "card_headline": f"{name} confirms film release", "hashtags": ["Cinema"]}
    return fake


def test_roster_matching_and_helpers():
    assert celebrities.match("Shah Rukh Khan launches new film") == "Shah Rukh Khan"
    assert celebrities.match("Kohli hits century in Pune") == "Virat Kohli"
    assert celebrities.match("SRK fans gather outside Mannat") == "Shah Rukh Khan"
    assert celebrities.match("Floods hit Assam") == ""
    assert celebrities.match("Prime Minister inaugurates highway") == ""   # politicians are not on the list
    assert celebrities.name_in_text("MS Dhoni", "Dhoni retires from IPL")
    assert not celebrities.name_in_text("Virat Kohli", "A batter hit a century")
    assert celebrities.is_death_story("Veteran actor passes away at 80")
    assert celebrities.is_rumour("Actor reportedly dating co-star")


def test_celebrity_story_gets_lower_bar_and_bonus(env, monkeypatch):
    db = FakeDB()
    pipeline.ingest(db, [entry("Salman Khan teases new film", "https://a/1", "NDTV"),
                         entry("Local fair opens in Pune", "https://a/2", "NDTV")])

    def score(arts):
        return {a["id"]: {"is_india": True, "is_opinion": False, "is_promotional": False, "is_factcheck": False,
                          "category": "Entertainment", "state": "National", "significance": 3, "impact": 3,
                          "interest": 6, "combined": 4.2, "sensitive": False, "sensitive_reason": "", "celebrity": ""}
                for a in arts}
    monkeypatch.setattr(ai, "score_batch", score)
    out = pipeline.score_pending(db)
    rows = {r["title"]: r for r in db.rows.values()}
    star, other = rows["Salman Khan teases new film"], rows["Local fair opens in Pune"]
    assert star["status"] == "candidate" and star["combined"] == 5.0 and star["draft"] == {"celebrity": "Salman Khan"}
    assert other["status"] == "discarded"
    assert out["celebrity"] == 1


def test_ai_detected_name_is_kept(env, monkeypatch):
    db = FakeDB()
    pipeline.ingest(db, [entry("Singer Ankit Roy announces world tour", "https://a/1", "NDTV")])

    def score(arts):
        return {a["id"]: {"is_india": True, "is_opinion": False, "is_promotional": False, "is_factcheck": False,
                          "category": "Entertainment", "state": "National", "significance": 3, "impact": 3,
                          "interest": 6, "combined": 4.0, "sensitive": False, "sensitive_reason": "",
                          "celebrity": "Ankit Roy"} for a in arts}
    monkeypatch.setattr(ai, "score_batch", score)
    pipeline.score_pending(db)
    row = next(iter(db.rows.values()))
    assert row["status"] == "candidate" and pipeline.celeb_name(row) == "Ankit Roy"


def test_fast_track_posts_immediately_with_name_and_tag(env, monkeypatch):
    db = FakeDB()
    row = star_row(db, "Shah Rukh Khan confirms new film release", name="Shah Rukh Khan")
    monkeypatch.setattr(ai, "write_post", draft_for("Shah Rukh Khan"))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Shah Rukh Khan confirmed the film release on Monday, officials said.")
    out = pipeline.celebrity_pass(db)
    assert out == ["published"]
    saved = db.get_article(row["id"])
    assert saved["status"] == "published"
    assert saved["draft"]["fast_track"] and saved["draft"]["celebrity"] == "Shah Rukh Khan"
    assert {"threads", "facebook", "instagram"} <= set(env["posts"])
    tags = hashtags.for_platforms(saved["draft"], saved)
    assert tags["threads"] == ["#ShahRukhKhan"] and tags["instagram"][0] == "#ShahRukhKhan"


def media_text():
    from newsbot import media
    return media


def test_duplicate_in_database_is_skipped(env, monkeypatch):
    db = FakeDB()
    star_row(db, "Shah Rukh Khan confirms new film release", name="Shah Rukh Khan", status="published")
    dup = star_row(db, "Shah Rukh Khan confirms new film release date", name="Shah Rukh Khan", url="https://x/other")
    monkeypatch.setattr(ai, "write_post", draft_for("Shah Rukh Khan"))
    assert pipeline.celebrity_pass(db) == []
    assert db.get_article(dup["id"])["status"] == "discarded"
    assert db.get_article(dup["id"])["hold_reason"].startswith("duplicate of")


def test_single_source_rules(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(ai, "write_post", draft_for("Salman Khan"))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Salman Khan confirmed the film release on Monday, officials said.")
    # unconfirmed death report from a regional/specialist source waits
    sad = star_row(db, "Salman Khan passes away says report", kind="niche", name="Salman Khan")
    # rumours wait even on a mainstream outlet
    gossip = star_row(db, "Salman Khan reportedly dating new co-star", name="Salman Khan", url="https://x/g")
    assert not pipeline.celeb_single_source_ok(db.get_article(sad["id"]))
    assert not pipeline.celeb_single_source_ok(db.get_article(gossip["id"]))
    assert pipeline.celebrity_pass(db) == []
    assert db.get_article(sad["id"])["status"] == "candidate"
    # one mainstream outlet is enough for a death report; two sources fix a rumour
    assert pipeline.celeb_single_source_ok({"title": "Actor dies at 80", "snippet": "", "source_kind": "news", "corroboration": 1})
    assert pipeline.celeb_single_source_ok({"title": "Actor reportedly dating", "snippet": "", "source_kind": "news", "corroboration": 2})
    assert pipeline.celeb_single_source_ok({"title": "Actor launches film", "snippet": "", "source_kind": "niche", "corroboration": 1})


def test_name_must_appear_in_post(env, monkeypatch):
    db = FakeDB()
    row = star_row(db, "Virat Kohli announces new academy", name="Virat Kohli")
    calls = []

    def drafts(a, body, developing, **kw):
        calls.append(kw)
        good = len(calls) > 1
        who = "Virat Kohli" if good else "The batter"
        return {"threads": f"{who} announced a new academy on Monday.", "facebook": f"{who} announced a new academy on Monday.",
                "instagram": f"{who} announced a new academy on Monday.", "card_headline": f"{who} announces academy",
                "hashtags": ["Cricket"]}
    monkeypatch.setattr(ai, "write_post", drafts)
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Virat Kohli announced a new academy on Monday.")
    assert pipeline.celebrity_pass(db) == ["published"] and len(calls) == 2 and calls[0]["celebrity"] == "Virat Kohli"
    # a draft that never names the person is not posted
    db2 = FakeDB()
    r2 = star_row(db2, "MS Dhoni opens cricket academy", name="MS Dhoni")
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: {
        "threads": "A player opened an academy on Monday.", "facebook": "A player opened an academy on Monday.",
        "instagram": "A player opened an academy on Monday.", "card_headline": "Academy opens", "hashtags": []})
    assert pipeline.celebrity_pass(db2) == ["skipped_safety"]


def test_legal_story_single_trusted_source_goes_but_sexual_offence_needs_two(env, monkeypatch):
    db = FakeDB()
    case = star_row(db, "Police register FIR against Ranveer Singh over ad", category="Crime & Law", name="Ranveer Singh")
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: {
        "threads": "Police registered an FIR against Ranveer Singh on Monday, an allegation he has not yet responded to.",
        "facebook": "Police registered an FIR against Ranveer Singh on Monday. It is an allegation.",
        "instagram": "Police registered an FIR against Ranveer Singh on Monday. It is an allegation.",
        "card_headline": "FIR registered against Ranveer Singh", "hashtags": ["FIR"]})
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Police registered an FIR against Ranveer Singh on Monday.")
    assert pipeline.celebrity_pass(db) == ["published"]
    assert db.get_article(case["id"])["draft"]["careful"] is True
    assert hashtags.for_platforms(db.get_article(case["id"])["draft"], db.get_article(case["id"]))["threads"] != ["#RanveerSingh"]
    db2 = FakeDB()
    bad = star_row(db2, "Actor Kartik Aaryan named in rape case", category="Crime & Law", name="Kartik Aaryan")
    assert pipeline.celebrity_pass(db2) == ["skipped_safety"]
    assert db2.get_article(bad["id"])["status"] == "discarded"


def test_named_accused_allowed_only_for_the_celebrity():
    d = {"threads": "Police arrested Salman Khan on Monday, an allegation he denies.", "careful_reasons": []}
    assert safety.careful_issues(d, "facts", allow_name="Salman Khan") == []
    d2 = {"threads": "Police arrested Rahul Verma on Monday.", "careful_reasons": []}
    assert safety.careful_issues(d2, "facts", allow_name="Salman Khan")


def test_caps_do_not_crowd_out_regular_posts(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(config, "CELEBRITY_PER_DAY", 2)
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: draft_for(kw["celebrity"])(a, b, d))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Aamir Khan Ranbir Kapoor Akshay Kumar confirmed the film release on Monday, officials said.")
    for i, who in enumerate(["Aamir Khan", "Ranbir Kapoor", "Akshay Kumar"]):
        star_row(db, f"{who} film update about {['Goa shoot', 'Delhi premiere', 'Paris trailer'][i]}", name=who, url=f"https://x/{i}")
    out = pipeline.celebrity_pass(db)
    assert out.count("published") == 2           # own daily cap
    posted = db.list_posted_since(ago(24))
    assert all(p["draft"]["fast_track"] for p in posted)
    plan = pipeline.plan_slot(db)
    assert "+2 celebrity fast-track" in plan["notes"][0] and "0/8" in plan["notes"][0]


def test_followups_get_story_only_then_are_skipped(env, monkeypatch):
    db = FakeDB()
    stories_sent = []
    monkeypatch.setattr(pipeline, "post_stories", lambda a, url, *rest: stories_sent.append(a["id"]))
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: draft_for("Tiger Shroff")(a, b, d))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Tiger Shroff confirmed the film release on Monday, officials said.")
    monkeypatch.setattr(config, "STORIES", True)
    monkeypatch.setattr(pipeline.cards, "render_story", lambda *a, **k: b"img")
    first = star_row(db, "Tiger Shroff car shooting: two held in Kota", name="Tiger Shroff", url="https://x/1")
    pipeline.celebrity_pass(db)
    assert db.get_article(first["id"])["status"] == "published"
    posts_after_first = len(env["posts"])
    # three more developments the same day: two become Stories (quota 2), the third is skipped
    ids = [star_row(db, t, name="Tiger Shroff", url=f"https://x/f{i}")["id"] for i, t in enumerate(
        ["Tiger Shroff shooter identity still unknown, family denies link", "Tiger Shroff case: guns brought from UP to Mumbai",
         "Tiger Shroff case: police question four women"])]
    out = pipeline.celebrity_pass(db)
    assert sorted(out) == ["skipped_followup", "story_only", "story_only"]
    assert len(env["posts"]) == posts_after_first            # no new feed posts
    assert len(stories_sent) == 3                              # the first post's Story + two follow-up Stories
    assert sum(1 for i in ids if db.get_article(i)["draft"] and db.get_article(i)["draft"].get("story_only")) == 2
    assert pipeline.stories(db.list_posted_since(ago(24))) and len(pipeline.stories(db.list_posted_since(ago(24)))) == 1


def test_critical_update_gets_one_more_post(env, monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: draft_for("Dharmendra")(a, b, d))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Dharmendra confirmed the film release on Monday, officials said.")
    first = star_row(db, "Dharmendra to attend film festival", name="Dharmendra", url="https://x/1")
    pipeline.celebrity_pass(db)
    crit = star_row(db, "Dharmendra passes away at 89, family confirms", name="Dharmendra", url="https://x/2")
    assert pipeline.celeb_followup(db, db.get_article(crit["id"]), "Dharmendra") is False
    assert pipeline.celebrity_pass(db) == ["published"]
    again = star_row(db, "Dharmendra funeral held in Mumbai, last rites", name="Dharmendra", url="https://x/3")
    assert pipeline.celeb_followup(db, db.get_article(again["id"]), "Dharmendra") is True


def test_non_english_headlines_are_dropped(env):
    assert safety.is_english("Tiger Shroff case: police question four women")
    assert not safety.is_english("टाइगर श्रॉफ केस: कानपुर से आए आरोपी")
    assert safety.is_junk("टाइगर श्रॉफ केस पुलिस ने पूछताछ की")


def test_facebook_comment_permission_error_pauses_retries(env, monkeypatch):
    db = FakeDB()
    calls = []

    def boom(pid, text):
        calls.append(pid)
        raise RuntimeError("facebook comment: HTTP 403 code=200 (#200) You do not have sufficient permissions")
    monkeypatch.setattr(pipeline.meta, "comment_facebook", boom)
    for i in range(3):
        row = star_row(db, f"Floods hit region {i} badly", category="Weather & Disaster", url=f"https://x/p{i}")
        db.update_article(row["id"], {"draft": {"threads": "t", "facebook": "f", "instagram": "i", "link": f"https://pub.example/{i}",
                                                "card_headline": "h", "hashtags": ["Floods"]}, "card_url": "https://cards/x.jpg",
                                      "status": "approved"})
        pipeline.publish_article(db, db.get_article(row["id"]))
    assert len(calls) == 1 and pipeline.fb_comment_paused(db)


def test_switch_off_and_feeds(env, monkeypatch):
    db = FakeDB()
    star_row(db, "Salman Khan teases film", name="Salman Khan")
    monkeypatch.setattr(config, "CELEBRITY_FAST", False)
    assert pipeline.celebrity_pass(db) == []
    feeds = sources.celebrity_feeds(use_google_news=True)
    assert len(feeds) >= 10 and any("Entertainment" in f.name for f in feeds)
    assert any("Names" in f.name for f in feeds)
    assert len(sources.celebrity_feeds(use_google_news=False)) == len(sources.CELEBRITY_DIRECT)


def urgent_row(db, title, sig=9.2, kind="news", corro=1, url=None):
    r = star_row(db, title, kind=kind, corro=corro, category="Weather & Disaster", combined=7.0, url=url)
    db.update_article(r["id"], {"significance": sig})
    return db.get_article(r["id"])


def test_urgent_rules():
    assert pipeline.is_urgent({"significance": 9.2, "source_kind": "news", "title": "Major fire", "corroboration": 1})
    assert pipeline.is_urgent({"significance": 8.2, "source_kind": "govt", "title": "Lockdown imposed in district"})
    assert not pipeline.is_urgent({"significance": 8.2, "source_kind": "news", "title": "Minister visits plant"})
    assert not pipeline.is_urgent({"significance": 9.5, "source_kind": "state_gn", "title": "Big event", "corroboration": 1})
    assert pipeline.is_urgent({"significance": 9.5, "source_kind": "state_gn", "title": "Big event", "corroboration": 2})


def test_urgent_news_posts_on_single_trusted_source(env, monkeypatch):
    db = FakeDB()
    row = urgent_row(db, "Curfew imposed in Imphal after violence")
    monkeypatch.setattr(ai, "write_post", lambda a, b, d, **kw: {
        "threads": "A curfew was imposed in Imphal on Monday, officials said.", "facebook": "A curfew was imposed in Imphal on Monday, officials said.",
        "instagram": "A curfew was imposed in Imphal on Monday, officials said.", "card_headline": "Curfew imposed in Imphal",
        "hashtags": ["Imphal"]})
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "A curfew was imposed in Imphal on Monday, officials said.")
    out = pipeline.breaking_pass(db)
    assert out and db.get_article(row["id"])["breaking"] is True
    assert sources.watch_feeds(use_google_news=True)[-1].name in {f.name for f in sources.NATIONAL_FEEDS}


def _approved(db, n):
    row = star_row(db, f"Floods hit district {n} badly", category="Weather & Disaster", url=f"https://x/ig{n}")
    db.update_article(row["id"], {"draft": {"threads": "t", "facebook": "f", "instagram": "i", "card_headline": "h",
                                            "hashtags": ["Floods"]}, "card_url": "https://cards/x.jpg", "status": "approved"})
    return db.get_article(row["id"])


def test_instagram_rate_limit_cooldown_and_auto_retry(env, monkeypatch):
    from newsbot import meta, runlog
    db = FakeDB()
    calls = []
    limit_msg = "instagram publish: HTTP 403 code=4 sub=2207051 Application request limit reached"

    def ig(caption, url, alt=""):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError(limit_msg)
        return "i-ok"
    monkeypatch.setattr(meta, "post_instagram", ig)
    log = runlog.start("publish", "test")
    a1 = _approved(db, 1)
    assert pipeline.publish_article(db, a1) == "published"
    saved = db.get_article(a1["id"])
    assert saved["results"]["instagram"]["rate_limited"] and saved["results"]["instagram"]["tries"] == 1
    assert pipeline.ig_cooldown_active(db)
    assert not log.errors and any("will retry automatically" in w for w in log.warnings)   # a warning, not an error
    # a second post during the cool-down does not call Instagram at all
    a2 = _approved(db, 2)
    pipeline.publish_article(db, a2)
    assert len(calls) == 1 and db.get_article(a2["id"])["results"]["instagram"]["rate_limited"]
    # auto retry waits for the cool-down, then retries both rate-limited posts
    assert pipeline.retry_instagram_rate_limited(db) == []
    db.set_setting("ig_cooldown_until", ago(1))
    assert pipeline.retry_instagram_rate_limited(db) == ["published", "published"]
    assert db.get_article(a1["id"])["results"]["instagram"]["ok"] and db.get_article(a2["id"])["results"]["instagram"]["ok"]
    assert pipeline.retry_instagram_rate_limited(db) == []     # nothing left to retry


def test_rate_limit_detection():
    from newsbot import meta
    assert meta.is_rate_limited("instagram publish: HTTP 403 code=4 sub=2207051 Application request limit reached")
    assert meta.is_rate_limited("x code=613 calls to this api have exceeded the rate limit")
    assert not meta.is_rate_limited("instagram publish: HTTP 400 code=190 token expired")
    assert "retries" in meta.explain_error("code=4 sub=2207051 Application request limit reached")


def test_photos_must_be_indian():
    from newsbot import photos
    assert photos.india_ok("Delhi High Court building, New Delhi")
    assert photos.india_ok("monsoon rain street Kochi Kerala")
    assert not photos.india_ok("Empire State Building, New York skyline")
    assert not photos.india_ok("skyscraper dusk city")              # no sign it is India
    assert not photos.india_ok("Taj Mahal replica in Dubai")       # names a foreign place
    assert photos.with_india("Supreme Court building") == "Supreme Court building India"
    assert photos.with_india("court building", "Kerala") == "court building Kerala"
    assert photos.with_india("Delhi High Court building", "Delhi") == "Delhi High Court building"


def test_pixabay_and_pexels_reject_foreign_pictures(monkeypatch):
    from newsbot import photos
    monkeypatch.setattr(photos.config, "PIXABAY_API_KEY", "k")
    monkeypatch.setattr(photos.config, "PEXELS_API_KEY", "k")

    class R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    foreign = {"hits": [{"tags": "skyscraper, dusk, city", "largeImageURL": "u1", "user": "a",
                         "pageURL": "https://pixabay.com/photos/new-york-skyline-1/"}],
               "photos": [{"alt": "Tall building at dusk", "width": 3000, "url": "https://pexels.com/photo/empire-state-building-9/",
                           "src": {"large2x": "p1"}, "photographer": "x"}]}
    monkeypatch.setattr(photos.requests, "get", lambda *a, **k: R(foreign))
    assert photos.pixabay("court building India") is None and photos.pexels("court building India") is None
    indian = {"hits": [{"tags": "high court, delhi, building", "largeImageURL": "u2", "user": "b", "pageURL": "https://pixabay.com/photos/delhi-court-2/"}],
              "photos": [{"alt": "Delhi High Court building", "width": 3000, "url": "https://pexels.com/photo/delhi-court-3/",
                          "src": {"large2x": "p2"}, "photographer": "y"}]}
    monkeypatch.setattr(photos.requests, "get", lambda *a, **k: R(indian))
    assert photos.pixabay("Delhi High Court")["url"] == "u2" and photos.pexels("Delhi High Court")["url"] == "p2"


def test_threads_digest_text_uses_whole_headlines():
    from newsbot import digest
    heads = [f"Headline number {i} about something that happened in a state and what officials did next" for i in range(1, 6)]
    text = digest._fit_threads("Today in short: 5 stories. Swipe for each one.", heads, "Sources are on our Facebook page.\n\n#IndiaNews")
    assert len(text) <= 490 and "…" not in text and "..." not in text
    listed = [h for h in heads if h in text]
    assert listed == heads[:len(listed)] and 0 < len(listed) < 5
    assert f"+{5 - len(listed)} more in the slides" in text
    short = digest._fit_threads("Today in short: 2 stories.", ["Short one", "Short two"], "Sources.")
    assert "more in the slides" not in short and "1. Short one" in short and "2. Short two" in short


def test_threads_carousel_falls_back_to_cover_image(env, monkeypatch):
    from newsbot import meta, runlog
    db = FakeDB()
    log = runlog.start("digest", "t")
    monkeypatch.setattr(meta, "post_threads_carousel", lambda t, urls, k: (_ for _ in ()).throw(RuntimeError("carousel rejected")))
    row = db.insert_article({"url": "digest:x", "title": "Today in short", "title_norm": "digest", "source_kind": "digest",
                             "status": "approved", "sources": [], "card_url": "https://c/0.jpg",
                             "draft": {"threads": "t", "facebook": "f", "instagram": "i", "card_headline": "d",
                                       "slides": ["https://c/0.jpg", "https://c/1.jpg"], "alts": ["a", "b"]}})
    assert pipeline.publish_article(db, row) == "published"
    assert "threads" in env["posts"] and any("carousel not accepted" in w for w in log.warnings)


def test_error_but_post_is_live_is_not_posted_twice(env, monkeypatch):
    from newsbot import meta
    db = FakeDB()
    posted = []

    def ig(caption, url, alt=""):
        posted.append(url)
        raise RuntimeError("instagram publish: HTTP 403 code=4 sub=2207051 Application request limit reached")
    monkeypatch.setattr(meta, "post_instagram", ig)
    monkeypatch.setattr(meta, "find_recent_instagram_post", lambda caption, hours=6: "live-123")
    a = _approved(db, 1)
    assert pipeline.publish_article(db, a) == "published"
    r = db.get_article(a["id"])["results"]["instagram"]
    assert r["ok"] and r["id"] == "live-123" and len(posted) == 1       # counted as posted, no duplicate
    # a later retry of a post that failed earlier also looks first
    b = _approved(db, 2)
    db.update_article(b["id"], {"results": {"instagram": {"ok": False, "error": "x", "rate_limited": True, "tries": 1}},
                                "status": "published", "posted_at": ago(0.1)})
    db.set_setting("ig_cooldown_until", ago(1))
    assert pipeline.retry_instagram_rate_limited(db) == ["published"]
    assert len(posted) == 1 and db.get_article(b["id"])["results"]["instagram"]["id"] == "live-123"


def test_instagram_story_daily_limit(env, monkeypatch):
    from newsbot import meta
    db = FakeDB()
    sent = []
    monkeypatch.setattr(config, "IG_STORIES_PER_DAY", 3)
    monkeypatch.setattr(config, "enabled_platforms", lambda: ["instagram", "facebook"])
    monkeypatch.setattr(meta, "post_instagram_story", lambda url: sent.append(("ig", url)) or "s")
    monkeypatch.setattr(meta, "post_facebook_story", lambda url: sent.append(("fb", url)) or "s")
    for i in range(5):
        pipeline.post_stories({"id": i}, f"https://s/{i}.jpg", db)
    assert sum(1 for k, _ in sent if k == "ig") == 3 and sum(1 for k, _ in sent if k == "fb") == 5
