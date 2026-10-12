"""Hourly job: fetch feeds -> dedup -> score -> fast-track breaking news."""
import traceback

from . import pipeline, telegram, trends
from .db import DB
from .sources import all_feeds


def main() -> None:
    db = DB()
    try:
        entries, failed = pipeline.collect_entries(all_feeds())
        stats = pipeline.ingest(db, entries)
        scored = pipeline.score_pending(db, trends=trends.fetch_trends())
        pipeline.expire_old(db)
        breaking = pipeline.breaking_pass(db)
        print({"ingest": stats, "score": scored, "breaking": breaking, "feed_failures": len(failed)})
        if failed:
            print("failed feeds:", failed[:10])
    except Exception:
        telegram.send_text("⚠️ Ingest job crashed:\n" + traceback.format_exc()[-1500:])
        raise


if __name__ == "__main__":
    main()
