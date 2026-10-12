"""Scheduled slot job (4x/day): pick the best stories and draft/post/send for review.
   python -m newsbot.publish          -> normal slot
   python -m newsbot.publish digest   -> evening 'Today in short' digest
"""
import sys
import traceback

from . import digest, pipeline, telegram
from .db import DB


def main() -> None:
    db = DB()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "digest":
            print(digest.run_digest(db))
        else:
            print(pipeline.run_slot(db))
    except Exception:
        telegram.send_text("⚠️ Publish job crashed:\n" + traceback.format_exc()[-1500:])
        raise


if __name__ == "__main__":
    main()
