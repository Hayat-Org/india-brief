"""One-click test of Pixabay + Stories: python -m newsbot.storytest (GitHub -> Actions -> Story test -> Run workflow).
Fetches a Pixabay photo, builds an India In Short promo Story (1080x1920), posts it to Instagram and Facebook
Stories and reports every step to Telegram and the run-errors issue."""
import time

from . import cards, config, media, meta, photos, pipeline, runlog
from .db import DB

QUERIES = ["india gate new delhi", "gateway of india mumbai", "taj mahal agra", "india landscape", "india flag"]


def pixabay_photo(log):
    if not config.PIXABAY_API_KEY:
        log.error("PIXABAY_API_KEY is empty in this workflow. Add it under Settings > Secrets and variables > Actions.")
        return None
    for q in QUERIES:
        try:
            hit = photos.pixabay(q)
            if hit:
                hit["image"] = photos.download(hit["url"])
                log.add(f"Pixabay OK: '{q}' -> photo by {hit['credit']} ({hit['image'].size[0]}x{hit['image'].size[1]})")
                return hit
            log.warn(f"Pixabay: no usable photo for '{q}'")
        except Exception as ex:
            log.error(f"Pixabay request failed for '{q}': {str(ex)[:200]}")
            if "400" in str(ex) or "401" in str(ex) or "403" in str(ex):
                log.error("Pixabay rejected the key. Re-copy it from pixabay.com/api/docs (no spaces).")
                return None
    return None


def main() -> None:
    log = runlog.start("storytest", "Story + Pixabay test")
    db = None
    try:
        db = DB()
        pipeline.load_runtime_tokens(db)
        hit = pixabay_photo(log)
        story = cards.render_story("India's news in short, in plain words.", "", "National",
                                   hit["image"] if hit else None, kicker="WELCOME · INDIA IN SHORT")
        url = media.upload_card(story, f"promo-story-{int(time.time())}.jpg")
        log.add(f"Story image uploaded: {url}")
        for name, fn in (("Instagram", meta.post_instagram_story), ("Facebook", meta.post_facebook_story)):
            try:
                log.add(f"{name} Story posted ✅ id={fn(url)}")
            except Exception as ex:
                hint = meta.explain_error(str(ex))
                log.error(f"{name} Story failed: {str(ex)[:260]}" + (f" -> {hint}" if hint else ""))
    except Exception:
        log.crash()
        raise
    finally:
        log.finish(db)


if __name__ == "__main__":
    main()
