"""AI layer: scoring + human-voice drafting. Groq first, Gemini as automatic failover."""
import json
import re
import time
from collections import Counter

import requests

from . import config
from .sources import CATEGORIES

TIMEOUT = 60

# What happened during this run: {"ok": Counter, "fail": Counter} keyed by "provider:model".
USAGE = {"ok": Counter(), "fail": Counter()}
DEAD = set()  # (provider, model) that returned 404: retired or not available to this key


def usage_summary() -> str:
    ok = ", ".join(f"{m} ×{n}" for m, n in USAGE["ok"].most_common()) or "none"
    fail = sum(USAGE["fail"].values())
    return f"AI calls OK: {ok}" + (f" · failed attempts: {fail}" if fail else "")


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
        fast, good = config.GROQ_FAST_MODELS, config.GROQ_QUALITY_MODELS
        groq = [("groq", m) for m in (fast + good if quality == "fast" else good + fast)]
    gem = [("gemini", m) for m in config.GEMINI_MODELS] if config.GEMINI_API_KEY else []
    seen, out = set(), []
    for c in groq + gem:
        if c not in seen and c not in DEAD:  # DEAD = models that answered 404 earlier in this run
            seen.add(c)
            out.append(c)
    return out


def chat_json(system: str, user: str, quality: str = "fast") -> dict:
    """Try each model in turn until one returns valid JSON."""
    errors = []
    for provider, model in _chain(quality):
        try:
            raw = (_groq if provider == "groq" else _gemini)(model, system, user)
            result = _extract_json(raw)
            USAGE["ok"][f"{provider}:{model}"] += 1
            return result
        except Exception as ex:  # rate limit, deprecation, bad JSON: try the next model
            USAGE["fail"][f"{provider}:{model}"] += 1
            if " 404" in str(ex):
                DEAD.add((provider, model))
            errors.append(f"{provider}:{model}: {str(ex)[:120]}")
            time.sleep(1)
    raise AIError("all models failed: " + " | ".join(errors) if errors else "no AI keys configured")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
SCORE_FIELDS = (
    '{"is_india": bool, "is_opinion": bool, "is_promotional": bool, "is_factcheck": bool,\n'
    ' "category": one of ' + str(CATEGORIES) + ',\n'
    ' "state": "National" or an Indian state/UT name,\n'
    ' "significance": 1-10, "impact": 1-10, "interest": 1-10,\n'
    ' "sensitive": bool, "sensitive_reason": "short text or empty"}'
)

SCORE_RULES = """Scales (use the whole range; most stories are 3-6):
- significance: how important the development is for the public, nationally or in its own region. 9-10 = major national event (big disaster, war, landmark law/verdict, national election result). 7-8 = very serious or far-reaching state/regional development (disease outbreak, major policy change, large-scale fraud or failure, court order affecting many people, environmental harm). 5-6 = notable development. 1-3 = minor/routine (ceremonies, routine notices, small local events).
- impact: how many people are directly affected and how strongly. Judge by who is affected, not by how famous the outlet or place is: a story from a small outlet can matter a lot.
- interest (popularity): how likely an ordinary reader across India wants to know it: broad usefulness, curiosity and everyday relevance (prices, weather, exams, travel, health, sports, major events). It is NOT outrage, controversy or partisan heat: never score a story higher because it is divisive.
Rules: is_india=false if it is not about India or Indians. Opinion/editorial/analysis = is_opinion true.
Deals, discounts, astrology, fantasy-sports tips, ads = is_promotional true.
sensitive=true for communal/religious tension, sexual offences, minors, suicide, terror, mob violence, ongoing court/legal risk, or anything where a careless post could inflame or defame.
Do not favour or disfavour any party, religion, community or ideology when scoring."""

SCORE_SYSTEM = ("You are the editor-in-chief's triage assistant for a neutral Indian news desk.\n"
                "Judge ONE story. Reply with JSON only:\n" + SCORE_FIELDS + "\n\n" + SCORE_RULES)

SCORE_SYSTEM_BATCH = (
    "You are the editor-in-chief's triage assistant for a neutral Indian news desk.\n"
    "You will receive several stories, each starting with [n]. Judge each one independently.\n"
    'Reply with JSON only: {"results": [ {"id": n, ...fields...}, ... ]} with exactly one entry per story.\n'
    "Each entry has the field \"id\" (the story's number) plus these fields:\n"
    + SCORE_FIELDS + "\n\n" + SCORE_RULES)


def _normalize_score(d: dict) -> dict:
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


def _story_text(article: dict, snippet_chars: int) -> str:
    return (f"Title: {article['title']} | Source: {article.get('source', '')} | "
            f"Region hint: {article.get('feed_state') or 'none'}\n"
            f"Snippet: {(article.get('snippet') or '')[:snippet_chars]}")


def score_article(article: dict) -> dict:
    return _normalize_score(chat_json(SCORE_SYSTEM, _story_text(article, 600), "fast"))


