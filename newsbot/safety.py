"""Safety gates, in three tiers.

  blocked  never posted: things the law or Meta policy forbids outright (identifying rape victims or
           minors, pending-case commentary, poll results, election silence, communal flashpoints, suicide).
  careful  crime/legal, terror and violence, state violence, politics, anything the AI flags as sensitive.
           Posted only with a strong source, strict attribution drafting and extra checks (see careful_issues).
  clear    everything else.

Rationale: Indian law (defamation, contempt, BNS provisions on promoting enmity, victim and minor
identity rules, RP Act 126/126A, IT Rules) and Meta policy.
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

# Reasons (from SENSITIVE_PATTERNS) that are blocked outright. Everything else that matches is "careful".
# Only the legally binding election blackout is a hard stop (RP Act s.126/126A). Every other
# sensitive topic is drafted in careful mode with topic-specific rules (see topic_rules).
BLOCKED_REASONS = {"election-silence window"}

TOPIC_RULES = {
    "sexual offence": "SEXUAL OFFENCE: never name or hint at the survivor (no family, school, college, village, colony, "
                      "workplace, photo or relationship). Name at most the state or district. Do not name the accused unless "
                      "convicted. No graphic detail. Report only the official step (arrest, FIR, court order, new law).",
    "minor involved": "MINOR: never name or identify a child, a child in conflict with law, or their family or school. "
                      "No photos. Name at most the state or district.",
    "suicide/self-harm": "SUICIDE: no method, place, note contents or sensational wording; do not call it a solution or a "
                         "reaction. Report the verified fact and any official step (inquiry, policy). A helpline line is added automatically.",
    "communal/mob violence": "COMMUNAL/MOB VIOLENCE: facts only: what happened, number hurt, what the police/administration did "
                             "(curfew, arrests, appeals for calm). Do not name or generalise about any religion, caste or community "
                             "unless an official statement does, and then quote the neutral fact only. No blame, no speculation.",
    "religious flashpoint": "RELIGIOUS SENSITIVITY: report the official action or court order in plain words. Do not characterise any "
                            "faith or group and use no emotive adjectives.",
    "legal risk": "PENDING CASE: report what the court or agency did or ordered, with dates. No comment on guilt, innocence or the "
                  "merits, and no prediction of outcome.",
    "election poll publication rules": "ELECTION POLLS: report only declared or counted results and official statements. Do not "
                                       "present an exit poll or opinion poll as a forecast.",
    "election conduct": "ELECTION CONDUCT: report the Election Commission's action neutrally, with no praise or blame for any party.",
}

SUICIDE_METHODS = re.compile(r"\b(hang(ed|ing)|poison(ed|ing)?|jump(ed|ing)? (off|from)|overdose|slit|noose|"
                             r"drank|consum(ed|ing) pesticide|self-immolat)\b", re.I)
HELPLINE_NOTE = "If you or someone you know needs support in India, call Tele-MANAS: 14416 (free, 24x7)."


def topic_rules(reasons: list) -> str:
    return "\n".join("- " + TOPIC_RULES[r] for r in reasons if r in TOPIC_RULES)

# Loaded, name-calling or dehumanising terms. A neutral desk should never write these itself.
LOADED_TERMS = re.compile(
    r"\banti-?national\b|\btraitor\b|\bjihadi\b|\blibtard\b|\burban naxal\b|\bgodi media\b|"
    r"\bbhakt\b|\bpresstitute\b|\bsickular\b|\blove jihad\b|\bpaid media\b|\bgoons\b|"
    r"\bterrorist sympathi[sz]er\b|\bcockroach|\bparasite|"
    r"\bexclusive\b|won'?t tell you|don'?t want you to know|media (is )?(hiding|ignoring|silent)|"
    r"\bcover-?up\b|shocking truth|you won'?t believe",
    re.I,
)

# Meta's engagement-bait rules (asking for likes, shares, tags, follows, comments) demote or remove posts.
ENGAGEMENT_BAIT = re.compile(
    r"\b(like|share|tag|follow|subscribe|retweet|repost)\s+(this|us|our|if|for|a friend|your)\b|"
    r"\b(tag|share with)\s+(a|your|someone|friends?)\b|comment\s+(below|yes|if|your)|"
    r"\btap (the )?(like|heart)\b|\bsmash\b|\bdouble.?tap\b|link in bio|\bfollow for more\b", re.I)
SHOUTING = re.compile(r"(?:\b[A-Z]{4,}\b[\s,]+){2,}\b[A-Z]{4,}\b|(?:\b[A-Z]{4,}\b[\s,]+){3,}")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
EMOTIVE = re.compile(r"\b(tragic|tragedy|horrific|horrifying|shocking|shocked|brutal|brutally|gruesome|heartbreaking|"
                     r"devastating|chilling|ghastly|barbaric|savage|sickening|terrifying|nightmare)\b", re.I)

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
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Rs", "Lok", "Sabha", "Rajya", "Assembly", "Parliament", "Congress", "Bureau", "Commission",
}


def is_sensitive(text: str):
    """Return a short reason if the text touches a gated topic, else None."""
    for pattern, reason in _SENSITIVE:
        if pattern.search(text or ""):
            return reason
    return None


def classify(title: str, snippet: str = "", body: str = "", category: str = "", ai_sensitive: bool = False,
             ai_reason: str = "", now: datetime = None):
    """Return (tier, [reasons]) with tier in {"blocked", "careful", "clear"}."""
    blocked, careful = [], []
    head = f"{title} {snippet}"
    for pattern, reason in _SENSITIVE:
        if pattern.search(head) or (reason in TOPIC_RULES and pattern.search((body or "")[:1500])):
            (blocked if reason in BLOCKED_REASONS else careful).append(reason)
    if in_election_silence(now) and (category in ("Politics", "Government & Policy")
                                     or "election poll publication rules" in careful):
        blocked.append("election-silence window")
    if blocked:
        return "blocked", list(dict.fromkeys(blocked))
    if category == "Crime & Law":
        careful.append("crime/legal story")
    if ai_sensitive:
        careful.append(ai_reason or "flagged sensitive by the AI")
    if category == "Politics":
        careful.append("political story")
    if careful:
        return "careful", list(dict.fromkeys(careful))
    return "clear", []


ATTRIBUTION_CUE = re.compile(
    r"\b(police|officials?|court|judge|bench|tribunal|ministry|government|authorities|said|says|told|"
    r"according to|reported|announced|ordered|stated|alleged|allegedly|claimed|confirmed|reports?)\b", re.I)
GUILT_LABELS = re.compile(
    r"\b(murderer|killer|rapist|culprit|fraudster|conman|thief|criminal|offender|goon|"
    r"guilty|mastermind|monster|beast)\b", re.I)
NAMED_ACCUSED = re.compile(
    r"\b(accused|arrested|suspect|held|booked|detained|charged)\b[:,]?\s+(?:(?:Mr|Mrs|Ms|Dr|Shri|Smt)\.?\s+)?"
    r"[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}")
CONVICTION_OK = re.compile(r"\b(convicted|sentenced|found guilty)\b", re.I)


def careful_issues(draft: dict, facts_text: str, allow_name: str = "") -> list:
    """Extra checks for the careful tier. Empty list = passes. `allow_name` is a public figure who may be
    named as the subject of the story (celebrity lane)."""
    allowed = [w.lower() for w in re.findall(r"[A-Za-z]+", allow_name or "") if len(w) > 2]
    issues = []
    for platform in ("threads", "facebook", "instagram"):
        text = draft.get(platform) or ""
        if "suicide/self-harm" in (draft.get("careful_reasons") or []) and SUICIDE_METHODS.search(text):
            issues.append(f"{platform}: describes a method (suicide reporting rule)")
        if GUILT_LABELS.search(text) and not CONVICTION_OK.search(facts_text or ""):
            issues.append(f"{platform}: presents an allegation as proven (guilt label)")
        if any(not any(w in m.group(0).lower() for w in allowed) for m in NAMED_ACCUSED.finditer(text)):
            issues.append(f"{platform}: names an accused person")
        if EMOTIVE.search(text):
            issues.append(f"{platform}: emotive wording ('{EMOTIVE.search(text).group(1)}'); keep it plain on deaths and crime")
    return list(dict.fromkeys(issues))


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


def check_draft(draft: dict, facts_text: str, now: datetime = None, thin: bool = False) -> list:
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
        if ENGAGEMENT_BAIT.search(text):
            issues.append(f"{platform}: engagement bait (asks for likes/shares/tags/follows)")
        if SHOUTING.search(text):
            issues.append(f"{platform}: shouting in capital letters")
        if len(EMOJI.findall(text)) > 2:
            issues.append(f"{platform}: too many emoji")
        for n in _numbers(text):
            if n not in facts_nums and n not in years_ok:
                issues.append(f"{platform}: number '{n}' not found in source")
        # Proper nouns: ignore the first word of each sentence
        unknown = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", sentence)
            for w in words[1:]:
                if w[0].isupper() and len(w) > 2 and w not in ALLOWED_CAPS \
                        and w.lower() not in facts_lower:
                    unknown.append(w)
        # With only a headline and summary to check against, one stray capitalised word is tolerated;
        # numbers are always checked strictly.
        if len(set(unknown)) > (1 if thin else 0):
            for w in dict.fromkeys(unknown):
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
