# India In Short: automated, neutral, AI-assisted India news desk

Monitors ~65 Indian news sources (national outlets, PIB, fact-checkers, and Google News searches for every state and UT), removes duplicates, scores stories, writes short human-voice summaries, and posts to **Threads, Facebook and Instagram**. Runs entirely free on GitHub Actions + Supabase + Groq/Gemini + Telegram.

**Start here:** [SETUP.md](SETUP.md) · **Before going live:** [COMPLIANCE.md](COMPLIANCE.md) · **Reach playbook:** [GROWTH.md](GROWTH.md)

## Flow

```
 ┌──────────────────────────── hourly (ingest.yml) ────────────────────────────┐
 │  ~60 feeds ─► junk/age filter ─► same-story match ─► corroboration count    │
 │  (national, PIB, fact-checkers,   (title similarity    (distinct outlets    │
 │   Google News per state/topic)     + entity check)      covering it)        │
 │                         │                                                   │
 │                         ▼                                                   │
 │   AI triage (Groq → Gemini failover): significance / impact / interest,     │
 │   category, state, opinion/promo/non-India filter, sensitivity flag         │
 │   combined = 0.35·criticality + 0.30·importance + 0.35·popularity           │
 │   ranking bonus: trending in India (Google Trends), critical, important,    │
 │   multi-outlet confirmation                                                 │
 │         < 5.5 → discarded        ≥ 5.5 → candidate queue                    │
 │                         │                                                   │
 │        score ≥ 8.8 AND ≥ 2 outlets AND daily cap left → fast-track          │
 └─────────────────────────┬───────────────────────────────────────────────────┘
                           ▼
 ┌── 4 India peak slots/day (07:35, 12:35, 18:35, 21:05 IST) + fast-track ─────┐
 │  pick best (score + corroboration bonus − age − category/state repeat)      │
 │  resolve real URL ─► read article text (private context) ─► human-voice    │
 │  draft per platform ─► FACTS-ONLY check ─► sensitive / election gate        │
 │  ─► branded 1080x1350 card ─► upload to public bucket                       │
 │                         │                                                   │
 │        review mode, or anything flagged ─► Telegram: ✅ Approve / ❌ Reject │
 │        auto mode and clean            ─► publish immediately                │
 └─────────────────────────┬───────────────────────────────────────────────────┘
                           ▼
 Threads (text + link)   Facebook Page (text + link)   Instagram (card + caption)
 each in its own try/except: one failure never blocks the others
                           ▼
 22:10 IST: 'Today in short' carousel from the day's vetted posts (digest.yml)
 Telegram report  ·  poll.yml reads taps every 5 min  ·  health.yml daily/weekly
```

## Layout

| Path | Purpose |
|---|---|
| `newsbot/sources.py` | Feed list. Add or remove sources here |
| `newsbot/dedup.py` | Same-story detection (text similarity + entity agreement) |
| `newsbot/ai.py` | Scoring and drafting prompts, Groq/Gemini failover |
| `newsbot/safety.py` | Sensitive-topic gate, facts-only check, loaded-word filter, election silence |
| `newsbot/pipeline.py` | Ingest, scoring, selection, publishing, approvals |
| `newsbot/cards.py` | Branded image cards and digest slides |
| `newsbot/trends.py` | Free popularity signal (Google Trends India) |
| `newsbot/digest.py` | Evening 'Today in short' carousel |
| `newsbot/meta.py` | Threads / Facebook / Instagram publishers |
| `newsbot/telegram.py` | Alerts, buttons, commands |
| `schema.sql` | Database tables + image bucket |
| `.github/workflows/` | Schedules: ingest, publish, poll, health, keepalive |
| `meta_helper.py` | One-time helper to get permanent Page token + IDs |
| `tests/` | Unit + end-to-end tests with fake DB and stubbed APIs |

## Telegram commands

`/status` · `/pause` · `/resume` · `/review` (approve everything) · `/auto` (auto-post clean stories) · `/retract <id>` · `/help`

## Tuning (all optional GitHub Secrets)

`W_SIGNIFICANCE` (0.35) · `W_IMPACT` (0.30) · `W_INTEREST` (0.35) · `TREND_BONUS` · `MAX_POLITICS_PER_DAY` (2) · `USE_GOOGLE_NEWS` (1) · `ATTACH_LINKS` (1) · `MAX_POSTS_PER_DAY` (8) · `MAX_POSTS_PER_SLOT` (2) · `MAX_BREAKING_PER_DAY` (2) · `MIN_SCORE` (5.5) · `BREAKING_SCORE` (8.8) · `REVIEW_EXPIRY_HOURS` (10) · `ELECTION_SILENCE` · `DRY_RUN=1` (log instead of posting)

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
