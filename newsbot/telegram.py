"""Telegram: alerts, approve/reject buttons, and owner commands."""
import requests

from . import config


def _api(method: str, **payload):
    if config.DRY_RUN or not config.TELEGRAM_BOT_TOKEN:
        print(f"[telegram dry-run] {method}: {str(payload)[:300]}")
        return {"ok": True, "result": {}}
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
                      json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data}")
    return data


def send_text(text: str, buttons=None) -> None:
    """Plain-text message(s). `buttons` is an inline keyboard shown on the last chunk."""
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
    for n, chunk in enumerate(chunks):
        extra = {"reply_markup": {"inline_keyboard": buttons}} if buttons and n == len(chunks) - 1 else {}
        _api("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=chunk,
             disable_web_page_preview=True, **extra)


def send_review(article: dict, draft: dict, card_url: str, flags: list) -> None:
    """Card image with Approve/Reject buttons, followed by the full drafts."""
    srcs = ", ".join(s["name"] for s in article.get("sources", [])) or article.get("source", "")
    caption = (
        f"REVIEW #{article['id']}  score {article.get('combined')}  "
        f"{article.get('category')} / {article.get('state')}\n"
        f"Outlets ({article.get('corroboration', 1)}): {srcs}\n"
        + ("\nFLAGS:\n- " + "\n- ".join(flags) + "\n" if flags else "\nNo flags.\n")
    )[:1000]
    _api("sendPhoto", chat_id=config.TELEGRAM_CHAT_ID, photo=card_url, caption=caption,
         reply_markup={"inline_keyboard": [[
             {"text": "✅ Approve & post", "callback_data": f"a:{article['id']}"},
             {"text": "❌ Reject", "callback_data": f"r:{article['id']}"},
         ]]})
    send_text(
        f"#{article['id']} drafts\n\nTHREADS:\n{draft['threads']}\n\n"
        f"FACEBOOK:\n{draft['facebook']}\n\nINSTAGRAM:\n{draft['instagram']}\n\n"
        f"Original: {draft.get('link', article['url'])}"
    )


def answer_callback(callback_id: str, text: str) -> None:
    _api("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])


def clear_buttons(chat_id, message_id) -> None:
    try:
        _api("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
             reply_markup={"inline_keyboard": []})
    except Exception:
        pass


def get_updates(offset: int) -> list:
    if config.DRY_RUN or not config.TELEGRAM_BOT_TOKEN:
        return []
    r = requests.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
                     params={"offset": offset, "timeout": 0, "allowed_updates":
                             '["callback_query","message"]'}, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram getUpdates: {data}")
    return data["result"]


def delete_message(chat_id, message_id) -> None:
    """Used to remove a message that contained a token the moment we have read it."""
    try:
        _api("deleteMessage", chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
