# Setup guide: from zero to a running India news desk

Everything here is free. Plan on 60-90 minutes, mostly Meta account setup.
**Read [COMPLIANCE.md](COMPLIANCE.md) before the first public post** (30 minutes, includes paste-ready bio and corrections text).
**Never paste passwords or tokens into any chat, including with me.** Tokens go only into GitHub Secrets.

Platform dashboards change their wording often. If a label differs from what I wrote, look for the closest match, or send me a screenshot (with any tokens blurred).

---

## 0. Names (confirmed)

| | |
|---|---|
| Display name | **India In Short** |
| Handle (same on Instagram, Threads, Facebook Page username) | **`@indiainshort.daily`** |
| Gmail for all sign-ups | `indiainshort.desk@gmail.com` (add a number if taken) |

Backups if the handle is taken on one platform: `@indiainshort.news`, `@indiainshortdaily`. Keep the **display name** identical everywhere.
Avoid "official", "PIB", "gov" or anything implying a government body.
The defaults already say India In Short, so you don't need to set `BRAND_NAME` / `BRAND_HANDLE` unless you change them.

**Bio (all platforms):** `Daily India news in plain words. Neutral · sourced · AI-assisted. Corrections: indiainshort.desk@gmail.com`

---

## 1. Accounts, in this order

### 1. Gmail
Create the new Gmail. Turn on 2-step verification (use your phone). Save the recovery codes offline.

### 2. GitHub (free, **public** repo)
Public repos get unlimited free Actions minutes. Secrets stay hidden even in a public repo.
1. Sign up with the new Gmail, then create a repo named `india-brief` (Public).
2. Upload this project. Easiest reliable way (the `.github` folder is hidden and web-upload often skips it):
   ```bash
   cd india-brief
   git init -b main
   git add -A
   git commit -m "Initial commit"
   git remote add origin https://github.com/<your-username>/india-brief.git
   git push -u origin main
   ```
   (GitHub Desktop works too. Or open a Codespace on the repo and drag the files in.)
3. Repo → **Settings → Actions → General → Workflow permissions** → choose **Read and write** (needed by the keepalive job).

### 3. Supabase (database + image storage)
1. supabase.com → sign in with GitHub → **New project** (free plan, pick the Mumbai/Singapore region).
2. **SQL Editor → New query** → paste all of `schema.sql` → **Run**. This creates the tables and the public `cards` bucket.
3. **Project Settings → API**: copy the **Project URL** (`SUPABASE_URL`) and the **service_role** key (`SUPABASE_SERVICE_KEY`).
   Newer projects may show "Secret keys" (`sb_secret_…`) instead. That works too.
   This key is powerful: it only ever goes into GitHub Secrets.

### 4. AI keys (both free)
- **Groq:** console.groq.com → API Keys → create → `GROQ_API_KEY`.
- **Gemini:** aistudio.google.com → Get API key → `GEMINI_API_KEY`. (Failover if Groq is rate-limited.)

### 5. Telegram (your control room)
1. In Telegram, open **@BotFather** → `/newbot` → follow prompts → copy the token (`TELEGRAM_BOT_TOKEN`).
2. Open your new bot and press **Start**, then send it any message.
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and find `"chat":{"id":123456789` → that number is `TELEGRAM_CHAT_ID`.
   (Do this *before* the bot is running in GitHub, otherwise the poller consumes the update.)

### 6. Facebook Page
Facebook has no "brand-only" personal account, so this uses a real personal Facebook account of yours, but the **Page** is the brand and your personal profile is not shown on it.
1. Facebook → **Create → Page**. Name: your display name. Category: *News & media website* (or *Media/news company*).
2. Add profile photo (logo) and cover. Add the bio above.

### 7. Instagram
1. Create the account with the new Gmail and your chosen handle.
2. **Settings → Account type → Switch to Professional** (Creator or Business, either works). Category: News & media.
3. Link it to the Page: Instagram **Settings → Accounts Center / Linked accounts**, or Facebook Page **Settings → Linked accounts → Instagram**.

### 8. Threads
Open the Threads app and sign in with the Instagram account. Set the bio.

### 9. Meta developer app (the fiddly part, free)
1. developers.facebook.com → log in with the Facebook account that owns the Page → **Get started / Register** as a developer.
2. **Create app** → name `India In Short Publisher` → choose the "Other / Business" style app type (the wording varies).
3. Add these capabilities/use cases to the app: **Facebook Login for Business (or Facebook Login)**, **Instagram API with Facebook Login**, and **Access the Threads API**.
4. Keep the app in **Development mode**. You do **not** need App Review because you are only posting to your own Page and accounts with your own developer role.

