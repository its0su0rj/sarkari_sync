# ExamMatch (sarkari_sync)

**To put it online for free, follow [SETUP.md](SETUP.md).**

| Part | File |
|---|---|
| Sync from sarkariresult.com | `sync.py`, `extract.py` |
| Eligibility engine | `eligibility.py` (Python) and `site/engine.js` (same rules in JavaScript) |
| Website (static, runs in the browser) | `site/` built by `build_site.py` |
| Local web page + API | `api.py`, `web/index.html` (`serve.bat`) |
| Telegram bot and alerts | `bot.py` |
| Encrypted storage of working data | `state.py` |
| Automation | `.github/workflows/sync.yml`, `.github/workflows/bot.yml` |

**Rule changes go in both `eligibility.py` and `site/engine.js`.** `tests/test_parity.py` checks that both
give identical results on every real job (needs Node.js; it runs automatically on GitHub).


Keeps a local SQLite database of sarkariresult.com posts (jobs, admit cards, results...)
with structured fields: dates, age limits, fees, total posts, qualification text and the
apply / notification / official-website links.

## How it works

1. **WordPress REST API** (`/wp-json/wp/v2/posts`): up to 100 posts per request, newest-modified first.
   Only posts changed since the last successful run (minus a 2-day safety overlap) are fetched,
   so a normal run every 2-3 days takes **2-3 requests**.
2. **RSS fallback**: if the API is missing or disabled, it reads `/feed/?paged=N`, then `/feed_rss.xml`.
3. **Detail pages**: on this site the API and RSS carry only titles, links and change times (the post
   body is empty). So the web page of each **job** post is fetched once, cut down to the post body
   (menus and sidebars removed), and parsed. It is fetched again only when the post changes.
   First run: ~250 pages, about 30 minutes. Later runs: only new or changed jobs, a few minutes.
   Unfinished pages carry over to the next run.
4. Each post is classified (`job`, `admit_card`, `result`, `answer_key`, `syllabus`, `admission`,
   `certificate`, `other`) and parsed by `extract.py`. A `confidence` score (0-1) and a `missing` list
   show what couldn't be found.
5. Unchanged posts are skipped (content hash). If a post you **approved** changes later
   (date extended, corrigendum), its `review_status` becomes `updated` so you re-check it.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put your real contact in the User-Agent (required, so the site owner can reach you):

```bash
export SR_USER_AGENT="ExamMatchBot/1.0 (+mailto:you@yourdomain.com)"     # Windows: set SR_USER_AGENT=...
```

## Use

```bash
python sync.py                                   # sync (first run fetches the last 120 days)
python sync.py --status                          # counts and last 5 runs
python sync.py --export jobs.json --open-only    # jobs whose last date hasn't passed
python sync.py --export all.json --kinds job,admit_card,result
python sync.py --full                            # re-scan the last 120 days
python sync.py --unpause                         # clear a 24h pause after a 403/429
```

Exit codes: `0` ok, `2` stopped or paused, `3` another run is in progress.

## Run it every 2 days

**Linux / Mac (cron)**, at 03:10 every second day:

```
10 3 */2 * *  cd /path/to/sarkari_sync && SR_USER_AGENT="ExamMatchBot/1.0 (+mailto:you@yourdomain.com)" .venv/bin/python sync.py >> cron.log 2>&1
```

