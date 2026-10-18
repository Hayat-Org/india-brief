"""Health jobs.
  python -m newsbot.health daily   -> daily summary on Telegram (also proves the bot is alive)
  python -m newsbot.health weekly  -> refresh Threads token, check all tokens, check feeds
"""
import sys

from . import config, meta, pipeline, runlog
from .db import DB
from .ingest import feed_report
from .sources import all_feeds


def daily(db, log) -> None:
    start = pipeline.ist_day_start(pipeline.now_utc())
    posted = pipeline.stories(db.list_posted_since(pipeline.iso(start)))
    log.add(f"Posted today: {len(posted)}/{config.MAX_POSTS_PER_DAY}")
    log.add(pipeline.queue_summary(db))
    log.add(f"Mode: {pipeline.get_mode(db)}{' (PAUSED)' if pipeline.is_paused(db) else ''}")
    log.add("Platforms connected: " + (", ".join(config.enabled_platforms()) or "none (dry run)"))
    log.add("Last runs:")
    for line in runlog.heartbeat_lines(db):
        log.add("  " + line)
    # Platform problems show up here too, so a broken token is noticed within a day.
    try:
        for platform, status in meta.check_tokens(pipeline.threads_token(db)).items():
            if not status.startswith("ok"):
                log.error(f"{platform}: {status}")
    except Exception as ex:
        log.warn(f"Could not check platforms: {str(ex)[:150]}")
    log.mark_activity()


def weekly(db, log) -> None:
    if config.THREADS_USER_ID:
        try:
            res = meta.refresh_threads_token(pipeline.threads_token(db))
            db.set_setting("threads_token", res["access_token"])
            log.add(f"Threads token refreshed (valid ~{int(res.get('expires_in', 0)) // 86400} days)")
        except Exception as ex:
            log.error(f"Threads token refresh FAILED: {str(ex)[:200]}\n"
                      "   Generate a new token in the Meta dashboard and update THREADS_ACCESS_TOKEN, "
                      "then run this job again.")
    for platform, status in meta.check_tokens(pipeline.threads_token(db)).items():
        if status.startswith("ok"):
            log.add(f"✅ {platform}: {status}")
        else:
            log.error(f"{platform}: {status}")
    _, results = pipeline.collect_entries(all_feeds())
    line, warns = feed_report(results)
    log.add(line)
    for w in warns:
        log.warn("feed down · " + w)
    log.mark_activity()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    log = runlog.start("health", "Weekly health check" if mode == "weekly" else "Daily summary")
    db = None
    try:
        db = DB()
        pipeline.load_runtime_tokens(db)
        (weekly if mode == "weekly" else daily)(db, log)
    except Exception:
        log.crash()
        raise
    finally:
        log.finish(db)


if __name__ == "__main__":
    main()
