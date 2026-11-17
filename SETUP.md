# Put ExamMatch online for free (₹0, no card)

What you get:
- A website at `https://YOUR-USERNAME.github.io/exammatch/` that is always on, works on cheap phones,
  and can be installed on Android like an app.
- Exams updated automatically every 2 days.
- A Telegram bot that tells students about new exams they are eligible for, and reminds them
  3 days before the last date.

Time needed: about 15 minutes, plus a 30–40 minute wait for the first update.

---

## Part A: create the Telegram bot (2 minutes)

1. Open Telegram and search for **@BotFather** (it has a blue tick). Send `/newbot`.
2. Name: `ExamMatch Alerts` (anything you like).
3. Username: must end in `bot`, e.g. `ExamMatchAlertsBot`. Write it down.
4. BotFather replies with a **token** like `7123456789:AAH...`. Keep it secret: anyone with it controls your bot.

## Part B: put the project on GitHub (10 minutes)

1. Sign up at **github.com** (free). Verify your email.
2. Click **+** (top right) → **New repository**.
   - Repository name: `exammatch`
   - Choose **Public** (needed for the free website and free automation; your private data
     stays encrypted, see "Privacy" below)
   - Do **not** tick "Add a README". Click **Create repository**.
3. On the new empty page, click the link **uploading an existing file**.
   Open `Documents\sarkari_sync\upload_to_github` in File Explorer, press **Ctrl+A**, drag
   everything into the browser, wait for the upload, then click **Commit changes**.
   (This folder holds only code: no database, no profiles, no passwords.)
4. Add the two automation files (GitHub needs them in a folder called `.github/workflows`):
   - Click **Add file → Create new file**. In the name box type `.github/workflows/sync.yml`
     (typing `/` creates the folders).
   - Open `upload_to_github\github_workflows\sync.yml` in Notepad, **Ctrl+A**, **Ctrl+C**,
     paste into the big box on GitHub, click **Commit changes**.
   - Do the same for `.github/workflows/bot.yml` with `github_workflows\bot.yml`.
5. **Settings** → **Pages** → under "Build and deployment", set **Source** to **GitHub Actions**.
6. **Settings** → **Secrets and variables** → **Actions**:
   - **Secrets** tab → **New repository secret**
     - Name `STATE_PASSWORD`. Value: double-click `newkey.bat` in your `sarkari_sync` folder,
       which copies a new random password; paste it. **Also save it somewhere safe** (e.g. a note
       on your phone). If it is lost, the job database and subscriber list can't be read and
       start again from empty.
     - Name `TELEGRAM_BOT_TOKEN`, value: the token from BotFather.
   - **Variables** tab → **New repository variable**
     - Name `TELEGRAM_BOT_USERNAME`, value: the bot username **without** `@`.
     - Optional: `SR_USER_AGENT` = `ExamMatchBot/1.0 (+mailto:your-email@example.com)` so the
       site owner can contact you.
7. **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
   Click **Sync jobs and publish site** → **Run workflow** → **Run workflow**.
   The first run fetches 120 days of posts slowly and politely: about 30–40 minutes.
8. When it shows a green tick, your website is at `https://YOUR-USERNAME.github.io/exammatch/`
   (also shown in **Settings → Pages**).

## Part C: try it

1. Open the website on your phone, fill in your details, tap **Find my exams**.
2. Chrome may offer **Install ExamMatch**; or use the menu → **Add to Home screen**.
3. In **Get alerts on Telegram**: tap **Open Telegram bot**, press **Start**, come back, tap
   **Copy my code**, paste it in the bot chat and send it.
4. The bot answers within about an hour. To get the answer immediately: **Actions → Telegram bot →
   Run workflow**.

---

## What runs by itself

| When | What |
|---|---|
| Every 2 days, about 2 AM | Fetch new and changed posts, run all tests, publish the website |
| Every hour | The bot reads and answers messages |
| Every day, about 8 AM | Alerts: new eligible exams, and reminders 3 days before last dates |

GitHub emails you if a run fails. Everything is free: Actions and Pages cost nothing for public
repositories.

## If something goes wrong

- **The sync step fails with 403 or "blocked"**: sarkariresult.com may block GitHub's servers.
  The website keeps showing the last data. Tell me and I'll switch the sync to your own PC
  (it worked from there) with automatic upload.
- **"cannot decrypt"**: the `STATE_PASSWORD` secret was changed. Put back the original.
- **Scheduled runs stopped**: GitHub pauses schedules on repositories with no activity for 60 days.
  The workflow commits a small status file every run to prevent this. If it happens anyway:
  **Actions → the workflow → Enable workflow**.
- **The bot doesn't answer**: check **Actions → Telegram bot** for errors, and that the token secret and
  username variable are right.

## Privacy

- Student details are stored only in the student's own browser, and, if they sign up for alerts,
  encrypted with your `STATE_PASSWORD` on the `state` branch of your repository. Nobody without the
  password can read them.
- The `state` branch is replaced on every run (no history is kept), and `/stop` deletes a student's
  details at the next hourly run. The bot does not keep names.
- The public website and `jobs.json` contain only facts and links (dates, ages, fees, links), not
  text copied from sarkariresult.com.

## Updating the code later

Change files on GitHub (or upload new versions). Every change to `main` runs the tests and
republishes the website. If you connect GitHub to Claude (claude.ai → Settings → Connectors),
Claude can make and upload changes for you.
