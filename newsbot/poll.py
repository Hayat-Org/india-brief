"""Every ~5 min: read Telegram taps (Approve/Reject/Retry) and owner commands.
Reports to Telegram only when something happened (or failed), to avoid noise."""
import time
import traceback

from . import config, pipeline, runlog, telegram
from .db import DB


def handle_updates(db, log, offset: int) -> int:
    for upd in telegram.get_updates(offset):
        try:
            if "callback_query" in upd:
                res = pipeline.handle_callback(db, upd["callback_query"])
                log.add(f"Tap handled: {res}")
            elif "message" in upd:
                res = pipeline.handle_message(db, upd["message"])
                log.add(f"Command handled: {res}")
            log.mark_activity()
        except Exception:
            log.error("Error handling a Telegram action:\n" + traceback.format_exc()[-900:])
        offset = upd["update_id"] + 1
        db.set_setting("tg_offset", str(offset))  # save progress so nothing runs twice
    return offset


def main() -> None:
    """GitHub starts this job only every 5-20 minutes, so each run keeps listening for a few
    minutes (POLL_SECONDS, default 240). A tap on Approve is then handled within seconds."""
    log = runlog.start("poll", "Telegram actions", quiet_unless_activity=True)
    db = None
    try:
        db = DB()
        offset = int(db.get_setting("tg_offset", "0") or 0)
        deadline = time.time() + max(0, config.POLL_SECONDS)
        while True:
            offset = handle_updates(db, log, offset)
            if time.time() + 10 >= deadline:
                break
            time.sleep(10)
    except Exception:
        log.crash()
        raise
    finally:
        # The poll heartbeat is only saved when something happened, to avoid a database write every 5 minutes.
        log.finish(db if log.activity else None)


if __name__ == "__main__":
    main()
