"""Every ~5 min: read Telegram taps (Approve/Reject) and owner commands."""
import traceback

from . import pipeline, telegram
from .db import DB


def main() -> None:
    db = DB()
    offset = int(db.get_setting("tg_offset", "0") or 0)
    try:
        for upd in telegram.get_updates(offset):
            try:
                if "callback_query" in upd:
                    print("callback:", pipeline.handle_callback(db, upd["callback_query"]))
                elif "message" in upd:
                    print("message:", pipeline.handle_message(db, upd["message"]))
            except Exception:
                telegram.send_text("⚠️ Error handling a Telegram action:\n" + traceback.format_exc()[-1200:])
            offset = upd["update_id"] + 1
            db.set_setting("tg_offset", str(offset))  # save progress so nothing runs twice
    except Exception:
        print(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
