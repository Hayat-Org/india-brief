from datetime import datetime, timezone

from newsbot import config, dedup, safety


def test_same_story_different_wording():
    assert dedup.is_same_story(
        "Delhi air quality turns 'severe' as AQI crosses 400",
        "Delhi air quality turns severe, AQI crosses 400 mark")


def test_different_cities_not_merged():
    assert not dedup.is_same_story("Fire breaks out in Mumbai building",
                                   "Fire breaks out in Pune building")


def test_different_numbers_not_merged():
    assert not dedup.is_same_story("Sensex falls 800 points on global cues",
                                   "Sensex gains 450 points on global cues")


def test_strip_source_suffix():
    assert dedup.strip_source("Floods hit Assam - The Hindu", "The Hindu") == "Floods hit Assam"
    assert dedup.strip_source("Modi - Xi meeting", "Reuters") == "Modi - Xi meeting"


def test_sensitive_detection():
    assert safety.is_sensitive("Communal clash reported in town")
    assert safety.is_sensitive("Police probe alleged gang-rape case")
    assert safety.is_sensitive("Budget session begins in Parliament") is None


def test_junk_titles():
    assert safety.is_junk("Aries horoscope today")
    assert not safety.is_junk("RBI keeps repo rate unchanged")


def test_election_silence(monkeypatch):
    monkeypatch.setattr(config, "ELECTION_SILENCE", [["2026-11-01", "2026-11-03"]])
    assert safety.in_election_silence(datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
    assert not safety.in_election_silence(datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc))


GOOD = {
    "threads": "Floods in Assam have affected 2,500 people, officials said.",
    "facebook": "Officials in Assam said 2,500 people are affected by floods.",
    "instagram": "Assam floods: officials say 2,500 people are affected.",
    "card_headline": "Assam floods affect 2,500 people",
}
FACTS = "Floods in Assam affected 2,500 people, state officials said on Monday. Assam"


def test_facts_check_passes_clean_draft():
    assert safety.check_draft(GOOD, FACTS) == []


def test_facts_check_catches_invented_number_and_name():
    bad = dict(GOOD, threads="Floods hit 9,000 people and Chief Himanta Sarma ordered relief.")
    issues = safety.check_draft(bad, FACTS)
    assert any("9000" in i for i in issues)
    assert any("Himanta" in i for i in issues)


def test_facts_check_catches_loaded_terms_and_urls():
    bad = dict(GOOD, facebook="These anti-national protesters... see https://x.co")
    issues = safety.check_draft(bad, FACTS)
    assert any("loaded" in i for i in issues)
    assert any("URL" in i for i in issues)