def score_batch(articles: list) -> dict:
    """Judge several stories in one AI call. Returns {article_id: result}.

    Stories the model skipped or mangled are retried one by one. If every model fails the
    AIError propagates (keys, quota or outage: retrying individually would fail too).
    """
    if not articles:
        return {}
    user = "\n\n".join(f"[{n}] {_story_text(a, 300)}" for n, a in enumerate(articles, 1))
    d = chat_json(SCORE_SYSTEM_BATCH, user, "fast")
    rows = d.get("results") if isinstance(d, dict) else d
    out = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            idx = int(row.get("id"))
            if 1 <= idx <= len(articles) and articles[idx - 1]["id"] not in out:
                out[articles[idx - 1]["id"]] = _normalize_score(row)
        except (TypeError, ValueError, AttributeError):
            continue
    for a in articles:
        if a["id"] not in out:
            try:
                out[a["id"]] = score_article(a)
            except Exception:
                pass
    return out


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
- If SPOTLIGHT is yes, the story comes from a specialist, regional or official source and the big national outlets have not carried it yet. Say plainly "reported by <outlet>" and, if it fits, that it "has not yet been widely reported". Never use words like exclusive, shocking, hidden or suppressed, never say the media is ignoring it, and never imply a cover-up.

OUTPUT: JSON only with keys
"threads" (max 380 chars), "facebook" (max 650 chars, may use 2 short paragraphs),
"instagram" (max 900 chars, 2-3 short paragraphs),
"card_headline" (max 95 chars, a plain factual statement, no quotes marks),
"hashtags" (3 topic-specific lowercase words, no #, no spaces),
"photo_query" (2-4 plain words naming a PLACE, LANDMARK or SCENE for a stock/archive photo, e.g. "Majuli island Assam" or "monsoon flooded street India". Never a person, party, accused or victim, never a brand; empty string if nothing suitable).
Do NOT include URLs, the outlet name as a sign-off, or hashtags inside the texts: those are added automatically."""


CAREFUL_RULES = """
CAREFUL MODE (this is a crime, legal, violence or political story). Extra rules, all mandatory:
- Attribute every claim: "police said", "according to the court", "officials said", "the ministry said".
- Anything not yet proven is "alleged"; people are "accused" or "arrested", never called a criminal, murderer, rapist or similar. Add that it is an allegation where it fits.
- Do NOT name any accused person, suspect, victim, minor or relative. Say "a 34-year-old man", "a woman", "the owner" instead. Public office holders speaking in office may be named.
- No graphic detail, no speculation about motive, no mention of anyone's religion, caste or community unless the article says it is central, and even then stay neutral.
- Do not comment on guilt, innocence or the merits of a court case; report what the court or police did or said.
- Political stories: give each side the same treatment, no adjectives, no praise or blame.
"""


def write_post(article: dict, body: str, developing: bool, careful: bool = False) -> dict:
    srcs = ", ".join(s["name"] for s in article.get("sources", [])) or article.get("source", "")
    user = (
        f"STATUS: {'DEVELOPING (single source)' if developing else 'Confirmed by multiple outlets'}\n"
        f"FACT-CHECK STORY: {'yes' if article.get('is_factcheck') else 'no'}\n"
        f"SPOTLIGHT: {'yes' if article.get('spotlight') else 'no'}\n"
        f"CAREFUL MODE: {'yes' if careful else 'no'}\n"
        f"OUTLETS: {srcs}\n"
        f"CATEGORY: {article.get('category')}  REGION: {article.get('state')}\n\n"
        f"HEADLINE: {article['title']}\nSUMMARY: {article.get('snippet') or ''}\n"
        f"ARTICLE TEXT:\n{body[:3500] if body else '(not available; use headline and summary only)'}"
    )
    d = chat_json(draft_system() + (CAREFUL_RULES if careful else ""), user, "quality")
    for key in ("threads", "facebook", "instagram", "card_headline"):
        d[key] = str(d.get(key) or "").strip()
    d["photo_query"] = re.sub(r"[^A-Za-z0-9 ,'\-]", " ", str(d.get("photo_query") or "")).strip()[:60]
    tags = d.get("hashtags") or []
    d["hashtags"] = [re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in tags][:3]
    d["hashtags"] = [t for t in d["hashtags"] if t]
    return d


def probe_models() -> list:
    """One tiny call per configured model. Returns [(label, ok, milliseconds, error)]."""
    results = []
    for provider, model in _chain("fast") + [c for c in _chain("quality") if c not in _chain("fast")]:
        t0 = time.time()
        try:
            raw = (_groq if provider == "groq" else _gemini)(model, 'Reply with JSON only.', '{"ok": true} - repeat this JSON')
            _extract_json(raw)
            results.append((f"{provider}:{model}", True, int((time.time() - t0) * 1000), ""))
        except Exception as ex:
            results.append((f"{provider}:{model}", False, int((time.time() - t0) * 1000), str(ex)[:160]))
    return results
