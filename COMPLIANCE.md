# Compliance guide: India law, government rules and platform policies

**Read this once before going live, and re-read it every few months.** Rules change.

**Not legal advice.** I'm an AI. I checked the items marked ✅ against current sources while building this; items marked 📚 are from my general knowledge and **must be confirmed** with a media/IT lawyer. A single one-hour consultation before you grow is worth it. It also costs money, so it's your call given the zero-budget goal. Nothing here guarantees you won't receive a notice. It reduces the risk and gives you a documented good-faith process, which matters.

---

## 1. India: the rules that matter, and what the system does about each

| Rule | Why it matters to you | What the bot does | What **you** must do |
|---|---|---|---|
| ✅ **IT Rules 2021, Part III** (digital news publishers) | Covers entities "functionally similar" to online news publishers that run a business or systematic activity of publishing news in India. Individuals not acting in the course of business are excluded. Duties include a code of ethics, a grievance officer in India and filings to the Ministry. Parts of this (Rules 9(1), 9(3)) were stayed by the Bombay High Court in 2021, and the stay's current status in 2026 is **unverified**. | Follows the spirit: neutral, attributed, corrected quickly. | **Unclear whether an automated news page counts.** Do the cheap voluntary steps in section 4 (named contact, grievance process, corrections policy). If the page grows large, becomes commercial, or you get any government notice, take legal advice immediately. |
| ✅ **IT Rules amendment on synthetic media** (notified 10 Feb 2026, effective 20 Feb 2026) | Targets realistic AI-generated audio/visual content. Duties fall mainly on platforms. Labelling and provenance rules apply to content that "appears real". | Our images are **text graphics, never photorealistic**, and we don't generate fake photos, video or voices. Posts say "AI-assisted summary". | **Never add AI-generated realistic images, video or voices** to this project without re-checking the rules. |
| ✅ **3-hour takedown on government/court orders** (same amendment) | Meta must act on such orders within 3 hours. Your post or account can disappear quickly. | `/retract` and `/pause` exist for fast response. | Keep Telegram notifications on; respond to platform notices the same day. |
| ✅ **DPDP Act 2023 + Rules** (notified 13 Nov 2025) | Core duties switch on about 18 months after notification (around May 2027). The Rules contain no explicit journalism exemption, and the Editors Guild has flagged this. | Never posts phone numbers, e-mails or ID-style numbers (hard check). Stores only public headline metadata. **No comment/DM collection.** | Don't add features that collect followers' data. Don't post private individuals' personal details. Re-check when the date nears. |
| 📚 **Bharatiya Nyaya Sanhita 2023**: defamation (s.356); promoting enmity between groups (s.196); imputations against national integration (s.197); deliberate acts to outrage religious feelings (s.299); statements causing public mischief (s.353); disclosure of a sexual-offence victim's identity (s.72) | The heart of the risk for any news page. | Neutral wording rules; "alleged/accused"; loaded-word filter; every number/name must come from the source; **held for your approval:** communal/religious flashpoints, sexual offences, minors, terror, mob violence, custodial/encounter deaths, **all crime stories**. | Read every held item with a "would I be comfortable if this reached the accused's lawyer?" test. Never approve a post naming a victim of a sexual offence or a minor. |
| 📚 **Contempt of Courts Act 1971 / sub judice** | Commentary that prejudices a pending case can be contempt. | Holds stories flagged `contempt`/`sub judice`; court posts are factual summaries of orders. | Don't approve commentary on pending trials; report orders, not opinions. |
| 📚 **Representation of the People Act 1951**: 48-hour pre-poll silence (s.126); ban on publishing exit polls during the specified window (s.126A); plus the Model Code of Conduct | Applies to **every** election, including state and by-elections. | `ELECTION_SILENCE` dates hold political posts; "exit poll"/"opinion poll"/"model code of conduct" stories are always held. | **When the Election Commission announces any schedule, add the silence dates** to the `ELECTION_SILENCE` secret (IST dates, e.g. `[["2026-11-01","2026-11-03"]]`). The bot cannot know them. |
| 📚 **Copyright Act 1957**: facts aren't protected, expression is; fair dealing for reporting current events (s.52(1)(a)(iii)) | We summarise other outlets' reporting. | Own-words summaries (copying is blocked in the prompt); outlets are credited; link to the original; **no publisher photos or logos**. | Honour any removal request within 24 hours. Publish a takedown contact (section 4). If you ever monetise, take advice. News-content licensing is being litigated in India (e.g. against AI companies). |
| 📚 **State Emblem Act 2005 / Flag Code** | Misusing the national emblem or implying government status is an offence. | No emblems; names avoid "official/gov". | Never use the Ashoka Lion or imply government affiliation in logos, bio or posts. |
| ✅ **Google News RSS terms** | The only source I could find (an article from 2010) says Google News feeds aren't for commercial use. I could not confirm Google's current terms. | Google News feeds are used **only for discovery** (headline, outlet, link). Real text comes from the publisher. | Your page is non-commercial. If you ever monetise (ads, paid promotions, sponsorship) set `USE_GOOGLE_NEWS=0` and consult a lawyer. |

---

## 2. Platform rules (Meta: Facebook, Instagram, Threads)

