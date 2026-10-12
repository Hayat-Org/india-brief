"""In-memory stand-in for newsbot.db.DB."""
from datetime import datetime, timezone


class FakeDB:
    def __init__(self):
        self.rows, self.settings, self._id = {}, {}, 0

    def recent_articles(self, hours=72):
        return [dict(r) for r in sorted(self.rows.values(), key=lambda r: -r["id"])]

    def insert_article(self, row):
        self._id += 1
        full = {"id": self._id, "created_at": datetime.now(timezone.utc).isoformat(),
                "attempts": 0, "corroboration": 1, **row}
        self.rows[self._id] = full
        return dict(full)

    def update_article(self, article_id, values):
        self.rows[article_id].update(values)

    def get_article(self, article_id):
        r = self.rows.get(article_id)
        return dict(r) if r else None

    def list_by_status(self, status, limit=500):
        return [dict(r) for r in self.rows.values() if r["status"] == status][:limit]

    def list_posted_since(self, since_iso):
        return [dict(r) for r in self.rows.values()
                if r["status"] == "published" and (r.get("posted_at") or "") >= since_iso]

    def get_setting(self, key, default=""):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value
