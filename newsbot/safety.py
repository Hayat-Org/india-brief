"""Safety gates. Anything flagged here is HELD for human approval, never auto-posted.

Rationale: Indian law (defamation, contempt, BNS provisions on promoting enmity,
IT Rules) and Meta policy make a few topics too risky to leave fully automated.
"""
import re
from datetime import datetime, timedelta, timezone

from . import config

IST = timezone(timedelta(hours=5, minutes=30))

SENSITIVE_PATTERNS = [
    (r"\bcommunal\b|\briot(s|ers|ing)?\b|\bmob violence\b|\blynch", "communal/mob violence"),
    (r"\brape\b|\bgang-?rape\b|sexual (assault|abuse|harass)|\bmolest|\bpocso\b", "sexual offence"),
    (r"\bminor (girl|boy)\b|\bchild abuse\b|\bjuvenile\b", "minor involved"),
    (r"\bsuicide\b|\bself-?harm\b", "suicide/self-harm"),
    (r"\bterror(ist|ism)?\b|\bblast\b|\bbomb\b|\bhostage\b|\bmilitant", "terror/violence"),
    (r"\bhate speech\b|\bblasphem|\breligious conversion|\bconversion racket", "religious flashpoint"),
    (r"\bcow (vigilante|slaughter|smuggl)|\bbeef\b", "religious flashpoint"),
    (r"\b(mosque|masjid|temple|church|gurdwara)\b.{0,40}\b(clash|dispute|tension|vandal|demolish)", "religious flashpoint"),
    (r"\bcontempt of court\b|\bsub judice\b", "legal risk"),
    (r"\bencounter (killing|death)|\bcustodial death", "state violence"),
    (r"\bdeath toll\b|\bkilled in\b.{0,30}\b(attack|firing|clash)", "mass-casualty/violence"),
    (r"\bexit polls?\b|\bopinion polls?\b|\bpoll of polls\b", "election poll publication rules"),
    (r"\bmodel code of conduct\b", "election conduct"),
]
_SENSITIVE = [(re.compile(p, re.I), reason) for p, reason in SENSITIVE_PATTERNS]

# Loaded, name-calling or dehumanising terms. A neutral desk should never write these itself.
LOADED_TERMS = re.compile(
    r"\banti-?national\b|\btraitor\b|\bjihadi\b|\blibtard\b|\burban naxal\b|\bgodi media\b|"
    r"\bbhakt\b|\bpresstitute\b|\bsickular\b|\blove jihad\b|\bpaid media\b|\bgoons\b|"
    r"\bterrorist sympathi[sz]er\b|\bcockroach|\bparasite|"
    r"\bexclusive\b|won'?t tell you|don'?t want you to know|media (is )?(hiding|ignoring|silent)|"
    r"\bcover-?up\b|shocking truth|you won'?t believe",
    re.I,
)

# Phone numbers, e-mails and 12-digit ID-style numbers must never appear in a post (privacy / DPDP).
PERSONAL_DATA = re.compile(
    r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+|\b\d{4}\s\d{4}\s\d{4}\b"
)

# Capitalised words that are fine even if the source text lacks them.
ALLOWED_CAPS = {
    "India", "Indian", "Indians", "Centre", "Union", "State", "Government", "Supreme",
    "Court", "High", "Prime", "Minister", "Chief", "Police", "Delhi", "The", "This", "That",
    "According", "Here", "What", "Why", "How", "When", "Who", "Also", "But", "And", "For",
    "Not", "No", "Yes", "Meanwhile", "However", "Officials", "Reuters", "Source", "Sources",
}


def is_sensitive(text: str):
    """Return a short reason if the text touches a gated topic, else None."""
    for pattern, reason in _SENSITIVE:
        if pattern.search(text or ""):
            return reason
    return None


def in_election_silence(now: datetime = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(IST).date().isoformat()
    for start, end in config.ELECTION_SILENCE:
        if start <= now <= end:
            return True
    return False


def _numbers(text: str) -> set:
    out = set()
    for m in re.findall(r"\d[\d,]*\.?\d*", text or ""):
        out.add(m.replace(",", "").rstrip("."))
    return out


def check_draft(draft: dict, facts_text: str, now: datetime = None) -> list:
    """Return a list of problems. Empty list = draft passes the facts-only check."""
    issues = []
    now = now or datetime.now(timezone.utc)
    facts_lower = (facts_text or "").lower()
    facts_nums = _numbers(facts_text)
    years_ok = {str(now.year), str(now.year - 1)}

    for platform in ("threads", "facebook", "instagram"):
        text = (draft.get(platform) or "").strip()
        if not text:
            issues.append(f"{platform}: empty text")
            continue
        if re.search(r"https?://|www\.", text):
            issues.append(f"{platform}: contains a URL")
        if PERSONAL_DATA.search(text):
            issues.append(f"{platform}: contains personal data (phone/email/ID number)")
        if LOADED_TERMS.search(text):
            issues.append(f"{platform}: loaded/insulting term")
        for n in _numbers(text):
            if n not in facts_nums and n not in years_ok:
                issues.append(f"{platform}: number '{n}' not found in source")
        # Proper nouns: ignore the first word of each sentence
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", sentence)
            for w in words[1:]:
                if w[0].isupper() and len(w) > 2 and w not in ALLOWED_CAPS \
                        and w.lower() not in facts_lower:
                    issues.append(f"{platform}: name '{w}' not found in source")
    headline = draft.get("card_headline", "")
    for n in _numbers(headline):
        if n not in facts_nums and n not in years_ok:
            issues.append(f"headline: number '{n}' not found in source")
    # de-duplicate while keeping order
    seen, uniq = set(), []
    for i in issues:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


JUNK_TITLE = re.compile(
    r"horoscope|rashifal|zodiac|dream ?11|fantasy (team|xi|picks)|prediction today|"
    r"discount|coupon|\bdeal(s)? of the day|photos?:|in pics|viral video|"
    r"sponsored|advertorial|gold rate today|petrol.{0,10}price today|panchang",
    re.I,
)


def is_junk(title: str) -> bool:
    return bool(JUNK_TITLE.search(title or ""))
