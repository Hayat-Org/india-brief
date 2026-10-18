"""One-click full check: python -m newsbot.diagnose (GitHub → Actions → Diagnose → Run workflow).
Sends a single Telegram report listing every problem with a plain-English fix. Never prints secret values."""
import requests

from . import ai, config, meta, pipeline, runlog, telegram
from .db import DB
from .sources import all_feeds

SECRETS = {
    "SUPABASE_URL": config.SUPABASE_URL, "SUPABASE_SERVICE_KEY": config.SUPABASE_SERVICE_KEY,
    "GROQ_API_KEY": config.GROQ_API_KEY, "GEMINI_API_KEY": config.GEMINI_API_KEY,
    "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN, "TELEGRAM_CHAT_ID": config.TELEGRAM_CHAT_ID,
    "THREADS_USER_ID": config.THREADS_USER_ID, "THREADS_ACCESS_TOKEN": config.THREADS_ACCESS_TOKEN,
    "FB_PAGE_ID": config.FB_PAGE_ID, "FB_PAGE_TOKEN": config.FB_PAGE_TOKEN,
    "IG_USER_ID": config.IG_USER_ID,
}
REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def check_secrets(log) -> None:
    missing = [k for k, v in SECRETS.items() if not v]
    for k in REQUIRED:
        if k in missing:
            log.error(f"Secret {k} is empty or missing. Add it in Settings → Secrets and variables → Actions.")
    if not (config.GROQ_API_KEY or config.GEMINI_API_KEY):
        log.error("Neither GROQ_API_KEY nor GEMINI_API_KEY is set, so stories cannot be scored or written.")
    optional = [k for k in missing if k not in REQUIRED and k not in ("GROQ_API_KEY", "GEMINI_API_KEY")]
    log.add("Secrets present: " + ", ".join(k for k, v in SECRETS.items() if v))
    if optional:
        log.warn("Not set (fine only if you have not set that platform up yet): " + ", ".join(optional))
    log.add(f"Platforms the bot will post to: {', '.join(config.enabled_platforms()) or 'none'}")


def check_database(log, db) -> None:
    try:
        counts = db.status_counts(72)
        log.add("Database reachable. Last 72h: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "no stories yet"))
        db._req("GET", "/articles", params={"select": "id,published_at,source_kind,sources,draft", "limit": "1"})
    except Exception as ex:
        msg = str(ex)
        if "published_at" in msg or "column" in msg:
            log.error("Database is missing a column. Re-run schema.sql in the Supabase SQL editor. " + msg[:160])
        else:
            log.error(f"Database problem: {msg[:240]}")
        return
    try:
        db.get_setting("mode2", "")
    except Exception as ex:
        log.error(f"'settings' table problem: {str(ex)[:200]}")
    try:
        url = f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_BUCKET}/does-not-exist.jpg"
        r = requests.get(url, timeout=20)
        if r.status_code == 400 and "not found" in r.text.lower() and "bucket" in r.text.lower():
            log.error(f"Storage bucket '{config.SUPABASE_BUCKET}' does not exist. Create it and make it Public.")
        elif r.status_code in (401, 403):
            log.error(f"Storage bucket '{config.SUPABASE_BUCKET}' is not public. Instagram cannot fetch images from it.")
        else:
            log.add(f"Storage bucket '{config.SUPABASE_BUCKET}' answers publicly.")
    except Exception as ex:
        log.warn(f"Could not test the storage bucket: {str(ex)[:120]}")


def check_ai(log) -> None:
    if not (config.GROQ_API_KEY or config.GEMINI_API_KEY):
        return
    results = ai.probe_models()
    for label, ok, ms, err in results:
        if ok:
            log.add(f"AI ✅ {label} ({ms} ms)")
        else:
            log.warn(f"AI ❌ {label}: {err}")
    if results and not any(r[1] for r in results):
        log.error("No AI model answered. Check the API keys, model names (GROQ_*_MODEL / GEMINI_MODELS) and free-tier quota.")


def check_telegram(log) -> None:
    try:
        r = requests.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe", timeout=20).json()
        if r.get("ok"):
            log.add(f"Telegram bot ✅ @{r['result'].get('username')}")
        else:
            log.error(f"Telegram rejected the bot token: {r.get('description')}")
    except Exception as ex:
        log.error(f"Telegram unreachable: {str(ex)[:150]}")


def check_platforms(log, db) -> None:
    try:
        statuses = meta.check_tokens(pipeline.threads_token(db))
    except Exception as ex:
        log.error(f"Platform check crashed: {str(ex)[:200]}")
        return
    for platform, status in statuses.items():
        if status.startswith("ok"):
            log.add(f"{platform} ✅ {status}")
        else:
            log.error(f"{platform}: {status}")


def check_feeds(log) -> None:
    from .ingest import feed_report
    entries, results = pipeline.collect_entries(all_feeds())
    line, warns = feed_report(results)
    log.add(line + f" · {len(entries)} stories fetched")
    for w in warns[:15]:
        log.warn("feed down · " + w)
    fresh = [e for e in entries if pipeline.now_utc() - e.published <= pipeline.timedelta(hours=config.ARTICLE_MAX_AGE_HOURS)]
    log.add(f"Stories inside the {config.ARTICLE_MAX_AGE_HOURS}h window: {len(fresh)}")
    if results and not fresh:
        log.error("No fresh stories found in any feed. Nothing can be posted until this is fixed.")


def main() -> None:
    log = runlog.start("diagnose", "Full diagnosis")
    db = None
    try:
        check_secrets(log)
        check_telegram(log)
        if config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY:
            db = DB()
            pipeline.load_runtime_tokens(db)
            check_database(log, db)
            log.add(pipeline.queue_summary(db))
            log.add("Last runs:")
            for line in runlog.heartbeat_lines(db):
                log.add("  " + line)
            check_platforms(log, db)
        check_ai(log)
        check_feeds(log)
    except Exception:
        log.crash()
        raise
    finally:
        log.add("Mode: " + (pipeline.get_mode(db) if db else "unknown"))
        log.finish(None)  # a diagnosis should not overwrite job heartbeats
        # Always send, even with REPORT_LEVEL=important.
        if not log.should_send():
            telegram.send_text(log.render())


if __name__ == "__main__":
    main()