| Policy | What it means | How the project complies |
|---|---|---|
| ✅ **Unoriginal-content crackdown** (Facebook) | Reposting others' content without meaningful additions gets reduced distribution; also flagged: more than five hashtags and excessive all-caps. | Own-words summaries with a hook and context; branded graphics we create; **≤5 hashtags** on Instagram, ≤3 on Facebook, 1 on Threads; no all-caps. A link-only post would be risky, so we never do that. |
| ✅ **Official APIs only** | You can publish through Meta's APIs for your own accounts. | Only official Graph/Threads APIs. Development-mode app with your own role needs no App Review. |
| ✅ **Instagram publishing limits and format** | 50–100 API posts per 24 h (Meta's docs conflict); JPEG only; carousels ≤10 items; alt text supported. | We post ≤10/day, JPEG only, alt text on every image, carousel digest. |
| 📚 **Community Standards** (hate speech, violence, misinformation, dangerous organisations) | Violations reduce reach or remove the account. New accounts are scrutinised harder. | Sensitive-topic gate; loaded-word filter; fact-checkers cited rather than AI verdicts; cross-source confirmation. |
| 📚 **Authentic identity / coordinated behaviour** | No fake personas, multiple accounts to mislead, or fake engagement. | Honest "AI-assisted news desk" identity; one set of accounts; no engagement automation. |
| 📚 **Engagement bait** | Asking for likes/shares/tags or "comment YES" is demoted. | The prompt forbids it. |
| 📚 **Political and social-issue ads** | Paid promotion of such content needs verified authorisation and disclaimers. | We run **no ads**. Don't boost posts without reading Meta's ad rules for social issues, elections and politics. |
| 📚 **AI labelling** | Meta labels or asks you to label *photorealistic* AI media. Text and graphics are not that. | No AI photos. Bio and cards say "AI-assisted". |
| 📚 **Free AI tiers** | Free-tier AI services may use submitted data to improve products. | Only public news text is sent. Never add anything private to the prompts. |

**Never do (these get new accounts banned):** buy followers, follow/unfollow loops, engagement pods, auto-comment or auto-like tools, scraping Meta, creating a second brand account to repost.

---

## 3. What the system enforces automatically

- **Held for your approval, even in auto mode:** sensitive topics; all **Crime & Law** stories; election-silence windows; poll/exit-poll stories; any draft failing the **facts-only** check (invented numbers or names); personal data; loaded/insulting terms.
- **Cross-source rule:** single-source stories are labelled "Developing" and attributed.
- **Balance:** no more than 2 **Politics** posts/day; category and state repeats are penalised; topic-neutral popularity signal; every rule applies identically to every party, religion and community.
- **Volume:** 8 posts/day max + 1 digest; 2 breaking/day.
- **Corrections:** `/retract <id>` deletes the Facebook post and tells you which posts to delete by hand on Instagram/Threads (their APIs don't allow it).
- **Kill switch:** `/pause`.
- **Audit trail:** every post, its sources, score, drafts and results are stored in your database.

---

## 4. Your checklist (about 30 minutes, do before the first public post)

- [ ] Bio on all three accounts (copy below), with a contact e-mail (the Gmail is fine).
- [ ] Facebook Page → **About**: paste the "About & corrections" text below.
- [ ] Pin a post (Threads/Facebook) with the corrections policy.
- [ ] Set `ELECTION_SILENCE` whenever any election is announced (state, by-election, local-body).
- [ ] Stay in **review mode** for 2+ weeks. Audit 20 random posts monthly.
- [ ] Reply to takedown or correction requests within 24 hours; use `/retract` when a post is wrong.
- [ ] Keep notifications on for Meta account warnings. Respond to platform notices the same day.
- [ ] If you receive any legal or government notice, `/pause` and get legal advice before replying.
- [ ] Re-read this file when you change anything (monetisation, ads, new platforms, AI images).

### Text you can paste

**Bio:** `Daily India news in plain words. Neutral · sourced · AI-assisted. Corrections: indiainshort.desk@gmail.com`

**About & corrections:**
> India In Short summarises news from Indian and official sources in our own words, always crediting the outlets and linking to the original report. Our summaries are written with AI assistance and checked against the source reporting. We take no side. If we get something wrong we correct or remove it promptly. Write to indiainshort.desk@gmail.com with the post link and the issue; we aim to respond within 24 hours. Rights holders can request removal at the same address.

**Corrections policy (pinned post):**
> Spotted an error? Tell us at indiainshort.desk@gmail.com. We fix or remove mistakes quickly and say so publicly when a correction matters.

---

## 5. Sources I checked
- IT Rules 2021 Part III: [Cyril Amarchand Mangaldas analysis](https://corporate.cyrilamarchandblogs.com/2021/09/from-harbour-to-hardships-understanding-the-information-technology-intermediary-guidelines-and-digital-media-ethics-code-rules-2021-part-iii/) (2021; court-stay status in 2026 not verified)
- 2026 synthetic-media amendment: [Hogan Lovells summary](https://www.hlc.com/en/publications/india-introduces-mandatory-labelling-for-ai-and-3hour-takedown-for-illegal-content)
- DPDP Rules timeline and journalism concerns: [Storyboard18](https://www.storyboard18.com/digital/stakeholders-raise-red-flags-over-dpdp-rules-2025-cite-threats-to-privacy-press-freedom-84444.htm)
- Facebook unoriginal-content policy: [Bitdefender summary](https://www.bitdefender.com/en-us/blog/hotforsecurity/facebooks-crackdown-on-reposted-content-what-creators-need-to-know)
- Instagram publishing limits, carousels, alt text: [Meta developer docs](https://developers.facebook.com/docs/instagram-platform/content-publishing)
- Google News RSS: [2010 article](https://www.fullpress.it/en/rss-feed-google-news-illegal-commercial-republication/) (old; current terms unverified)
