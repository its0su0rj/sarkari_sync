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
4. The bot answers within a few seconds (it runs on Cloudflare: see the next section).

---

## Telegram bot on Cloudflare (free, instant replies)

GitHub runs scheduled jobs late or skips them, so the bot runs on Cloudflare Workers instead
(free plan, no card). One-time setup:

1. Make a free account at **dash.cloudflare.com/sign-up** and verify the email.
2. In the VS Code terminal, in the `sarkari_sync` folder, run:
   `powershell -ExecutionPolicy Bypass -File worker\setup.ps1`
3. It opens Cloudflare in the browser: click **Allow**. If it asks for a workers.dev name, type a
   short one (e.g. `exammatch`). When asked, paste the bot token from BotFather (it stays hidden).
4. It moves the existing subscribers over, connects Telegram, and prints **Done**.
   The admin token is saved as `Documents\exammatch_ADMIN_TOKEN.txt`.

Alerts run every day around 8 AM IST from Cloudflare. To change the bot code later, edit
`worker/src/` and run `cd worker` then `npx wrangler deploy`.

## Sync from your PC (needed: sarkariresult.com blocks GitHub's servers)

The website runs on GitHub and the bot on Cloudflare, but the fetching of new exams runs on your PC:

1. Keep the password file `Documents\exammatch_STATE_PASSWORD.txt` on your PC (the PC needs it to
   encrypt the upload). Also keep a copy in a safe note.
2. Double-click **`pc_sync.bat`** once. It fetches new exams, uploads them (encrypted) to GitHub and
   starts the website rebuild. It says *Done* at the end.
3. Double-click **`schedule_sync.bat`** and choose how often: every 6 or 12 hours, once a day,
   every 2 days, or your own number of hours or days. Run it again any time to change or stop it.
   If the PC is off at the chosen time, it runs as soon as the PC is on again.

If the PC is off for days, the website simply shows the last data until it is on again.
Log: `pc_sync.log` in the folder.

## What runs by itself

| When | What |
|---|---|
| As often as you chose (your PC) | Fetch new and changed posts, upload them, start the website rebuild |
| After each upload, and every 2 days | Run all tests, publish the website |
| Instantly (Cloudflare) | The bot answers messages |
| Every day, about 8 AM (Cloudflare) | Alerts: new eligible exams, and reminders 3 days before last dates |

GitHub emails you if a run fails. Everything is free: Actions and Pages cost nothing for public
repositories.

## If something goes wrong

- **pc_sync.bat says "not logged in"**: run `gh auth login` again in a terminal.
- **"cannot decrypt"**: the `STATE_PASSWORD` secret was changed. Put back the original.
- **Scheduled runs stopped**: GitHub pauses schedules on repositories with no activity for 60 days.
  The workflow commits a small status file every run to prevent this. If it happens anyway:
  **Actions → the workflow → Enable workflow**.
- **The bot doesn't answer**: in `worker`, run `npx wrangler tail` and send the bot a message to see
  errors; run `setup.ps1` again if the token changed.

## Privacy

- Student details are stored only in the student's own browser, and, if they sign up for alerts,
  in your private Cloudflare KV store (only your Cloudflare account can read it).
- `/stop` deletes a student's details at once. The bot does not keep names.
- The public website and `jobs.json` contain only facts and links (dates, ages, fees, links), not
  text copied from sarkariresult.com.

## Updating the code later

Change files on GitHub (or upload new versions). Every change to `main` runs the tests and
republishes the website. If you connect GitHub to Claude (claude.ai → Settings → Connectors),
Claude can make and upload changes for you.
