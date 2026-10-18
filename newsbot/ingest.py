"""Hourly job: fetch feeds -> dedup -> score -> fast-track breaking news. Sends a Telegram report."""
from . import ai, pipeline, runlog, trends
from .db import DB
from .sources import all_feeds


def feed_report(results: list) -> tuple:
    """(summary line, warning lines) for a list of FeedResult."""
    ok = [r for r in results if r.ok]
    fallback = [r for r in ok if r.via == "fallback"]
    bad = [r for r in results if not r.ok]
    line = (f"Feeds: {len(ok)}/{len(results)} working"
            + (f" · {len(fallback)} via Google News backup" if fallback else ""))
    warns = [f"{r.name}: {r.error}" for r in bad]
    return line, warns


def stage(log, name, fn, default=None):
    """Run one step. A failure is reported (with the cause) but never stops the steps after it."""
    try:
        return fn()
    except Exception:
        import traceback
        log.error(f"{name} failed:\n" + runlog.redact(traceback.format_exc())[-900:])
        return default


def main() -> None:
    log = runlog.start("ingest", "Ingest run")
    db = None
    try:
        db = DB()
        entries, results = stage(log, "Fetching feeds", lambda: pipeline.collect_entries(all_feeds()), ([], []))
        line, warns = feed_report(results)
        log.add(line)
        for w in warns[:12]:
            log.warn("feed down · " + w)
        if len(warns) > 12:
            log.warn(f"…and {len(warns) - 12} more feeds down")
        if results and not any(r.ok for r in results):
            log.error("Every feed failed. Is the runner offline or blocked?")

        stats = stage(log, "Saving new stories", lambda: pipeline.ingest(db, entries))
        if stats:
            log.add(f"Stories seen {stats['seen']} → new {stats['new']} (specialist/regional {stats['niche_new']}), "
                    f"merged with existing {stats['merged']}, already known {stats['dup']}, "
                    f"older than {pipeline.config.ARTICLE_MAX_AGE_HOURS}h skipped {stats['old']}, junk {stats['junk']}")
            if stats["new"]:
                log.mark_activity()

        sc = stage(log, "Scoring", lambda: pipeline.score_pending(db, trends=stage(log, "Trends", trends.fetch_trends, {})))
        if sc:
            log.add(f"Scored {sc['scored']} → {sc['candidates']} candidates "
                    f"({sc['niche_candidates']} specialist), {sc['discarded']} discarded "
                    f"(below minimum {sc['below_min']}, not India {sc['not_india']}, "
                    f"opinion {sc['opinion']}, promo {sc['promo']}), too old {sc['stale']}")
            if sc["backlog"]:
                log.add(f"Backlog: {sc['backlog']} stories wait for the next run")
            for score, title, category, state, outlets in sc["top"][:3]:
                log.add(f"  top: {score} · {category} · {state} · {outlets} outlet(s) · {title[:80]}")
            if sc["errors"]:
                log.error(f"AI scoring failed for {sc['errors']} stories. " + " | ".join(sc["error_samples"]))
        log.add(ai.usage_summary())

        expired = stage(log, "Cleaning up old items", lambda: pipeline.expire_old(db), 0)
        if expired:
            log.add(f"Expired {expired} old items")
        cleaned = stage(log, "Old-data cleanup", lambda: pipeline.cleanup_old(db))
        if cleaned and (cleaned["rows"] or cleaned["images"]):
            log.add(f"Cleanup: removed {cleaned['rows']} stories and {cleaned['images']} images older than "
                    f"{pipeline.config.DATA_RETENTION_HOURS}h (published posts are kept)")
        breaking = stage(log, "Fast-track check", lambda: pipeline.breaking_pass(db), [])
        if breaking:
            log.mark_activity()
        log.add(stage(log, "Queue summary", lambda: pipeline.queue_summary(db), ""))
    except Exception:
        log.crash()
        raise
    finally:
        log.finish(db)


if __name__ == "__main__":
    main()
