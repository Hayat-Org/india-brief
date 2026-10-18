"""Per-run report sent to Telegram, plus job heartbeats stored in the database.

Every job (ingest, publish, digest, health, poll) opens a RunLog, other modules add notes
through the module-level helpers (no-ops when no run is open, so tests stay simple), and the
job ends with `finish()` which sends one plain-text report and records a heartbeat.
"""
import hashlib
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone

from . import config, telegram

_current = None
REPEAT_QUIET_HOURS = 6  # an identical failure is reported once, then at most every 6 hours

SECRET_ENV = ("SUPABASE_SERVICE_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "THREADS_ACCESS_TOKEN",
              "FB_PAGE_TOKEN", "IG_TOKEN", "PEXELS_API_KEY", "SUPABASE_URL")


def redact(text: str) -> str:
    """Remove secrets from text before it is shown anywhere."""
    text = str(text)
    for name in SECRET_ENV:
        value = os.environ.get(name, "").strip()
        if len(value) >= 6:
            text = text.replace(value, f"<{name}>")
    text = re.sub(r"bot\d{6,}:[A-Za-z0-9_-]{20,}", "bot<token>", text)
    text = re.sub(r"(access_token|key|apikey|token)=[^&\s'\"]+", r"\1=<hidden>", text)
    return text

JOBS = ("ingest", "publish", "digest", "health", "poll")


class RunLog:
    def __init__(self, job: str, title: str, quiet_unless_activity: bool = False):
        self.job, self.title = job, title
        self.quiet_unless_activity = quiet_unless_activity  # used by the 5-minute poll job
        self.t0 = time.time()
        self.lines, self.warnings, self.errors = [], [], []
        self.activity = False

    # --- collecting -------------------------------------------------------
    def add(self, text: str) -> None:
        self.lines.append(str(text))

    def warn(self, text: str) -> None:
        self.warnings.append(str(text))

    def error(self, text: str) -> None:
        self.errors.append(str(text))

    def mark_activity(self) -> None:
        self.activity = True

    def crash(self) -> None:
        self.error("Job crashed:\n" + redact(traceback.format_exc())[-1500:])

    # --- rendering --------------------------------------------------------
    @staticmethod
    def trigger() -> str:
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        return {"schedule": "scheduled", "workflow_dispatch": "manual run"}.get(event, event or "local")

    @staticmethod
    def run_url() -> str:
        server, repo, run = (os.environ.get(k, "") for k in
                             ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
        return f"{server}/{repo}/actions/runs/{run}" if repo and run else ""

    def icon(self) -> str:
        return "❌" if self.errors else ("⚠️" if self.warnings else "✅")

    def render(self) -> str:
        secs = time.time() - self.t0
        head = [f"{self.icon()} {self.title}", f"{self.trigger()} · {secs:.0f}s"
                + (f"\n{self.run_url()}" if self.run_url() else "")]
        out = ["\n".join(head), ""]
        out += self.lines
        if self.warnings:
            out += ["", f"⚠️ Warnings ({len(self.warnings)})"] + [f"• {w}" for w in self.warnings[:15]]
            if len(self.warnings) > 15:
                out.append(f"• …and {len(self.warnings) - 15} more")
        if self.errors:
            out += ["", f"❌ Errors ({len(self.errors)})"] + [f"• {e}" for e in self.errors[:10]]
        return "\n".join(out).strip()

    def signature(self) -> str:
        first = re.sub(r"\d+", "#", (self.errors[0] if self.errors else "")[:200])
        return hashlib.md5(first.encode()).hexdigest()[:10]

    def is_repeat(self, db) -> bool:
        """True when the same error was already reported to you in the last few hours."""
        if not self.errors or db is None:
            return False
        try:
            prev = json.loads(db.get_setting(f"lasterr_{self.job}", "") or "{}")
            fresh = prev.get("sig") == self.signature() and \
                (datetime.now(timezone.utc) - datetime.fromisoformat(prev["at"])).total_seconds() < REPEAT_QUIET_HOURS * 3600
            if not fresh:
                db.set_setting(f"lasterr_{self.job}", json.dumps(
                    {"sig": self.signature(), "at": datetime.now(timezone.utc).isoformat()}))
            return bool(fresh)
        except Exception:
            return False

    def should_send(self) -> bool:
        if self.errors:
            return True
        if self.quiet_unless_activity:
            return self.activity
        if config.REPORT_LEVEL == "important":
            return self.activity
        return True

    def finish(self, db=None) -> None:
        global _current
        try:
            if db is not None:
                save_heartbeat(db, self.job, not self.errors, (self.lines[0] if self.lines else "")[:200])
        except Exception as ex:  # a heartbeat problem must never break the job
            print(f"heartbeat not saved: {str(ex)[:120]}")
        try:
            text = redact(self.render())
            print(text)  # also lands in the GitHub Actions log
            if self.errors and os.environ.get("GITHUB_ACTIONS"):  # lets the workflow's failure step know what happened
                with open("crash_report.txt", "w") as f:
                    f.write(text[-6000:])
            if self.is_repeat(db) and not self.activity:
                print("same error as the last report, not repeating it on Telegram")
            elif self.should_send():
                telegram.send_text(text)
                open(".reported", "w").close()
        except Exception as ex:
            print(f"report not sent: {redact(str(ex))[:160]}")
        if _current is self:
            _current = None


# --- module-level helpers ---------------------------------------------------
def start(job: str, title: str, quiet_unless_activity: bool = False) -> RunLog:
    global _current
    _current = RunLog(job, title, quiet_unless_activity)
    return _current


def current():
    return _current


def note(text: str) -> None:
    if _current:
        _current.add(text)


def warn(text: str) -> None:
    if _current:
        _current.warn(text)


def error(text: str) -> None:
    if _current:
        _current.error(text)


def activity() -> None:
    if _current:
        _current.mark_activity()


# --- heartbeats ----------------------------------------------------------------
def save_heartbeat(db, job: str, ok: bool, summary: str) -> None:
    db.set_setting(f"last_{job}", json.dumps({
        "at": datetime.now(timezone.utc).isoformat(), "ok": bool(ok), "summary": summary}))


def load_heartbeats(db) -> dict:
    out = {}
    for job in JOBS:
        try:
            raw = db.get_setting(f"last_{job}", "")
            out[job] = json.loads(raw) if raw else None
        except Exception:
            out[job] = None
    return out


def ago(iso_ts: str, now: datetime = None) -> str:
    try:
        now = now or datetime.now(timezone.utc)
        secs = (now - datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))).total_seconds()
    except Exception:
        return "unknown"
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs / 60:.0f} min ago"
    if secs < 172800:
        return f"{secs / 3600:.1f} h ago"
    return f"{secs / 86400:.1f} days ago"


def heartbeat_lines(db, now: datetime = None) -> list:
    lines = []
    for job, hb in load_heartbeats(db).items():
        if hb:
            lines.append(f"{'✅' if hb.get('ok') else '❌'} {job}: {ago(hb['at'], now)}"
                         + (f" ({hb['summary']})" if hb.get("summary") else ""))
        else:
            lines.append(f"⏳ {job}: has not reported yet")
    return lines
