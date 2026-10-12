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


# --- Health checks --------------------------------------------------------------
def check_tokens(threads_token: str) -> dict:
    """Return {platform: 'ok' | error text} for every configured platform."""
    out = {}
    if config.THREADS_USER_ID:
        try:
            r = requests.get(f"{THREADS}/me", timeout=20,
                             params={"fields": "username", "access_token": threads_token})
            out["threads"] = "ok (@%s)" % _check(r, "threads me").get("username")
        except Exception as ex:
            out["threads"] = str(ex)[:200]
    if config.FB_PAGE_ID and config.FB_PAGE_TOKEN:
        try:
            r = requests.get(f"{GRAPH}/{config.FB_PAGE_ID}", timeout=20,
                             params={"fields": "name", "access_token": config.FB_PAGE_TOKEN})
            out["facebook"] = "ok (%s)" % _check(r, "facebook page").get("name")
        except Exception as ex:
            out["facebook"] = str(ex)[:200]
    if config.IG_USER_ID and config.IG_TOKEN:
        try:
            r = requests.get(f"{GRAPH}/{config.IG_USER_ID}", timeout=20,
                             params={"fields": "username", "access_token": config.IG_TOKEN})
            out["instagram"] = "ok (@%s)" % _check(r, "instagram me").get("username")
        except Exception as ex:
            out["instagram"] = str(ex)[:200]
    return out
