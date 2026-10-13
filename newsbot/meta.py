"""Publishers for Threads, Facebook Page and Instagram (Meta Graph APIs, free)."""
import time

import requests

from . import config

GRAPH = f"https://graph.facebook.com/{config.GRAPH_VERSION}"
THREADS = "https://graph.threads.net/v1.0"


class PublishError(RuntimeError):
    pass


def _check(r: requests.Response, what: str) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 300 or "error" in data:
        err = data.get("error", {})
        raise PublishError(
            f"{what}: HTTP {r.status_code} code={err.get('code')} "
            f"sub={err.get('error_subcode')} {err.get('message', r.text[:200])}"
        )
    return data


# --- Threads ----------------------------------------------------------------
def post_threads(text: str, link, token: str) -> str:
    data = {"media_type": "TEXT", "text": text, "access_token": token}
    if link:
        data["link_attachment"] = link
    r = requests.post(f"{THREADS}/{config.THREADS_USER_ID}/threads", timeout=30, data=data)
    creation_id = _check(r, "threads container")["id"]
    time.sleep(5)
    r = requests.post(f"{THREADS}/{config.THREADS_USER_ID}/threads_publish", timeout=30, data={
        "creation_id": creation_id, "access_token": token,
    })
    return _check(r, "threads publish")["id"]


def refresh_threads_token(token: str) -> dict:
    r = requests.get("https://graph.threads.net/refresh_access_token", timeout=30, params={
        "grant_type": "th_refresh_token", "access_token": token,
    })
    return _check(r, "threads token refresh")  # {access_token, expires_in}


# --- Facebook Page ------------------------------------------------------------
def post_facebook(text: str, link=None) -> str:
    data = {"message": text, "access_token": config.FB_PAGE_TOKEN}
    if link:
        data["link"] = link
    r = requests.post(f"{GRAPH}/{config.FB_PAGE_ID}/feed", timeout=30, data=data)
    return _check(r, "facebook feed")["id"]


def delete_facebook(post_id: str) -> None:
    """Used by /retract. (Instagram's API cannot delete posts; Threads must be removed by hand.)"""
    r = requests.delete(f"{GRAPH}/{post_id}", timeout=30,
                        params={"access_token": config.FB_PAGE_TOKEN})
    _check(r, "facebook delete")


