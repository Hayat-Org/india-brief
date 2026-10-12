"""Thin Supabase (PostgREST) client. Tests swap this for an in-memory fake."""
from datetime import datetime, timedelta, timezone

import requests

from . import config


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class DB:
    def __init__(self, url: str = None, key: str = None):
        self.base = (url or config.SUPABASE_URL) + "/rest/v1"
        self.headers = {**config.supabase_auth_headers(key), "Content-Type": "application/json"}

    def _req(self, method, path, params=None, json=None, prefer=None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        r = requests.request(method, self.base + path, headers=headers,
                             params=params, json=json, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    # --- articles ---------------------------------------------------------
    def recent_articles(self, hours: int = 72) -> list:
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        return self._req("GET", "/articles", params={
            "select": "id,url,title,title_norm,source,sources,status,corroboration,created_at",
            "created_at": f"gte.{since}",
            "order": "created_at.desc",
            "limit": "1000",
        })

    def insert_article(self, row: dict) -> dict:
        return self._req("POST", "/articles", json=row, prefer="return=representation")[0]

    def update_article(self, article_id: int, values: dict) -> None:
        values = dict(values)
        values["updated_at"] = _iso(datetime.now(timezone.utc))
        self._req("PATCH", "/articles", params={"id": f"eq.{article_id}"}, json=values)

    def get_article(self, article_id: int):
        rows = self._req("GET", "/articles", params={"id": f"eq.{article_id}", "select": "*"})
        return rows[0] if rows else None

    def list_by_status(self, status: str, limit: int = 500) -> list:
        return self._req("GET", "/articles", params={
            "status": f"eq.{status}", "select": "*",
            "order": "created_at.desc", "limit": str(limit),
        })

    def list_posted_since(self, since_iso: str) -> list:
        return self._req("GET", "/articles", params={
            "status": "eq.published", "posted_at": f"gte.{since_iso}",
            "select": "*",
        })

    # --- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str = ""):
        rows = self._req("GET", "/settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._req("POST", "/settings", json={"key": key, "value": value,
                                             "updated_at": _iso(datetime.now(timezone.utc))},
                  prefer="resolution=merge-duplicates")
