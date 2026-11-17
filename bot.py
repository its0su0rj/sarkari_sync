#!/usr/bin/env python3
"""
ExamMatch Telegram bot (no server needed: runs from GitHub Actions).

    python bot.py poll      read new messages: subscribe (profile code), /matches, /stop, /help
    python bot.py alerts    send each subscriber new eligible exams + last-date reminders

Environment
    TELEGRAM_BOT_TOKEN   from @BotFather (required)
    SITE_URL             the website, shown in messages
    SR_DB                job database (default sarkari.db)
    BOT_STATE            subscriber file (default bot_state.json)
"""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone

import requests

from eligibility import Profile, load_jobs, match

log = logging.getLogger("bot")
API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
MAX_CODE = 6000
REMIND_DAYS = 3
MAX_LIST = 10


# =========================================================================== state
def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    else:
        st = {}
    st.setdefault("offset", 0)
    st.setdefault("subscribers", {})
    return st


def save_state(path: str, st: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False)
    os.replace(tmp, path)


# =========================================================================== telegram
class Telegram:
    def __init__(self, token: str, base: str = API):
        self.url = f"{base}/bot{token}"
        self.s = requests.Session()

    def call(self, method: str, **params):
        for _ in range(4):
            r = self.s.post(f"{self.url}/{method}", json=params, timeout=40)
            try:
                body = r.json()
            except ValueError:
                body = {"ok": False, "description": r.text[:200]}
            if r.status_code == 429:                       # flood control: wait as told
                wait = (body.get("parameters") or {}).get("retry_after", 5)
                time.sleep(min(int(wait), 60))
                continue
            return r.status_code, body
        return 429, {"ok": False, "description": "rate limited"}

    def send(self, chat_id, text: str) -> str:
        """'ok', 'gone' (user blocked the bot / chat deleted), or 'error'. Long texts go as several messages."""
        result = "ok"
        for part in split_message(text):
            result = self._send_one(chat_id, part)
            if result != "ok":
                break
        return result

    def _send_one(self, chat_id, text: str) -> str:
        code, body = self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                               disable_web_page_preview=True)
        time.sleep(0.05)                                   # stay well under 30 messages/second
        if body.get("ok"):
            return "ok"
        if code in (400, 403) and any(w in (body.get("description") or "").lower()
                                      for w in ("blocked", "deactivated", "chat not found", "kicked")):
            return "gone"
        log.warning("sendMessage to %s failed: %s %s", chat_id, code, body.get("description"))
        return "error"


