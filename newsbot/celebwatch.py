"""Fast-track watch (6 times a day): read entertainment, sports and business feeds plus the main national and
official feeds, score the new stories, and post right away anything about a well-known person or any urgent,
critical news (lockdown, major disaster...), duplicate-checked. Reports on Telegram only when it posted
something or hit a problem. Normal ingest and the 4 daily slots are untouched.
"""
from . import ai, config, pipeline, runlog
from .db import DB
from .ingest import stage
from .sources import watch_feeds


def main() -> None:
    log = runlog.start("celebwatch", "Fast-track watch", quiet_unless_activity=True)
    db = None
    try:
        db = DB()
        pipeline.load_runtime_tokens(db)
        if not config.CELEBRITY_FAST:
            log.add("Celebrity fast-track is switched off (CELEBRITY_FAST=0).")
            return
        entries, results = stage(log, "Fetching feeds", lambda: pipeline.collect_entries(watch_feeds()), ([], []))
        ok = sum(1 for r in results if r.ok)
        log.add(f"Feeds: {ok}/{len(results)} working")
        if results and not ok:
            log.error("Every celebrity feed failed. Is the runner offline or blocked?")
        stats = stage(log, "Saving new stories", lambda: pipeline.ingest(db, entries))
        if stats:
            log.add(f"Stories seen {stats['seen']} → new {stats['new']}, merged {stats['merged']}, "
                    f"already known {stats['dup']}, older than {config.ARTICLE_MAX_AGE_HOURS}h {stats['old']}")
            if stats["new"]:
                sc = stage(log, "Scoring", lambda: pipeline.score_pending(
                    db, only_ids=set(stats["new_ids"]), limit=config.CELEBWATCH_SCORE_LIMIT))
                if sc:
                    log.add(f"Scored {sc['scored']} → {sc['candidates']} candidates ({sc['celebrity']} celebrity)")
                    if "rate_limited" in sc:
                        log.warn("AI free limit reached: celebrity stories wait for the next run.")
                    elif sc["errors"]:
                        log.error(f"AI scoring failed for {sc['errors']} stories. " + " | ".join(sc["error_samples"]))
        ig = stage(log, "Instagram retry", lambda: pipeline.retry_instagram_rate_limited(db), [])
        if ig:
            log.add(f"Instagram retry after rate limit: {len(ig)} post(s) → {', '.join(ig)}")
            log.mark_activity()
        made_up = stage(log, "Stories catch-up", lambda: pipeline.catch_up_stories(db), [])
        if made_up:
            log.add(f"Stories made up: {', '.join(made_up)}")
            log.mark_activity()
        posted = stage(log, "Celebrity fast-track", lambda: pipeline.celebrity_pass(db), [])
        if posted:
            log.add(f"Celebrity fast-track: {len(posted)} processed ({', '.join(posted)})")
            log.mark_activity()
        urgent = stage(log, "Urgent fast-track", lambda: pipeline.breaking_pass(db), [])
        if urgent:
            log.add(f"Urgent fast-track: {len(urgent)} processed ({', '.join(urgent)})")
            log.mark_activity()
        if ai.USAGE["ok"] or ai.USAGE["fail"]:
            log.add(ai.usage_summary())
    except Exception:
        log.crash()
        raise
    finally:
        log.finish(db)


if __name__ == "__main__":
    main()
