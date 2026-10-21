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
    monkeypatch.setattr(ai, "write_post", draft_for("Aamir Khan"))
    monkeypatch.setattr(media_text(), "fetch_text", lambda u: "Aamir Khan confirmed the film release on Monday, officials said.")
    for i in range(3):
        star_row(db, f"Aamir Khan film update number {i} about {['Goa shoot', 'Delhi premiere', 'Paris trailer'][i]}",
                 name="Aamir Khan", url=f"https://x/{i}")
    out = pipeline.celebrity_pass(db)
    assert out.count("published") == 2           # own daily cap
    posted = db.list_posted_since(ago(24))
    assert all(p["draft"]["fast_track"] for p in posted)
    plan = pipeline.plan_slot(db)
    assert "+2 celebrity fast-track" in plan["notes"][0] and "0/8" in plan["notes"][0]


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
