"""AI layer: scoring + human-voice drafting. Groq first, Gemini as automatic failover."""
import json
import re
import time

import requests

from . import config
from .sources import CATEGORIES

TIMEOUT = 60


class AIError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _groq(model: str, system: str, user: str) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json={
            "model": model,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    if r.status_code >= 300:
        raise AIError(f"groq {model} {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _gemini(model: str, system: str, user: str) -> str:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": config.GEMINI_API_KEY},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
        },
        timeout=TIMEOUT,
    )
    if r.status_code >= 300:
        raise AIError(f"gemini {model} {r.status_code}: {r.text[:200]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _chain(quality: str) -> list:
    groq = []
    if config.GROQ_API_KEY:
        fast, good = config.GROQ_FAST_MODEL, config.GROQ_QUALITY_MODEL
        groq = [("groq", m) for m in ([fast, good] if quality == "fast" else [good, fast])]
    gem = [("gemini", m) for m in config.GEMINI_MODELS] if config.GEMINI_API_KEY else []
    return groq + gem


def chat_json(system: str, user: str, quality: str = "fast") -> dict:
    """Try each model in turn until one returns valid JSON."""
    errors = []
    for provider, model in _chain(quality):
        try:
            raw = (_groq if provider == "groq" else _gemini)(model, system, user)
            return _extract_json(raw)
        except Exception as ex:  # rate limit, deprecation, bad JSON: try the next model
            errors.append(f"{provider}:{model}: {str(ex)[:120]}")
            time.sleep(1)
    raise AIError("all models failed: " + " | ".join(errors) if errors else "no AI keys configured")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
SCORE_SYSTEM = f"""You are the editor-in-chief's triage assistant for a neutral Indian news desk.
Judge ONE story. Reply with JSON only:
{{"is_india": bool, "is_opinion": bool, "is_promotional": bool, "is_factcheck": bool,
 "category": one of {CATEGORIES},
 "state": "National" or an Indian state/UT name,
 "significance": 1-10, "impact": 1-10, "interest": 1-10,
 "sensitive": bool, "sensitive_reason": "short text or empty"}}

Scales (use the whole range; most stories are 3-6):
- significance: national importance. 9-10 = major national event (big disaster, war, landmark law/verdict, national election result). 5-6 = notable state/national development. 1-3 = minor/local/routine.
- impact: how many people are directly affected and how strongly.
- interest (popularity): how likely an ordinary reader across India wants to know it: broad usefulness, curiosity and everyday relevance (prices, weather, exams, travel, health, sports, major events). It is NOT outrage, controversy or partisan heat: never score a story higher because it is divisive.
Rules: is_india=false if it is not about India or Indians. Opinion/editorial/analysis = is_opinion true.
Deals, discounts, astrology, fantasy-sports tips, ads = is_promotional true.
sensitive=true for communal/religious tension, sexual offences, minors, suicide, terror, mob violence, ongoing court/legal risk, or anything where a careless post could inflame or defame.
Do not favour or disfavour any party, religion, community or ideology when scoring."""


def score_article(article: dict) -> dict:
    user = (f"Title: {article['title']}\nSource: {article.get('source', '')}\n"
            f"Snippet: {(article.get('snippet') or '')[:600]}\n"
            f"Feed region hint: {article.get('feed_state') or 'none'}")
    d = chat_json(SCORE_SYSTEM, user, "fast")

    def num(key):
        try:
            return max(1.0, min(10.0, float(d.get(key, 1))))
        except (TypeError, ValueError):
            return 1.0

    sig, imp, inte = num("significance"), num("impact"), num("interest")
    category = d.get("category") if d.get("category") in CATEGORIES else "Society"
    return {
        "is_india": bool(d.get("is_india", True)),
        "is_opinion": bool(d.get("is_opinion", False)),
        "is_promotional": bool(d.get("is_promotional", False)),
        "is_factcheck": bool(d.get("is_factcheck", False)),
        "category": "Fact Check" if d.get("is_factcheck") else category,
        "state": str(d.get("state") or "National")[:40],
        "significance": sig, "impact": imp, "interest": inte,
        "combined": combined_score(sig, imp, inte),
        "sensitive": bool(d.get("sensitive", False)),
        "sensitive_reason": str(d.get("sensitive_reason") or "")[:120],
    }


def combined_score(significance: float, impact: float, interest: float) -> float:
    return round(config.W_SIGNIFICANCE * significance + config.W_IMPACT * impact
                 + config.W_INTEREST * interest, 1)


# --------------------------------------------------------------------------
# Drafting (human voice, neutral)
# --------------------------------------------------------------------------
def draft_system() -> str:
    return f"""You write for {config.BRAND_NAME}, an AI-assisted Indian news desk. Write like a thoughtful,
well-informed friend explaining the news over chai: warm, plain, short sentences, no jargon.

VOICE
- Human and conversational, never robotic. No clickbait, no ALL CAPS, no outrage, no "BREAKING" shouting.
- Open with the hook: the single most important fact as one plain, specific sentence. Never a teaser, never hold back the key fact to force a click.
- Then 1-3 short sentences: what happened, who is affected, and what happens next, only as stated in the article.
- At most one emoji, and none for deaths, violence, disasters or crime.
- Never ask people to like, share, comment, tag or follow. You may end with one short, genuine, non-leading question when it fits; never a polarising one.

NEUTRALITY (strict)
- Take no side. Do not praise or blame any party, leader, religion, caste, community or ideology.
- Attribute every claim: "police said", "the government says", "according to <outlet>". When a dispute is reported, give each side's stated position in equal terms.
- If something is reported as false or misleading by a fact-checker, say so plainly and credit them. If it is true, say so. Be straightforward either way; fairness is not vagueness.
- Accused is not guilty: use "alleged"/"accused". Never name minors or survivors of sexual offences. Never speculate or guess motives.
- No loaded or insulting words.

FACTS ONLY
- Use ONLY facts in the article text provided. Do not add numbers, names, dates, places or quotes that are not there.
- Write in your own words; do not copy sentences.
- If the story is DEVELOPING (single source), say it is "reported by <outlet>" and "not yet confirmed by other outlets".

OUTPUT: JSON only with keys
"threads" (max 380 chars), "facebook" (max 650 chars, may use 2 short paragraphs),
"instagram" (max 900 chars, 2-3 short paragraphs),
"card_headline" (max 95 chars, a plain factual statement, no quotes marks),
"hashtags" (3 topic-specific lowercase words, no #, no spaces).
Do NOT include URLs, the outlet name as a sign-off, or hashtags inside the texts: those are added automatically."""


def write_post(article: dict, body: str, developing: bool) -> dict:
    srcs = ", ".join(s["name"] for s in article.get("sources", [])) or article.get("source", "")
    user = (
        f"STATUS: {'DEVELOPING (single source)' if developing else 'Confirmed by multiple outlets'}\n"
        f"FACT-CHECK STORY: {'yes' if article.get('is_factcheck') else 'no'}\n"
        f"OUTLETS: {srcs}\n"
        f"CATEGORY: {article.get('category')}  REGION: {article.get('state')}\n\n"
        f"HEADLINE: {article['title']}\nSUMMARY: {article.get('snippet') or ''}\n"
        f"ARTICLE TEXT:\n{body[:3500] if body else '(not available; use headline and summary only)'}"
    )
    d = chat_json(draft_system(), user, "quality")
    for key in ("threads", "facebook", "instagram", "card_headline"):
        d[key] = str(d.get(key) or "").strip()
    tags = d.get("hashtags") or []
    d["hashtags"] = [re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in tags][:3]
    d["hashtags"] = [t for t in d["hashtags"] if t]
    return d
