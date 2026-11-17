#!/usr/bin/env python3
"""
sarkari_sync: keeps a local SQLite copy of sarkariresult.com notifications.

Discovery : the site's WordPress REST API (/wp-json/wp/v2/posts): titles, links, change times.
            Fallback: WordPress RSS (/feed/?paged=N), then /feed_rss.xml
Details   : the API body is empty on this site, so each job post's web page is fetched
            once (and again only when the post changes) and parsed by extract.py.

Usage
    python sync.py                    # normal run (every 2-3 days)
    python sync.py --full             # re-scan the last SR_FIRST_RUN_DAYS days
    python sync.py --status           # what's in the database, last runs
    python sync.py --export jobs.json --open-only
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import logging.handlers
import os
import random
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from extract import classify, extract, main_content

log = logging.getLogger("sarkari_sync")
UTC = timezone.utc


# =========================================================================== config
@dataclass
class Config:
    base: str = "https://www.sarkariresult.com"
    user_agent: str = "ExamMatchBot/1.0 (+mailto:your-email@example.com)"
    db_path: str = "sarkari.db"
    min_delay: float = 5.0         # seconds between requests (random in [min, max])
    max_delay: float = 10.0
    backoff: float = 30.0          # base wait after a timeout / 5xx (doubles each retry)
    max_requests: int = 400        # hard cap per run (API + pages)
    overlap_days: int = 2          # re-check this many days before the last success
    first_run_days: int = 120      # how far back the first run / --full goes
    timeout: float = 40.0
    block_hours: int = 24          # pause after 403/429
    page_kinds: tuple = ("job",)   # post kinds whose web page is fetched for details

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        e = os.environ.get
        c.base = e("SR_BASE", c.base).rstrip("/")
        c.user_agent = e("SR_USER_AGENT", c.user_agent)
        c.db_path = e("SR_DB", c.db_path)
        c.min_delay = float(e("SR_MIN_DELAY", c.min_delay))
        c.max_delay = float(e("SR_MAX_DELAY", c.max_delay))
        c.backoff = float(e("SR_BACKOFF", c.backoff))
        c.max_requests = int(e("SR_MAX_REQUESTS", c.max_requests))
        c.overlap_days = int(e("SR_OVERLAP_DAYS", c.overlap_days))
        c.first_run_days = int(e("SR_FIRST_RUN_DAYS", c.first_run_days))
        c.timeout = float(e("SR_TIMEOUT", c.timeout))
        c.block_hours = int(e("SR_BLOCK_HOURS", c.block_hours))
        kinds = e("SR_PAGE_KINDS")
        if kinds is not None:
            c.page_kinds = tuple(k.strip() for k in kinds.split(",") if k.strip())
        if c.max_delay < c.min_delay:
            c.max_delay = c.min_delay
        return c


class Stop(Exception):
    """Stop this run (request cap, repeated server errors)."""


class Blocked(Stop):
    """The site refused us (403/429). Pause for block_hours."""


class ApiUnavailable(Exception):
    """REST API missing or disabled: use RSS instead."""


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# =========================================================================== http
class Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.count = 0
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.5",
            "Accept-Language": "en-IN,en;q=0.9",
        })

    def _pause(self):
        gap = random.uniform(self.cfg.min_delay, self.cfg.max_delay)
        wait = self._last + gap - time.monotonic()
        if self._last and wait > 0:
            time.sleep(wait)

    def get(self, path: str, params: dict | None = None) -> requests.Response:
        url = path if path.startswith("http") else self.cfg.base + path
        for attempt in range(4):
            if self.count >= self.cfg.max_requests:
                raise Stop(f"request cap of {self.cfg.max_requests} reached")
            self._pause()
            self.count += 1
            try:
                r = self.s.get(url, params=params, timeout=self.cfg.timeout)
            except requests.RequestException as exc:
                self._last = time.monotonic()
                log.warning("network error on %s (%s), attempt %d/4", url, exc.__class__.__name__, attempt + 1)
                time.sleep(self.cfg.backoff * (2 ** attempt))
                continue
            self._last = time.monotonic()
            log.debug("GET %s %s -> %s", url, params or "", r.status_code)

            if r.status_code in (401, 403):
                body = _json(r)
                # A JSON error from WordPress means "API switched off", not "you are blocked".
                if isinstance(body, dict) and str(body.get("code", "")).startswith("rest_"):
                    raise ApiUnavailable(f"HTTP {r.status_code} {body.get('code')}")
                raise Blocked(f"{url} answered HTTP {r.status_code}")
            if r.status_code == 429:
                raise Blocked(f"{url} answered HTTP 429 (too many requests)")
            if r.status_code >= 500:
                log.warning("HTTP %s on %s, attempt %d/4", r.status_code, url, attempt + 1)
                time.sleep(self.cfg.backoff * (2 ** attempt))
                continue
            return r
        raise Stop(f"{url} kept failing after 4 attempts")


def _json(r: requests.Response):
    try:
        return r.json()
    except ValueError:
        return None


def _total_pages(r: requests.Response, page: int) -> int:
    try:
        return int(r.headers.get("X-WP-TotalPages", page))
    except ValueError:
        return page


# =========================================================================== database
SCHEMA = """
CREATE TABLE IF NOT EXISTS posts(
  link TEXT PRIMARY KEY,
  wp_id INTEGER, slug TEXT, title TEXT, kind TEXT, categories TEXT,
  published_gmt TEXT, modified_gmt TEXT,
  fields TEXT, acf TEXT, confidence REAL,
  content_hash TEXT, content_html TEXT, source TEXT,
  review_status TEXT DEFAULT 'new',      -- new | updated | approved | rejected
  first_seen TEXT, last_changed TEXT,
  page_for TEXT                          -- version whose web page has been parsed
);
CREATE INDEX IF NOT EXISTS idx_posts_kind ON posts(kind, review_status);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT, slug TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, source TEXT,
  requests INTEGER, seen INTEGER, new INTEGER, updated INTEGER, status TEXT, message TEXT
);
"""


def open_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(posts)")}
    if "page_for" not in cols:                       # upgrade databases made by v1
        db.execute("ALTER TABLE posts ADD COLUMN page_for TEXT")
    run_cols = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    if "pages" not in run_cols:
        db.execute("ALTER TABLE runs ADD COLUMN pages INTEGER")
    db.commit()
    return db


def get_meta(db, key):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
    db.commit()


def save_post(db, rec: dict) -> str | None:
    """Insert or update one post. Returns 'new', 'updated' or None (unchanged)."""
    digest = hashlib.sha256(json.dumps(
        [rec["title"], rec["content_html"], rec["acf"], rec.get("modified_gmt")], sort_keys=True, ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    row = db.execute("SELECT content_hash, review_status FROM posts WHERE link=?", (rec["link"],)).fetchone()
    if row and row[0] == digest:
        return None

    kind = classify(rec["title"], rec["categories"])
    fields = extract(rec["content_html"], rec["title"])
    stamp = now_utc().isoformat(timespec="seconds")
    values = (
        rec["wp_id"], rec["slug"], rec["title"], kind, json.dumps(rec["categories"], ensure_ascii=False),
        rec["published_gmt"], rec["modified_gmt"], json.dumps(fields, ensure_ascii=False),
        json.dumps(rec["acf"], ensure_ascii=False) if rec["acf"] else None, fields["confidence"],
        digest, rec["content_html"], rec["source"],
    )
    if row:
        # A change to an approved post (date extended, corrigendum...) needs a fresh human check.
        status = "updated" if row[1] in ("approved", "updated") else row[1]
        db.execute(
            "UPDATE posts SET wp_id=?, slug=?, title=?, kind=?, categories=?, published_gmt=?, modified_gmt=?, "
            "fields=?, acf=?, confidence=?, content_hash=?, content_html=?, source=?, "
            "review_status=?, last_changed=? WHERE link=?",
            values + (status, stamp, rec["link"]))
        return "updated"
    db.execute(
        "INSERT INTO posts(wp_id, slug, title, kind, categories, published_gmt, modified_gmt, fields, acf, "
        "confidence, content_hash, content_html, source, link, review_status, first_seen, last_changed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        values + (rec["link"], "new", stamp, stamp))
    return "new"


# =========================================================================== REST API
POSTS = "/wp-json/wp/v2/posts"
CATS = "/wp-json/wp/v2/categories"
FIELDS = "id,date_gmt,modified_gmt,slug,link,title,content,categories,acf"


def load_categories(client: Client, db) -> dict[int, str]:
    try:
        page = 1
        while True:
            r = client.get(CATS, {"per_page": 100, "page": page, "_fields": "id,name,slug"})
            data = _json(r)
            if r.status_code == 400 and page > 1:
                break
            if r.status_code != 200 or not isinstance(data, list):
                raise ApiUnavailable(f"categories: HTTP {r.status_code}")
            for c in data:
                if isinstance(c, dict) and "id" in c:
                    db.execute("INSERT OR REPLACE INTO categories VALUES (?,?,?)",
                               (c["id"], html.unescape(c.get("name", "")), c.get("slug")))
            db.commit()
            if not data or page >= _total_pages(r, page):
                break
            page += 1
    except ApiUnavailable as exc:
        log.warning("could not refresh categories (%s); using cached names", exc)
    return {i: n for i, n in db.execute("SELECT id, name FROM categories")}


def _from_api(p: dict, cats: dict) -> dict | None:
    if not isinstance(p, dict) or not p.get("link"):
        return None
    acf = p.get("acf")
    return {
        "link": p["link"],
        "wp_id": p.get("id"),
        "slug": p.get("slug"),
        "title": strip_tags((p.get("title") or {}).get("rendered", "")),
        "categories": [cats.get(i, str(i)) for i in (p.get("categories") or [])],
        "published_gmt": p.get("date_gmt"),
        "modified_gmt": p.get("modified_gmt"),
        "content_html": (p.get("content") or {}).get("rendered", "") or "",
        "acf": acf if isinstance(acf, dict) and acf else None,
        "source": "api",
    }


def sync_api(client: Client, db, cats: dict, cutoff: datetime) -> dict:
    """Newest-modified first; stops at the cutoff even if the server ignores modified_after."""
    stats = {"seen": 0, "new": 0, "updated": 0, "complete": True}
    # Sent in site-local form: the site is IST (ahead of UTC), so this only widens the window.
    params = {"per_page": 100, "orderby": "modified", "order": "desc", "_fields": FIELDS,
              "modified_after": cutoff.strftime("%Y-%m-%dT%H:%M:%S")}
    page = 1
    while True:
        params["page"] = page
        r = client.get(POSTS, params)
        if r.status_code == 400:
            body = _json(r)
            code = body.get("code", "") if isinstance(body, dict) else ""
            if page > 1 and code == "rest_post_invalid_page_number":
                break
            if page == 1 and "modified_after" in params:
                log.info("server rejected modified_after (%s); filtering on our side", code)
                params.pop("modified_after")
                continue
            raise ApiUnavailable(f"posts: HTTP 400 {code}")
        data = _json(r)
        if r.status_code != 200 or not isinstance(data, list):
            raise ApiUnavailable(f"posts: HTTP {r.status_code}, not a JSON list")
        if not data:
            break
        reached_cutoff = False
        for p in data:
            rec = _from_api(p, cats)
            if not rec:
                continue
            stats["seen"] += 1
            result = save_post(db, rec)
            if result:
                stats[result] += 1
            mod = parse_dt(rec["modified_gmt"])
            if mod and mod < cutoff:
                reached_cutoff = True
        db.commit()
        log.info("API page %d: %d posts (total pages %s)", page, len(data), r.headers.get("X-WP-TotalPages", "?"))
        if reached_cutoff or page >= _total_pages(r, page):
            break
        page += 1
    return stats


# =========================================================================== RSS fallback
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


def parse_rss(xml_bytes: bytes) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            continue
        pub = None
        try:
            pub = parsedate_to_datetime((it.findtext("pubDate") or "").strip()).astimezone(UTC)
        except (TypeError, ValueError, IndexError):
            pass
        m = re.search(r"[?&]p=(\d+)", it.findtext("guid") or "")
        items.append({
            "link": link,
            "wp_id": int(m.group(1)) if m else None,
            "slug": link.rstrip("/").rsplit("/", 1)[-1],
            "title": strip_tags(it.findtext("title") or ""),
            "categories": [(c.text or "").strip() for c in it.findall("category") if c.text],
            "published_gmt": pub.strftime("%Y-%m-%dT%H:%M:%S") if pub else None,
            "modified_gmt": None,
            "content_html": it.findtext("content:encoded", namespaces=NS) or it.findtext("description") or "",
            "acf": None,
            "source": "rss",
            "_pub": pub,
        })
    return items


def _save_items(db, items, stats, cutoff):
    reached = False
    for rec in items:
        stats["seen"] += 1
        result = save_post(db, rec)
        if result:
            stats[result] += 1
        if rec["_pub"] and rec["_pub"] < cutoff:
            reached = True
    db.commit()
    return reached


def sync_rss(client: Client, db, cutoff: datetime) -> dict:
    stats = {"seen": 0, "new": 0, "updated": 0, "complete": False}
    for page in range(1, 21):
        r = client.get("/feed/", {"paged": page} if page > 1 else None)
        items = parse_rss(r.content) if r.status_code == 200 else []
        if not items:
            break
        log.info("RSS /feed/ page %d: %d items", page, len(items))
        if _save_items(db, items, stats, cutoff):
            stats["complete"] = True
            return stats
    if stats["seen"] == 0:
        r = client.get("/feed_rss.xml")
        items = parse_rss(r.content) if r.status_code == 200 else []
        log.info("RSS /feed_rss.xml: %d items", len(items))
        stats["complete"] = _save_items(db, items, stats, cutoff)
    if not stats["complete"]:
        log.warning("RSS did not reach back to %s; some posts may be missing until the API returns",
                    cutoff.date())
    return stats


# =========================================================================== detail pages
def fetch_pages(client: Client, db, kinds) -> dict:
    """Fetch the web page of every post (of the given kinds) not yet parsed at its current version."""
    stats = {"pages": 0, "pending": 0}
    if not kinds:
        return stats
    marks = ",".join("?" * len(kinds))
    rows = db.execute(
        f"SELECT link, title, COALESCE(modified_gmt, content_hash) FROM posts "
        f"WHERE kind IN ({marks}) AND (page_for IS NULL OR page_for != COALESCE(modified_gmt, content_hash)) "
        f"ORDER BY COALESCE(modified_gmt, published_gmt) DESC", tuple(kinds)).fetchall()
    if rows:
        log.info("fetching %d detail pages (about %d min at this pace)", len(rows),
                 max(1, round(len(rows) * (client.cfg.min_delay + client.cfg.max_delay) / 2 / 60)))
    for i, (link, title, version) in enumerate(rows):
        try:
            r = client.get(link)
        except Blocked:
            raise
        except Stop as exc:
            stats["pending"] = len(rows) - i
            log.warning("detail pages paused (%s); %d left for the next run", exc, stats["pending"])
            break
        if r.status_code == 200:
            body = main_content(r.text)
            fields = extract(body, title, link)
            db.execute("UPDATE posts SET content_html=?, fields=?, confidence=?, page_for=? WHERE link=?",
                       (body, json.dumps(fields, ensure_ascii=False), fields["confidence"], version, link))
            stats["pages"] += 1
        else:
            log.warning("page %s answered HTTP %s; skipping this version", link, r.status_code)
            db.execute("UPDATE posts SET page_for=? WHERE link=?", (version, link))
        db.commit()
        if (i + 1) % 25 == 0:
            log.info("detail pages: %d/%d done", i + 1, len(rows))
    return stats


# =========================================================================== run
class Lock:
    def __init__(self, path):
        self.path = path + ".lock"

    def __enter__(self):
        if os.path.exists(self.path) and time.time() - os.path.getmtime(self.path) < 6 * 3600:
            raise Stop("another run is in progress (lock file present)")
        with open(self.path, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def run(cfg: Config, full: bool = False) -> int:
    """0 = ok, 2 = stopped or blocked, 3 = already running."""
    try:
        lock = Lock(cfg.db_path).__enter__()
    except Stop as exc:
        log.error("%s", exc)
        return 3
    db = open_db(cfg.db_path)
    try:
        started = now_utc()
        blocked = parse_dt(get_meta(db, "blocked_until"))
        if blocked and blocked > started:
            log.error("paused until %s because the site refused us earlier; not fetching", blocked)
            return 2

        last = parse_dt(get_meta(db, "last_success"))
        if full or not last:
            cutoff = started - timedelta(days=cfg.first_run_days)
        else:
            cutoff = last - timedelta(days=cfg.overlap_days)
        log.info("sync start: changes since %s", cutoff.isoformat(timespec="minutes"))

        client = Client(cfg)
        source, stats, status, message = "api", {"seen": 0, "new": 0, "updated": 0}, "ok", ""
        try:
            try:
                cats = load_categories(client, db)
                stats = sync_api(client, db, cats, cutoff)
            except ApiUnavailable as exc:
                log.warning("REST API unavailable (%s); falling back to RSS", exc)
                source = "rss"
                stats = sync_rss(client, db, cutoff)
            if stats.get("complete", True):
                set_meta(db, "last_success", started.isoformat())
            else:
                status = "partial"
            pstats = fetch_pages(client, db, cfg.page_kinds)
            stats["pages"] = pstats["pages"]
            if pstats["pending"]:
                message = f"{pstats['pending']} detail pages left for the next run"
        except Blocked as exc:
            status, message = "blocked", str(exc)
            until = now_utc() + timedelta(hours=cfg.block_hours)
            set_meta(db, "blocked_until", until.isoformat())
            log.error("%s: pausing until %s", exc, until.isoformat(timespec="minutes"))
        except Stop as exc:
            status, message = "stopped", str(exc)
            log.error("stopped: %s", exc)
        finally:
            client.s.close()
            db.commit()
            db.execute(
                "INSERT INTO runs(started, finished, source, requests, seen, new, updated, status, message, pages) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (started.isoformat(timespec="seconds"), now_utc().isoformat(timespec="seconds"), source,
                 client.count, stats.get("seen", 0), stats.get("new", 0), stats.get("updated", 0),
                 status, message, stats.get("pages", 0)))
            db.commit()
        log.info("sync %s via %s: %d requests, %d seen, %d new, %d updated, %d detail pages",
                 status, source, client.count, stats.get("seen", 0), stats.get("new", 0),
                 stats.get("updated", 0), stats.get("pages", 0))
        return 0 if status in ("ok", "partial") else 2
    finally:
        db.close()
        lock.__exit__()


# =========================================================================== export / status
def export(cfg: Config, path: str, kinds=("job",), open_only=False) -> int:
    db = open_db(cfg.db_path)
    marks = ",".join("?" * len(kinds))
    rows = db.execute(
        f"SELECT link, title, kind, categories, published_gmt, modified_gmt, fields, acf, confidence, "
        f"review_status FROM posts WHERE kind IN ({marks}) "
        f"ORDER BY COALESCE(modified_gmt, published_gmt) DESC", kinds).fetchall()
    today = date.today().isoformat()
    out = []
    for link, title, kind, cats, pub, mod, fields, acf, conf, review in rows:
        f = json.loads(fields or "{}")
        last_date = (f.get("dates") or {}).get("last_date")
        if open_only and last_date and last_date < today:
            continue
        out.append({"link": link, "title": title, "kind": kind, "categories": json.loads(cats or "[]"),
                    "published_gmt": pub, "modified_gmt": mod, "review_status": review,
                    "confidence": conf, **f, "acf": json.loads(acf) if acf else None})
    db.close()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log.info("exported %d posts to %s", len(out), path)
    return len(out)


def status(cfg: Config):
    db = open_db(cfg.db_path)
    print("last success :", get_meta(db, "last_success") or "never")
    print("paused until :", get_meta(db, "blocked_until") or "-")
    print("\nposts by kind / review status:")
    for kind, rs, n in db.execute(
            "SELECT kind, review_status, COUNT(*) FROM posts GROUP BY kind, review_status ORDER BY kind"):
        print(f"  {kind:<12} {rs:<9} {n}")
    pend = db.execute("SELECT COUNT(*) FROM posts WHERE kind='job' AND (page_for IS NULL OR "
                      "page_for != COALESCE(modified_gmt, content_hash))").fetchone()[0]
    print("\njob pages not yet parsed:", pend)
    print("\nlast 5 runs:")
    for r in db.execute("SELECT started, source, requests, new, updated, pages, status, message FROM runs "
                        "ORDER BY id DESC LIMIT 5"):
        print("  ", " | ".join(str(x) for x in r))
    db.close()


def reparse(cfg: Config) -> int:
    db = open_db(cfg.db_path)
    rows = db.execute("SELECT link, title, content_html FROM posts WHERE page_for IS NOT NULL").fetchall()
    for link, title, body in rows:
        fields = extract(body or "", title, link)
        db.execute("UPDATE posts SET fields=?, confidence=? WHERE link=?",
                   (json.dumps(fields, ensure_ascii=False), fields["confidence"], link))
    db.commit()
    db.close()
    log.info("re-parsed %d saved pages", len(rows))
    return len(rows)


def setup_logging(verbose=False):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.handlers.RotatingFileHandler("sync.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(sh)
    log.addHandler(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sync sarkariresult.com notifications into SQLite.")
    ap.add_argument("--full", action="store_true", help="re-scan the last SR_FIRST_RUN_DAYS days")
    ap.add_argument("--status", action="store_true", help="show database summary and recent runs")
    ap.add_argument("--export", metavar="FILE", help="write posts to a JSON file (no sync)")
    ap.add_argument("--kinds", default="job", help="comma list for --export, e.g. job,admit_card,result")
    ap.add_argument("--open-only", action="store_true", help="with --export: skip jobs whose last date passed")
    ap.add_argument("--unpause", action="store_true", help="clear a pause set after a 403/429")
    ap.add_argument("--reparse", action="store_true",
                    help="re-run extract.py on saved pages (no network), e.g. after improving it")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)
    cfg = Config.from_env()
    if args.status:
        status(cfg)
        return 0
    if args.export:
        export(cfg, args.export, tuple(k.strip() for k in args.kinds.split(",") if k.strip()), args.open_only)
        return 0
    if args.reparse:
        reparse(cfg)
        return 0
    if args.unpause:
        db = open_db(cfg.db_path)
        db.execute("DELETE FROM meta WHERE key='blocked_until'")
        db.commit()
        db.close()
        log.info("pause cleared")
        return 0
    return run(cfg, full=args.full)


if __name__ == "__main__":
    sys.exit(main())