# --- Instagram ----------------------------------------------------------------
def _wait_container(container: str) -> None:
    for _ in range(12):  # wait up to ~60 s for processing
        s = requests.get(f"{GRAPH}/{container}", timeout=30, params={
            "fields": "status_code", "access_token": config.IG_TOKEN,
        })
        status = _check(s, "instagram status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"instagram container status {status}")
        time.sleep(5)


def _ig_create(data: dict, what: str) -> str:
    """Create a container; if alt_text is rejected, retry once without it."""
    payload = {**data, "access_token": config.IG_TOKEN}
    r = requests.post(f"{GRAPH}/{config.IG_USER_ID}/media", timeout=30, data=payload)
    try:
        return _check(r, what)["id"]
    except PublishError:
        if "alt_text" not in payload:
            raise
        payload.pop("alt_text")
        r = requests.post(f"{GRAPH}/{config.IG_USER_ID}/media", timeout=30, data=payload)
        return _check(r, what)["id"]


def _ig_publish(container: str) -> str:
    r = requests.post(f"{GRAPH}/{config.IG_USER_ID}/media_publish", timeout=30, data={
        "creation_id": container, "access_token": config.IG_TOKEN,
    })
    return _check(r, "instagram publish")["id"]


def post_instagram(caption: str, image_url: str, alt_text: str = "") -> str:
    data = {"image_url": image_url, "caption": caption}
    if alt_text:
        data["alt_text"] = alt_text[:900]
    container = _ig_create(data, "instagram container")
    _wait_container(container)
    return _ig_publish(container)


def post_instagram_carousel(caption: str, image_urls: list, alt_texts: list = None) -> str:
    """2-10 JPEG slides as one swipeable post (carousels count as one API post)."""
    children = []
    for n, url in enumerate(image_urls[:10]):
        data = {"image_url": url, "is_carousel_item": "true"}
        if alt_texts and n < len(alt_texts) and alt_texts[n]:
            data["alt_text"] = alt_texts[n][:900]
        children.append(_ig_create(data, f"instagram carousel item {n + 1}"))
    for c in children:
        _wait_container(c)
    container = _ig_create({"media_type": "CAROUSEL", "children": ",".join(children),
                            "caption": caption}, "instagram carousel container")
    _wait_container(container)
    return _ig_publish(container)


# --- Plain-English hints for errors ------------------------------------------------
def explain_error(message: str) -> str:
    """Turn a Meta/Threads error into a likely cause and fix. These are hints, not certainties."""
    import re
    m = str(message or "")
    code = re.search(r"code=(\d+)", m)
    sub = re.search(r"sub=(\d+)", m)
    code, sub = (code.group(1) if code else ""), (sub.group(1) if sub else "")
    low = m.lower()
    if code == "190" and sub == "459":
        return ("Facebook wants a security check on the account that owns the Page. Log in at facebook.com "
                "with that account, complete the check, then tap Retry.")
    if code == "190" or code == "102" or "expired" in low or "invalid oauth" in low:
        return ("The access token has expired or was invalidated (a password change does this). "
                "Create a new token (SETUP.md step 9) and update the GitHub secret.")
    if code in ("10", "200", "299", "3") or "permission" in low:
        return ("The token is missing a permission (pages_manage_posts, instagram_content_publish or "
                "threads_content_publish). Regenerate it with those ticked.")
    if sub == "2207042" or (code == "9" and "limit" in low):
        return "Instagram's daily publishing limit was reached. It resets within 24 hours; check for repeated manual runs."
    if code in ("4", "17", "32", "341", "613") or "rate limit" in low or "too many" in low:
        return "Rate limit hit. Wait a while and tap Retry. If it repeats, lower MAX_POSTS_PER_DAY."
    if code == "368":
        return "Facebook temporarily blocked posting (anti-spam on new Pages). Slow down, wait a day, keep posts original."
    if sub in ("2207003", "2207006", "2207026", "2207027") or "image_url" in low or "media" in low and "download" in low:
        return ("Instagram could not fetch or accept the image. Check that the Supabase 'cards' bucket is public "
                "(Storage → cards → Public) and that the image is a JPEG.")
    if sub == "2207050" or "restricted" in low:
        return "The Instagram account appears restricted. Open the Instagram app and look for a notice."
    if code == "100" and sub == "33":
        return ("This ID does not exist or the token cannot see it. For Instagram, IG_USER_ID is probably wrong "
                "(it must be the Instagram account's ID, not the Page ID).")
    if code == "100":
        return "A parameter was rejected (often a wrong ID, image URL or text). See the message above."
    if "timed out" in low or "timeout" in low or "connection" in low:
        return "Network hiccup. Tap Retry."
    if re.search(r"http 5\d\d", low):
        return "Meta had a temporary problem. Tap Retry in a few minutes."
    return ""


def instagram_link_check() -> dict:
    """Which Instagram account is actually linked to the Facebook Page?
    Returns {"id": ..., "username": ...} (both None when nothing is linked)."""
    r = requests.get(f"{GRAPH}/{config.FB_PAGE_ID}", timeout=20, params={
        "fields": "instagram_business_account{id,username}", "access_token": config.FB_PAGE_TOKEN})
    ig = _check(r, "page→instagram").get("instagram_business_account") or {}
    return {"id": ig.get("id"), "username": ig.get("username")}


# --- Health checks --------------------------------------------------------------
def check_tokens(threads_token: str) -> dict:
    """Return {platform: status text}. A status starting with 'ok' is healthy."""
    out = {}
    if config.THREADS_USER_ID:
        try:
            r = requests.get(f"{THREADS}/me", timeout=20,
                             params={"fields": "username", "access_token": threads_token})
            out["threads"] = "ok (@%s)" % _check(r, "threads me").get("username")
        except Exception as ex:
            out["threads"] = _with_hint(ex)
    if config.FB_PAGE_ID and config.FB_PAGE_TOKEN:
        try:
            r = requests.get(f"{GRAPH}/{config.FB_PAGE_ID}", timeout=20,
                             params={"fields": "name", "access_token": config.FB_PAGE_TOKEN})
            out["facebook"] = "ok (%s)" % _check(r, "facebook page").get("name")
        except Exception as ex:
            out["facebook"] = _with_hint(ex)
    if config.IG_USER_ID and config.IG_TOKEN:
        out["instagram"] = _check_instagram()
    elif config.FB_PAGE_ID and config.FB_PAGE_TOKEN:
        out["instagram"] = "not configured (IG_USER_ID missing)"
    return out


def _with_hint(ex: Exception) -> str:
    msg = str(ex)[:200]
    hint = explain_error(msg)
    return msg + (f"\n   Likely fix: {hint}" if hint else "")


def _check_instagram() -> str:
    try:
        r = requests.get(f"{GRAPH}/{config.IG_USER_ID}", timeout=20,
                         params={"fields": "username", "access_token": config.IG_TOKEN})
        username = _check(r, "instagram me").get("username")
    except Exception as ex:
        return _with_hint(ex)
    linked = {}
    if config.FB_PAGE_ID and config.FB_PAGE_TOKEN:
        try:
            linked = instagram_link_check()
        except Exception:
            linked = {}
    if linked.get("id") and linked["id"] != config.IG_USER_ID:
        return (f"IG_USER_ID is wrong. The Instagram account linked to your Page is @{linked.get('username')} "
                f"with ID {linked['id']}. Set the GitHub secret IG_USER_ID to {linked['id']}.")
    if not username:
        if linked.get("id") is None and config.FB_PAGE_ID:
            return ("The ID answered but is not an Instagram account, and no Instagram account is linked to your "
                    "Facebook Page. Link your professional Instagram account to the Page, then put its ID "
                    "(from meta_helper.py) in IG_USER_ID.")
        return "The ID answered but returned no username, so it is probably not an Instagram account ID."
    return f"ok (@{username})"