**Windows (Task Scheduler)**: Create Basic Task → Daily, recur every 2 days → Start a program:
`C:\path\to\sarkari_sync\.venv\Scripts\python.exe`, arguments `sync.py`, start in `C:\path\to\sarkari_sync`.
Set `SR_USER_AGENT` as a user environment variable.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SR_USER_AGENT` | placeholder | Your bot name + contact |
| `SR_DB` | `sarkari.db` | Database file |
| `SR_MIN_DELAY` / `SR_MAX_DELAY` | 5 / 10 | Seconds between requests |
| `SR_MAX_REQUESTS` | 400 | Hard cap per run (API + pages) |
| `SR_PAGE_KINDS` | `job` | Kinds whose pages are fetched, e.g. `job,admit_card` (empty = none) |
| `SR_FIRST_RUN_DAYS` | 120 | How far back the first run and `--full` go |
| `SR_OVERLAP_DAYS` | 2 | Re-check window before the last success |
| `SR_BLOCK_HOURS` | 24 | Pause after a 403/429 |

## Safety rules built in

- One request at a time, 5-10 s apart, max 400 per run.
- On **403 or 429** it stops immediately and pauses for 24 h. It never retries around a block.
  If this happens on your first run, the site may block scripts; email the owner rather than working around it.
- Timeouts and 5xx errors are retried 3 times with growing waits.
- A lock file prevents two runs at once.
- `--export` writes facts and links only, never the site's article text. `content_html` is kept in your
  local database only so posts can be re-parsed if `extract.py` improves.

## Database

`posts` table: `link` (key), `wp_id`, `title`, `kind`, `categories`, `published_gmt`, `modified_gmt`,
`fields` (JSON from extract.py), `acf` (custom fields, if the site exposes any), `confidence`,
`review_status` (`new` / `updated` / `approved` / `rejected`), `first_seen`, `last_changed`.

Approve a post after checking it against the official PDF:

```sql
UPDATE posts SET review_status='approved' WHERE link='https://www.sarkariresult.com/2026/...';
```

## Eligibility matching

`eligibility.py` checks a student profile against every synced job:

```bash
python eligibility.py profile.json            # eligible + check manually, open jobs only
python eligibility.py profile.json --all      # also not eligible / closed, with reasons
```

On Windows: double-click `match.bat`. The first time it creates `profile.json` from
`profile.example.json` and opens it in Notepad; fill it in and run `match.bat` again.
Results go to the screen and to `matches.json` (with apply and notification links).

### Profile fields

| Field | Values |
|---|---|
| `dob` | `YYYY-MM-DD` |
| `gender` | `male`, `female`, `other` |
| `category` | `GEN`, `EWS`, `OBC`, `SC`, `ST` |
| `pwd`, `ex_serviceman` | `true` / `false` |
| `domicile` | state code: `UP`, `BR`, `RJ`, `MP`, `DL`, `HR`, ... |
| `education` | list of `{level, degree, stream/branch, subjects, percent}`; level is `8th`, `10th`, `12th`, `diploma`, `graduate`, `postgraduate`, `phd` |
| `pursuing` | `{level, degree, final_year: true}` if in the final year |
| `certificates` | e.g. `ITI Fitter`, `B.Ed`, `D.El.Ed`, `CTET`, `CCC`, `UPSSSC PET 2025`, `Driving Licence` |
| `certificates_complete` | `true` if the list is complete (then a missing B.Ed / ITI means "not eligible") |
| `experience_years` | number |

### Results

- **eligible**: every rule the engine can read passes.
- **check_manually**: nothing rules the student out, but something can't be confirmed
  (a subject, a state domicile rule, a PET scorecard, data missing from the post). The reason is shown.
- **not_eligible**: a clear rule fails (age, level, gender, a required certificate, marks, experience).
- **closed**: last date passed.

Rules used: maximum age N means not having completed N+1 years on the stated date; central relaxation
OBC 3, SC/ST 5, PwD +10; UP rules (OBC/SC/ST 5, PwD 15) for UP domicile only; other states' relaxation
is flagged for checking; no category relaxation for entrance exams and defence entries.
Title rules add requirements the text often omits (Medical Officer, Engineer branch, Assistant Professor,
UPSSSC PET). The engine is a filter: the official notification is always the final word.

## Web page and API

Double-click **`serve.bat`**: it opens http://127.0.0.1:8000 in your browser. Students fill in their
profile and see their exams in three tabs (Eligible / Check / Not eligible), with deadlines,
reasons, and Apply / Notification / Details buttons. The profile is remembered in that browser.

To open it on a phone on the same Wi-Fi, double-click **`serve_phone.bat`** and open the address it
prints (e.g. `http://192.168.1.5:8000`). Windows will ask once to allow Python through the firewall:
allow it for **private networks** only.

The page uses the API, which a mobile app can use directly:

| Method | Path | What it does |
|---|---|---|
| GET | `/api/status` | last sync time, job counts |
| GET | `/api/jobs` | open jobs with dates, ages, fees, links (`?all=true` for all) |
| POST | `/api/match` | send a profile (same fields as `profile.example.json`), get results; `?include_not_eligible=true` for everything |

Interactive API docs: http://127.0.0.1:8000/docs. The API reloads jobs by itself after each sync.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests run against a fake WordPress site on localhost: pagination, incremental sync, edits to
approved posts, 403 pause, RSS fallback, retries, request cap, lock and export.

## First real run: check this

1. Run `python sync.py -v`, then `python sync.py --status`.
2. Run `python sync.py --export jobs.json` and open 5 posts next to the real pages.
   If a field is often missing (see `missing` and `confidence`), save that post's HTML into a new
   test in `tests/test_all.py` and adjust the labels in `extract.py`.
3. Check whether `acf` has data. If it does, those custom fields may already hold clean dates and fees.