**Facebook + Instagram token (permanent Page token):**
1. **Tools → Graph API Explorer** → select your app → **Generate Access Token**, tick these permissions:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`
   and select your Page when asked.
2. Copy the short-lived token. On your own computer run `python meta_helper.py` (included). Give it your App ID, App Secret (App settings → Basic) and that token.
3. It prints `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID`. A Page token made from a long-lived user token does not expire.

**Threads token:**
1. In the app, open the **Threads API** use case → add permissions `threads_basic`, `threads_content_publish`.
2. **App roles → Roles → Add People → Threads Tester** → enter your Threads username. Then in the Threads app: **Settings → Account → Website permissions → Invites** → accept.
3. Back in the Threads API settings, use the **User Token Generator** for your tester → copy the token (`THREADS_ACCESS_TOKEN`) and the user ID (`THREADS_USER_ID`).
4. The bot refreshes this token every week and stores the fresh copy in the database, so you set it once.

---

### Optional: Pixabay key (more photo choice)
Pexels has paused new API keys, and Wikimedia Commons plus Openverse already work without any key. For more choice, sign up free at pixabay.com, open pixabay.com/api/docs and copy your key, then add it as the GitHub secret `PIXABAY_API_KEY`.

## 2. GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add each of these (names must match exactly):

| Secret | Where it comes from |
|---|---|
| `SUPABASE_URL` | Supabase API settings |
| `SUPABASE_SERVICE_KEY` | Supabase API settings |
| `GROQ_API_KEY` | Groq console |
| `GEMINI_API_KEY` | Google AI Studio |
| `TELEGRAM_BOT_TOKEN` | BotFather |
| `TELEGRAM_CHAT_ID` | getUpdates (step 5) |
| `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID` | `meta_helper.py` |
| `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` | Threads token generator |
| `BRAND_NAME`, `BRAND_HANDLE` *(optional)* | defaults: `India In Short`, `@indiainshort.daily` |
| `USE_GOOGLE_NEWS`, `ATTACH_LINKS`, `MAX_POSTS_PER_DAY`, `MAX_POLITICS_PER_DAY` *(optional)* | tuning knobs, see README. Leave unset for the defaults |
| `ELECTION_SILENCE` *(add whenever an election is announced)* | e.g. `[["2026-11-01","2026-11-03"]]` (IST dates; political posts are held) |

You can go live one platform at a time. Any platform whose secrets are missing is skipped, not an error.

---

## 3. First run (auto mode)

1. Repo → **Actions** tab → enable workflows if asked.
2. Run **Health checks** manually with mode `weekly`. Telegram should report each platform token as ✅ and any dead feeds.
3. Run **Ingest news (hourly)** manually. Check the log: it prints counts of new, merged, scored items.
4. Run **Publish slot** manually. Within a minute you should get a review card in Telegram with ✅ Approve / ❌ Reject.
5. Tap Approve. The **Telegram approvals** job runs every ~5 minutes (GitHub can delay it to 10-15), then posts to all three platforms and reports back.
6. Look at the posts on the real accounts. Tell me anything that reads off: tone, length, wrong categories.

The system now posts by itself (auto mode). To go back to approving every post first, send `/review` to your bot. In auto mode:
- clean stories post by themselves,
- sensitive, flagged or unverified-looking stories are **skipped, never posted**.

Send `/review` any time to go back, `/pause` to stop everything, `/status` to see the queue, and `/retract <id>` to take down a post that turned out wrong.

Every evening (~22:10 IST) you'll also get a **Today in short** carousel to approve (it only uses stories already posted that day). See [GROWTH.md](GROWTH.md) for the reach playbook.

---

## 4. How it protects you

- **Cross-source check:** a story is "confirmed" when 2+ different outlets carry it. Single-source stories are posted as "Developing" and say so.
- **Facts-only check:** every number and name in the post must appear in the source text, or the post is held.
- **Sensitive gate:** communal/religious flashpoints, sexual offences, minors, suicide, terror, mob violence, custodial/encounter deaths, contempt/sub judice, exit/opinion polls, election-silence windows and **all crime stories** → always held for you.
- **Neutral voice rules:** attribution, "alleged/accused", both sides' stated positions, no loaded words, no guessing motives. Fact-checks cite the fact-checker.
- **Own wording + credit:** we summarise in our own words, name the outlets, and link to the original. We don't copy articles or publisher photos.
- **Caps:** at most 8 posts/day (+1 evening digest), 2 per slot, 2 breaking/day, 2 Politics/day.

---

## 5. Honest limits

- GitHub's schedules are not exact: runs can start 5-20 minutes late. "Breaking" here means minutes, not seconds.
- GitHub disables schedules after 60 days of repo inactivity. The keepalive job prevents that, and the daily Telegram summary is your alarm: **if it stops arriving, open the Actions tab.**
- Google News links are unwrapped on a best-effort basis; a few may fail and fall back to the Google link.
- Instagram captions can't contain clickable links; the caption points readers to the original publisher.
- Model names and Meta API versions change. If the AI or Meta calls start failing after a deprecation, set `GROQ_FAST_MODEL`, `GROQ_QUALITY_MODEL` or `GEMINI_MODELS` (comma-separated lists, tried in order) or `GRAPH_VERSION` as secrets, or paste me the error.
- You are the publisher. The safeguards reduce risk but cannot remove it, and this is not legal advice. Keep reviewing for the first weeks and handle corrections quickly.

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Health check says token error `code=190` | Token expired or password changed → regenerate (step 9) and update the secret |
| Instagram `code=9` / `2207042` | Daily API publish limit hit. We stay far below it, so check for repeated manual runs |
| Instagram "media not found / cannot download image" | The `cards` bucket is not public. Re-run `schema.sql`, or Supabase → Storage → cards → make public |
| Anything looks wrong | Actions → **Diagnose** → Run workflow. One Telegram message lists every problem with a fix |
| Instagram health check says `@None` or "IG_USER_ID is wrong" | Put the ID shown in the message into the `IG_USER_ID` secret |
| Nothing arrives in Telegram | Check `TELEGRAM_CHAT_ID`, and that you pressed Start on the bot |
| No candidates after ingest | Run ingest again (scoring is capped per run). Check the log for AI errors / keys |
| Many "feeds returned nothing" | Some publishers change RSS URLs. Send me the list and I'll update `sources.py` |