def split_message(text: str, limit: int = 3800) -> list[str]:
    """Split at line breaks (every line has its own closed HTML tags) to stay under Telegram's 4096 limit."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                          # a single huge line: hard cut (rare)
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts or [""]


# =========================================================================== messages
def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def nice_date(iso: str | None) -> str:
    if not iso:
        return "see notification"
    d = date.fromisoformat(iso)
    return d.strftime("%d %b").lstrip("0")


def job_line(r: dict, today: date) -> str:
    parts = [f"• <b>{esc(r['title'])}</b>"]
    if r.get("last_date"):
        left = (date.fromisoformat(r["last_date"]) - today).days
        when = "today" if left == 0 else "tomorrow" if left == 1 else f"{left} days left"
        parts.append(f"  Last date: {nice_date(r['last_date'])} ({when})")
    links = []
    for key, label in (("apply", "Apply"), ("notification", "Notification"), ("link", "Details")):
        u = r.get(key)
        if u and str(u).startswith(("http://", "https://")):
            links.append(f'<a href="{esc(u)}">{label}</a>')
    if links:
        parts.append("  " + " · ".join(links))
    return "\n".join(parts)


def footer(site: str) -> str:
    out = []
    if site:
        out.append(f'Full details: {esc(site)}')
    out.append("Always confirm in the official notification. /stop to stop alerts.")
    return "\n\n" + "\n".join(out)


HELP = ("<b>ExamMatch alerts</b> · सरकारी परीक्षा सूचना\n\n"
        "1. Open the ExamMatch website, fill in your details and tap <b>Find my exams</b>.\n"
        "2. Tap <b>Copy my code</b> and send the code here.\n\n"
        "I will message you when a new exam you are eligible for comes out, and 3 days before each last date.\n\n"
        "/matches – exams you can apply for now\n/stop – stop alerts and delete your details\n/help – this message")


# =========================================================================== profile codes
def decode_code(text: str) -> dict:
    text = text.strip()
    if not text.startswith("EM1.") or len(text) > MAX_CODE:
        raise ValueError("not a profile code")
    raw = text[4:]
    raw += "=" * (-len(raw) % 4)
    data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bad code")
    allowed = {"name", "dob", "gender", "category", "pwd", "ex_serviceman", "domicile", "education", "pursuing",
               "certificates", "certificates_complete", "experience_years"}
    data = {k: v for k, v in data.items() if k in allowed}
    data.pop("name", None)                                 # not needed for alerts; don't keep it
    Profile.load(data)                                     # raises if invalid
    dob = date.fromisoformat(data["dob"])
    if not (date(1940, 1, 1) <= dob <= date.today()):
        raise ValueError("date of birth looks wrong")
    if len(data.get("education") or []) > 10 or len(data.get("certificates") or []) > 30:
        raise ValueError("too many entries")
    return data


# =========================================================================== matching
def open_matches(profile: dict, jobs: list[dict], today: date) -> list[dict]:
    return [r for r in match(Profile.load(profile), jobs, today) if r["status"] in ("eligible", "check_manually")]


def summary_text(results: list[dict], today: date, site: str, intro: str) -> str:
    elig = [r for r in results if r["status"] == "eligible"]
    chk = [r for r in results if r["status"] == "check_manually"]
    lines = [intro, ""]
    if elig:
        lines.append(f"✅ <b>You can apply for {len(elig)} open exam{'s' if len(elig) != 1 else ''}:</b>")
        lines += [job_line(r, today) for r in elig[:MAX_LIST]]
        if len(elig) > MAX_LIST:
            lines.append(f"…and {len(elig) - MAX_LIST} more on the website.")
    else:
        lines.append("No open exam matches all your details right now. I'll tell you when one comes out.")
    if chk:
        lines.append(f"\n🔎 {len(chk)} more need a manual check (see the website).")
    return "\n".join(lines) + footer(site)


# =========================================================================== commands
def handle_message(tg: Telegram, st: dict, msg: dict, jobs: list[dict], today: date, site: str):
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return
    cid = str(chat["id"])
    text = (msg.get("text") or "").strip()
    subs = st["subscribers"]

    if text.startswith("/stop"):
        existed = subs.pop(cid, None) is not None
        tg.send(cid, "Stopped. Your details have been deleted." if existed else "You are not subscribed.")
        return
    if text.startswith("/matches"):
        if cid not in subs:
            tg.send(cid, "You are not subscribed yet.\n\n" + HELP)
            return
        res = open_matches(subs[cid]["profile"], jobs, today)
        tg.send(cid, summary_text(res, today, site, "<b>Your exams today</b>"))
        return
    if text.startswith("EM1."):
        try:
            profile = decode_code(text)
        except Exception:
            tg.send(cid, "That code didn't work. Please copy it again from the website (tap <b>Copy my code</b>) "
                         "and send the whole code.")
            return
        res = open_matches(profile, jobs, today)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        old = subs.get(cid) or {}
        subs[cid] = {"profile": profile, "since": old.get("since", now), "updated": now,
                     "notified": {r["link"]: {"new": today.isoformat()}
                                  for r in res if r["status"] == "eligible"}}
        intro = "✅ <b>Subscribed!</b> Your details were updated." if old else "✅ <b>Subscribed!</b>"
        tg.send(cid, summary_text(res, today, site, intro))
        return
    tg.send(cid, HELP)


def poll(tg: Telegram, st: dict, jobs: list[dict], today: date, site: str) -> int:
    code, body = tg.call("getUpdates", offset=st["offset"], timeout=0, allowed_updates=["message"])
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates failed: {code} {body.get('description')}")
    n = 0
    for upd in body.get("result") or []:
        st["offset"] = max(st["offset"], upd["update_id"] + 1)
        msg = upd.get("message")
        if msg:
            try:
                handle_message(tg, st, msg, jobs, today, site)
            except Exception as exc:                      # one bad message must not stop the rest
                log.exception("message failed: %s", exc)
            n += 1
    # The saved offset confirms these updates on the next poll. If saving fails, Telegram keeps them
    # (for 24 hours) and they are handled again; every command is safe to repeat.
    return n


def alerts(tg: Telegram, st: dict, jobs: list[dict], today: date, site: str) -> dict:
    stats = {"subscribers": len(st["subscribers"]), "messages": 0, "removed": 0}
    live = {j["link"] for j in jobs}
    for cid in list(st["subscribers"]):
        sub = st["subscribers"][cid]
        notified = {k: v for k, v in (sub.get("notified") or {}).items() if k in live}
        try:
            res = open_matches(sub["profile"], jobs, today)
        except Exception as exc:
            log.warning("profile of %s failed: %s", cid, exc)
            continue
        elig = [r for r in res if r["status"] == "eligible"]
        new = [r for r in elig if r["link"] not in notified]
        remind = [r for r in elig if r["last_date"] and r["link"] not in [x["link"] for x in new]
                  and 0 <= (date.fromisoformat(r["last_date"]) - today).days <= REMIND_DAYS
                  and "remind" not in notified.get(r["link"], {})]
        if not new and not remind:
            sub["notified"] = notified
            continue
        lines = []
        if new:
            lines.append(f"🆕 <b>New exam{'s' if len(new) > 1 else ''} you can apply for</b>")
            lines += [job_line(r, today) for r in new[:MAX_LIST]]
            if len(new) > MAX_LIST:
                lines.append(f"…and {len(new) - MAX_LIST} more on the website.")
        if remind:
            if lines:
                lines.append("")
            lines.append("⏰ <b>Last date soon</b>")
            lines += [job_line(r, today) for r in remind[:MAX_LIST]]
        result = tg.send(cid, "\n".join(lines) + footer(site))
        if result == "gone":
            del st["subscribers"][cid]
            stats["removed"] += 1
            continue
        if result == "ok":
            stats["messages"] += 1
            for r in new:
                notified.setdefault(r["link"], {})["new"] = today.isoformat()
            for r in remind:
                notified.setdefault(r["link"], {})["remind"] = today.isoformat()
        sub["notified"] = notified
    return stats


# =========================================================================== CLI
def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in ("poll", "alerts"):
        print(__doc__)
        return 1
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN is not set")
        return 1
    db = os.environ.get("SR_DB", "sarkari.db")
    state_path = os.environ.get("BOT_STATE", "bot_state.json")
    site = os.environ.get("SITE_URL", "")
    jobs = load_jobs(db) if os.path.exists(db) else []
    st = load_state(state_path)
    tg = Telegram(token)
    today = date.today()
    try:
        if argv[0] == "poll":
            n = poll(tg, st, jobs, today, site)
            log.info("handled %d messages; %d subscribers", n, len(st["subscribers"]))
        else:
            s = alerts(tg, st, jobs, today, site)
            log.info("alerts: %s", s)
    finally:
        save_state(state_path, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
