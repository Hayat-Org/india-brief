"""Every ~5 min: read Telegram taps (Approve/Reject/Retry) and owner commands.
Reports to Telegram only when something happened (or failed), to avoid noise."""
import traceback

from . import pipeline, runlog, telegram
from .db import DB


def main() -> None:
    log = runlog.start("poll", "Telegram actions", quiet_unless_activity=True)
    db = None
    try:
        db = DB()
        offset = int(db.get_setting("tg_offset", "0") or 0)
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
    except Exception:
        log.crash()
        raise
    finally:
        # The poll heartbeat is only saved when something happened, to avoid a database write every 5 minutes.
        log.finish(db if log.activity else None)


if __name__ == "__main__":
    main()
