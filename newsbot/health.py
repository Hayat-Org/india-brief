"""Health jobs.
  python -m newsbot.health daily   -> daily summary on Telegram (also proves the bot is alive)
  python -m newsbot.health weekly  -> refresh Threads token, check all tokens, check feeds
"""
import sys
from datetime import timedelta

from . import config, meta, pipeline, telegram
from .db import DB
from .sources import all_feeds


def daily(db) -> None:
    start = pipeline.ist_day_start(pipeline.now_utc())
    posted = pipeline.stories(db.list_posted_since(pipeline.iso(start)))
    telegram.send_text(
        "🗞 Daily summary\n"
        f"Posted today: {len(posted)}/{config.MAX_POSTS_PER_DAY}\n"
        f"Awaiting your review: {len(db.list_by_status('pending_review'))}\n"
        f"Candidates queued: {len(db.list_by_status('candidate'))}\n"
        f"Mode: {pipeline.get_mode(db)}{' (PAUSED)' if pipeline.is_paused(db) else ''}"
    )


def weekly(db) -> None:
    report = []
    if config.THREADS_USER_ID:
        try:
            res = meta.refresh_threads_token(pipeline.threads_token(db))
            db.set_setting("threads_token", res["access_token"])
            report.append(f"Threads token refreshed (valid ~{int(res.get('expires_in', 0)) // 86400} days)")
        except Exception as ex:
            report.append(f"⚠️ Threads token refresh FAILED: {str(ex)[:200]}\n"
                          "   Generate a new token in the Meta dashboard and update THREADS_ACCESS_TOKEN, "
                          "then run this job again.")
    for platform, status in meta.check_tokens(pipeline.threads_token(db)).items():
        report.append(f"{'✅' if status.startswith('ok') else '⚠️'} {platform}: {status}")
    _, failed = pipeline.collect_entries(all_feeds())
    if failed:
        report.append(f"⚠️ {len(failed)} feeds returned nothing:\n  " + "\n  ".join(failed[:15]))
    else:
        report.append("✅ All feeds reachable")
    telegram.send_text("🔧 Weekly health check\n" + "\n".join(report))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    db = DB()
    (weekly if mode == "weekly" else daily)(db)


if __name__ == "__main__":
    main()
