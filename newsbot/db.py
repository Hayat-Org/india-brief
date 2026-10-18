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

    def insert_article(self, row: dict):
        """Insert a story. Returns the new row, or None when that URL is already stored
        (the feeds keep offering old links, which must never crash a run)."""
        rows = self._req("POST", "/articles", params={"on_conflict": "url"}, json=row,
                         prefer="resolution=ignore-duplicates,return=representation")
        return rows[0] if rows else None

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

    def status_counts(self, hours: int = 72) -> dict:
        """{status: number of articles} for stories first seen in the last `hours`."""
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        rows = self._req("GET", "/articles", params={
            "select": "status", "created_at": f"gte.{since}", "limit": "5000"})
        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return counts

    # --- cleanup ----------------------------------------------------------
    def old_with_cards(self, cutoff_iso: str, limit: int = 300) -> list:
        """Stories created before the cutoff that still point at images in the storage bucket."""
        return self._req("GET", "/articles", params={
            "select": "id,card_url,draft,status", "created_at": f"lt.{cutoff_iso}",
            "card_url": "not.is.null", "order": "created_at.asc", "limit": str(limit)})

    def purge_old(self, cutoff_iso: str, keep_status: str = "published") -> int:
        """Delete stories created before the cutoff, except published posts (the audit trail)."""
        rows = self._req("DELETE", "/articles", params={
            "created_at": f"lt.{cutoff_iso}", "status": f"neq.{keep_status}", "select": "id"},
            prefer="return=representation")
        return len(rows or [])

    # --- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str = ""):
        rows = self._req("GET", "/settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._req("POST", "/settings", json={"key": key, "value": value,
                                             "updated_at": _iso(datetime.now(timezone.utc))},
                  prefer="resolution=merge-duplicates")
