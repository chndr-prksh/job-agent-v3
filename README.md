# job-agent

A daemon that watches your Supabase `jobs` queue, fetches each job posting,
ranks it against your resume, and writes a tailored `.docx` resume — then
pings you on Telegram so you can apply with one tap.

## What it does

1. **Reads** jobs already in your `jobs` table (populated by your scraper).
2. **Ranks** each one against your `candidate_profile` (Claude Haiku, ~$0.005/job).
3. **Tailors** a `.docx` resume and saves it to `~/Downloads/{Company}_{Role}_{date}.docx`
   (Claude Sonnet, ~$0.05/job).
4. **Plans** the apply form: which fields to fill, with what selectors (Claude Sonnet, ~$0.025/job).
5. **Notifies** Telegram with the tailored resume path + apply link.
6. **Watches** Gmail for ATS confirmation emails and updates `applications.status`.
7. **Sends** a daily summary at 9am.

## What it doesn't do (by design)

- Doesn't solve CAPTCHAs (you solve them, 10-30 sec).
- Doesn't bypass bot detection (no proxies, no stealth).
- Doesn't auto-submit (you click Submit, 1 sec).
- Doesn't work on SmartRecruiters (DataDome wall).

## Schema

Reads/writes your existing tables. Adds 3 additive tables (in `supabase/schema_additions.sql`):

- `daemon_heartbeat` — liveness
- `apply_log` — audit trail per event
- `job_status` — pipeline progress (separate from `jobs.is_active`)

Run `supabase/schema_additions.sql` once in the Supabase SQL editor.

## Setup

```bash
# 1. Extract
mkdir -p ~/Downloads/job-agent
tar xzf job-agent-v3.tar.gz -C ~/Downloads/job-agent/
cd ~/Downloads/job-agent/job-agent-v3

# 2. Python venv + deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 3. Configure
cp .env.example .env
# Edit .env with your Supabase URL, Anthropic key, Telegram token, etc.
# IMPORTANT: never commit .env; never paste values into AI chats.

# 4. Fill candidate_profile in Supabase (one row, the source of truth for ranking + tailoring)

# 5. Run the schema additions
#    In Supabase SQL editor: paste supabase/schema_additions.sql → Run

# 6. Test one job
python scripts/run_one.py "https://jobs.ashbyhq.com/Jerry.ai/2f9c37f6-2a97-4033-92d1-6ecf4b494c48"

# 7. Daemon mode (polls forever)
python run.py
```

## Daily budget

DAILY_LLM_BUDGET_USD=5.00 in `.env` (default). At ~$0.08/job you can process ~60 jobs/day
before the daemon stops calling Claude.

## Files

```
job-agent-v3/
├── README.md                       # this file
├── requirements.txt                # Python deps
├── .env.example                    # template (copy to .env locally)
├── run.py                          # entrypoint: daemon mode
├── supabase/
│   └── schema_additions.sql        # 3 new tables
├── daemon/
│   ├── __init__.py
│   ├── config.py                   # .env loader
│   ├── supabase_client.py          # schema-aware wrapper (reads/writes your tables)
│   ├── llm.py                      # Claude client + budget guard
│   ├── ranker.py                   # → job_matches
│   ├── tailor.py                   # → resumes + resume_versions + ~/Downloads/*.docx
│   ├── planner.py                  # → application_plans (DOM-grounded field plan)
│   ├── url_ingest.py               # fetches a URL not yet in your scraper pipeline
│   ├── telegram_bot.py             # outbound + inbound commands
│   ├── gmail_watcher.py            # IMAP confirmation detection
│   └── orchestrator.py             # main loop
├── scripts/
│   └── run_one.py                  # one-off: python scripts/run_one.py <url>
├── extension/                      # Chrome extension (MV3)
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── overlay.css
│   ├── popup.html / popup.js
│   └── ats/{detect,greenhouse,lever}.js
└── tests/
    ├── ext_smoke.js
    ├── ext_full_smoke.js           # Greenhouse: 7/7 passing
    └── ext_lever_smoke.js          # Lever: 8/8 passing
```

## Loading the Chrome extension

1. Open `chrome://extensions/`
2. Toggle "Developer mode" (top-right)
3. Click "Load unpacked" and pick `~/Downloads/job-agent/job-agent-v3/extension/`
4. Click the extension's icon → enter your Supabase URL + service_role key → Save
5. Visit any Greenhouse or Lever apply page → click the 🤖 button → form fills, resume attaches, you click Submit.

## Status of ATS handlers

| ATS | Scrape | Autofill |
|---|---|---|
| Greenhouse | ✅ | ✅ (7/7 tests) |
| Lever | ✅ | ✅ (8/8 tests) |
| Ashby | ✅ | ⏳ TODO |
| Workday | ✅ | ⏳ TODO |
| iCIMS | ✅ | ⏳ TODO |
| Eightfold | ✅ | ⏳ TODO |
| Rippling | ✅ | ⏳ TODO |
| SmartRecruiters | ❌ (DataDome) | — |

## Hard rules (from your blueprint)

- Never solve or bypass CAPTCHAs.
- Never use residential proxies or anti-detect browsers.
- Resume file picker requires a human (browser security boundary).
- Submit happens in the user's real Chrome session.
- Honest gaps are surfaced; never fabricate facts
