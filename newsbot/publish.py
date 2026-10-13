"""Scheduled slot job (4x/day): pick the best stories and draft/post/send for review.
   python -m newsbot.publish          -> normal slot
   python -m newsbot.publish digest   -> evening 'Today in short' digest
Every run sends a Telegram report, including when nothing was posted (with the reason).
"""
import sys

from . import ai, digest, pipeline, runlog
from .db import DB


def main() -> None:
    is_digest = len(sys.argv) > 1 and sys.argv[1] == "digest"
    log = runlog.start("digest" if is_digest else "publish",
                       "Evening digest" if is_digest else "Publish slot")
    db = None
    try:
        db = DB()
        if is_digest:
            result = digest.run_digest(db)
            explain = {"paused": "Paused, nothing sent.", "exists": "Today's digest already exists.",
                       "too_few": f"Fewer than {pipeline.config.DIGEST_MIN_STORIES} stories were posted today, so no digest."}
            log.add(f"Digest result: {result if isinstance(result, str) and result not in explain else explain.get(result, result)}")
            if result not in ("paused", "exists", "too_few"):
                log.mark_activity()
        else:
            result = pipeline.run_slot(db)
            if result.get("outcomes"):
                log.mark_activity()
                for o in result["outcomes"]:
                    log.add(f"Outcome: {o}")
            if ai.USAGE["ok"] or ai.USAGE["fail"]:
                log.add(ai.usage_summary())
        log.add(pipeline.queue_summary(db))
    except Exception:
        log.crash()
        raise
    finally:
        log.finish(db)


if __name__ == "__main__":
    main()
