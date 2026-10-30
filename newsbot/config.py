"""All settings come from environment variables (GitHub Secrets in production)."""
import json
import os


def env(name: str, default: str = "") -> str:
    # .strip() avoids hidden newline/space problems when pasting secrets.
    # An unset GitHub secret arrives as "", which must mean "use the default".
    value = os.environ.get(name, "").strip()
    return value if value else default


def _int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> list:
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


# --- Branding -------------------------------------------------------------
BRAND_NAME = env("BRAND_NAME", "India In Short")
BRAND_HANDLE = env("BRAND_HANDLE", "@indiainshort.daily")

# --- Database (Supabase) --------------------------------------------------
SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = env("SUPABASE_BUCKET", "cards")

# --- AI (free tiers). Model names change over time, so they are configurable.
GROQ_API_KEY = env("GROQ_API_KEY")
GEMINI_API_KEY = env("GEMINI_API_KEY")
# Each setting is a comma-separated list tried in order, so one retired model never stops the bot.
GROQ_FAST_MODELS = _list("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
GROQ_QUALITY_MODELS = _list("GROQ_QUALITY_MODEL", "openai/gpt-oss-120b")
GEMINI_MODELS = _list("GEMINI_MODELS", "gemini-3.6-flash,gemini-3.8-flash,gemini-3.5-flash-lite")

# --- Telegram -------------------------------------------------------------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

# --- Meta (Threads / Facebook Page / Instagram) ---------------------------
GRAPH_VERSION = env("GRAPH_VERSION", "v23.0")
THREADS_USER_ID = env("THREADS_USER_ID")
THREADS_ACCESS_TOKEN = env("THREADS_ACCESS_TOKEN")  # seed value; refreshed copy lives in DB
FB_PAGE_ID = env("FB_PAGE_ID")
FB_PAGE_TOKEN = env("FB_PAGE_TOKEN")
IG_USER_ID = env("IG_USER_ID")
FB_APP_ID = env("FB_APP_ID")          # only needed for the /fbtoken Telegram command
FB_APP_SECRET = env("FB_APP_SECRET")
IG_TOKEN = env("IG_TOKEN") or FB_PAGE_TOKEN
# Instagram API with Instagram Login (graph.instagram.com): per-account limits instead of the Meta app's shared limit.
# Saved with /igtoken in Telegram (kept in the database and refreshed weekly) or as the secret IG_LOGIN_TOKEN.
IG_LOGIN_TOKEN = env("IG_LOGIN_TOKEN")
IG_LOGIN_VERSION = env("IG_LOGIN_VERSION", "v21.0")
IG_LOGIN_GAP_SECONDS = _float("IG_LOGIN_GAP_SECONDS", 20.0)

# --- Behaviour ------------------------------------------------------------
DRY_RUN = env("DRY_RUN") == "1"          # log instead of calling social APIs
DEFAULT_MODE = env("DEFAULT_MODE", "auto")  # auto = post by itself (risky stories skipped), review = approve on Telegram

MAX_POSTS_PER_DAY = _int("MAX_POSTS_PER_DAY", 8)
MAX_POSTS_PER_SLOT = _int("MAX_POSTS_PER_SLOT", 2)
MAX_BREAKING_PER_DAY = _int("MAX_BREAKING_PER_DAY", 3)
MAX_PENDING_REVIEW = _int("MAX_PENDING_REVIEW", 4)
REVIEW_EXPIRY_HOURS = _int("REVIEW_EXPIRY_HOURS", 10)

# Ranking: criticality (significance), importance (impact) and popularity (interest) all
# matter; popularity gets a modest lift for reach. Bonuses below are deliberately small.
W_SIGNIFICANCE = _float("W_SIGNIFICANCE", 0.35)
W_IMPACT = _float("W_IMPACT", 0.30)
W_INTEREST = _float("W_INTEREST", 0.35)
CRITICAL_BONUS = _float("CRITICAL_BONUS", 0.5)      # significance >= 8
IMPORTANT_BONUS = _float("IMPORTANT_BONUS", 0.3)    # impact >= 8
TREND_BONUS = _float("TREND_BONUS", 0.6)            # matches a trending search in India
TREND_BONUS_BIG = _float("TREND_BONUS_BIG", 1.0)    # ...with 100K+ searches
MAX_CAREFUL_PER_DAY = _int("MAX_CAREFUL_PER_DAY", 4)  # crime/violence/legal stories posted under the strict rules
MAX_POLITICS_PER_DAY = _int("MAX_POLITICS_PER_DAY", 2)  # keeps the feed balanced
USE_GOOGLE_NEWS = env("USE_GOOGLE_NEWS", "1") == "1"
ATTACH_LINKS = env("ATTACH_LINKS", "1") == "1"  # link to the original on Facebook/Threads
DIGEST_MIN_STORIES = _int("DIGEST_MIN_STORIES", 3)
DIGEST_MAX_STORIES = _int("DIGEST_MAX_STORIES", 5)

MIN_SCORE = _float("MIN_SCORE", 5.5)
BREAKING_SCORE = _float("BREAKING_SCORE", 8.8)
BREAKING_MIN_SOURCES = _int("BREAKING_MIN_SOURCES", 2)
URGENT_SIGNIFICANCE = _float("URGENT_SIGNIFICANCE", 9.0)  # urgent/critical story: posts on ONE trusted source (main outlet or official)
URGENT_WORD_SIGNIFICANCE = _float("URGENT_WORD_SIGNIFICANCE", 8.0)  # ...or this with an emergency word in the headline
MAX_SCORE_PER_RUN = _int("MAX_SCORE_PER_RUN", 40)   # stories scored per hourly run (a third of this at night, 00:00-07:00 IST)
AI_PACE_SECONDS = _float("AI_PACE_SECONDS", 3.0)    # pause after each AI call, to stay under per-minute limits
SCORE_BATCH_SIZE = _int("SCORE_BATCH_SIZE", 8)       # stories judged per AI call (saves free-tier quota)
ARTICLE_MAX_AGE_HOURS = _int("ARTICLE_MAX_AGE_HOURS", 48)  # never post anything published longer ago
# Old rows (and their card images) are deleted after this. Never below the age limit + 24h, because stored
# links are what stop an old story from being treated as new.
DATA_RETENTION_HOURS = max(_int("DATA_RETENTION_HOURS", 72), ARTICLE_MAX_AGE_HOURS + 24)

# Spotlight lane: important stories from specialist/regional/official sources that the big
# national outlets have not carried yet. Always attributed, never presented as "exclusive".
SPOTLIGHT_PER_DAY = _int("SPOTLIGHT_PER_DAY", 1)
SPOTLIGHT_MIN_VALUE = _float("SPOTLIGHT_MIN_VALUE", 6.0)   # (significance + impact) / 2
SPOTLIGHT_MIN_AGE_HOURS = _int("SPOTLIGHT_MIN_AGE_HOURS", 2)  # give mainstream a chance to pick it up first
SPOTLIGHT_AUTO = env("SPOTLIGHT_AUTO", "1") == "1"  # 0 = Spotlight posts from non-official sources are skipped (or reviewed)

# Celebrity fast-track: stories about well-known people post as soon as they are scored and checked,
# without waiting for a slot. Own daily cap; it does not use up MAX_POSTS_PER_DAY or MAX_CAREFUL_PER_DAY.
CELEBRITY_FAST = env("CELEBRITY_FAST", "1") == "1"
CELEBRITY_PER_DAY = _int("CELEBRITY_PER_DAY", 3)
STORY_ONLY_PER_DAY = _int("STORY_ONLY_PER_DAY", 2)          # follow-up developments get a Story only, up to this many a day
URGENT_FOLLOWUP_HOURS = _int("URGENT_FOLLOWUP_HOURS", 12)   # same state + category again within this time = follow-up
CELEBRITY_MIN_SCORE = _float("CELEBRITY_MIN_SCORE", 3.5)   # normal minimum is MIN_SCORE
CELEBRITY_BONUS = _float("CELEBRITY_BONUS", 0.8)
CELEBWATCH_SCORE_LIMIT = _int("CELEBWATCH_SCORE_LIMIT", 30)  # stories scored per fast-track watch run

# Instagram app-level rate limit ("Application request limit reached", code 4): back off, then retry by itself.
IG_COOLDOWN_MINUTES = _int("IG_COOLDOWN_MINUTES", 90)   # pause all Instagram publishing after this error (it is usually an anti-spam block)
IG_MIN_GAP_SECONDS = _float("IG_MIN_GAP_SECONDS", 60.0)  # minimum time between two Instagram publishes (feed posts and Stories)
IG_STORIES_PER_DAY = _int("IG_STORIES_PER_DAY", 6)       # Instagram Stories a day (Facebook Stories are not limited)
IG_AUTO_RETRY_HOURS = _int("IG_AUTO_RETRY_HOURS", 12)    # keep retrying a rate-limited post for this long
IG_MAX_TRIES = _int("IG_MAX_TRIES", 4)
STORY_CATCHUP_HOURS = _int("STORY_CATCHUP_HOURS", 6)    # Stories missed earlier are still posted if the post is newer than this

POLL_SECONDS = _int("POLL_SECONDS", 240)  # how long each approvals run keeps listening for taps

# Photos: copyright-safe images only (Wikimedia Commons CC0/PD/CC BY, optional Pexels). See photos.py.
USE_PHOTOS = env("USE_PHOTOS", "1") == "1"
STORIES = env("STORIES", "1") == "1"   # also post a 9:16 story to Instagram and Facebook after each feed post
PEXELS_API_KEY = env("PEXELS_API_KEY")      # optional (Pexels has paused new keys)
PIXABAY_API_KEY = env("PIXABAY_API_KEY")    # optional, free and instant at pixabay.com/api/docs

# Telegram run reports: "full" = a report after every run, "important" = only runs that
# had errors/warnings or actually posted/queued something.
REPORT_LEVEL = env("REPORT_LEVEL", "full")

# Election silence / special no-political-post windows, e.g.
# ELECTION_SILENCE='[["2026-11-01","2026-11-03"]]'  (IST dates, inclusive)
try:
    ELECTION_SILENCE = json.loads(env("ELECTION_SILENCE", "[]"))
except json.JSONDecodeError:
    ELECTION_SILENCE = []

USER_AGENT = (
    "Mozilla/5.0 (compatible; IndiaBriefBot/1.0; +https://github.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


def supabase_auth_headers(key: str = None) -> dict:
    """Legacy service_role keys are JWTs (send as Bearer too); new sb_secret_ keys go in apikey only."""
    key = key or SUPABASE_SERVICE_KEY
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def enabled_platforms() -> list:
    """Platforms with credentials present (lets you go live one platform at a time)."""
    out = []
    if THREADS_USER_ID:  # token may live in the DB (auto-refreshed), so only the ID is required
        out.append("threads")
    if FB_PAGE_ID and FB_PAGE_TOKEN:
        out.append("facebook")
    if (IG_USER_ID and IG_TOKEN) or IG_LOGIN_TOKEN:
        out.append("instagram")
    return out
