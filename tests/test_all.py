"""
Offline tests: a fake WordPress site on localhost stands in for sarkariresult.com.
Run:  python -m unittest discover -s tests -v
"""
import json
import math
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sync  # noqa: E402
from extract import classify, extract, find_date, main_content  # noqa: E402

UTC = timezone.utc

LIST_LAYOUT = """
<table><tr><td>Name Of Post</td><td>Indian Bank Apprentice Online Form 2026</td></tr></table>
<h3>Important Dates</h3>
<ul><li>Application Begin : 20/11/2026</li><li>Last Date for Apply Online : <b>10/12/2026</b></li>
<li>Last Date for Fee Payment : 12/12/2026</li><li>Exam Date : Notify Later</li></ul>
<h3>Application Fee</h3>
<ul><li>General / OBC / EWS : 800/-</li><li>SC / ST / PH : 175/-</li></ul>
<h2>Age Limit as on 01/11/2026</h2>
<ul><li>Minimum Age : 20 Years</li><li>Maximum Age : 28 Years</li></ul>
<h2>Vacancy Details Total : 1500 Post</h2>
<table><tr><th>Post Name</th><th>Total Post</th><th>Eligibility</th></tr>
<tr><td>Apprentice</td><td>1500</td><td>Bachelor Degree in Any Stream from a recognized university.</td></tr></table>
<h2>Important Links</h2>
<h2>Apply Online</h2><p><a href="https://ibpsonline.ibps.in/ibapr/">Click Here</a></p>
<h2>How To Fill Form (Hindi Video)</h2><p><a href="https://www.youtube.com/watch?v=x">Watch Video</a></p>
<h2>Download Notification</h2><p><a href="https://indianbank.in/notice.pdf">Click Here</a></p>
<h2>Official Website</h2><p><a href="https://indianbank.in/">Indian Bank</a></p>
"""

TABLE_LAYOUT = """
<table>
<tr><td><strong>Important Dates</strong><br>Application Begin : 05 January 2027<br>
Last Date Apply Online : 4th February 2027<br>Exam Date : March 2027</td>
<td><strong>Application Fee</strong><br>General / OBC : Rs. 100<br>SC / ST : Nil</td></tr>
<tr><td colspan=2><strong>SSC CGL Age Limit as on 01/08/2027</strong><br>Age Limit : 18-32 Years</td></tr>
<tr><td>Total Post : 14,582</td></tr>
<tr><td>Graduate in any stream</td></tr>
<tr><td>Apply Online</td><td><a href="https://ssc.gov.in/login">Click Here</a></td></tr>
<tr><td>Download Notification</td><td><a href="https://ssc.gov.in/cgl.pdf">Click Here</a></td></tr>
<tr><td>Official Website</td><td><a href="https://ssc.gov.in/">SSC</a></td></tr>
</table>
"""


class ExtractTests(unittest.TestCase):
    def test_list_layout(self):
        f = extract(LIST_LAYOUT, "Indian Bank Apprentice Online Form 2026")
        self.assertEqual(f["dates"]["application_start"], "2026-11-20")
        self.assertEqual(f["dates"]["last_date"], "2026-12-10")
        self.assertEqual(f["dates"]["fee_last_date"], "2026-12-12")
        self.assertNotIn("exam_date", f["dates"])
        self.assertEqual(f["age"], {"as_on": "2026-11-01", "min": 20, "max": 28})
        self.assertEqual(f["fees"], {"general_obc_ews": 800, "sc_st_pwd": 175})
        self.assertEqual(f["total_posts"], 1500)
        self.assertTrue(any("Bachelor Degree" in q for q in f["qualification_text"]))
        self.assertEqual(f["links"], {"apply": "https://ibpsonline.ibps.in/ibapr/",
                                      "notification": "https://indianbank.in/notice.pdf",
                                      "official_site": "https://indianbank.in/"})
        self.assertEqual(f["confidence"], 1.0)

    def test_table_layout(self):
        f = extract(TABLE_LAYOUT, "SSC CGL Online Form 2027")
        self.assertEqual(f["dates"]["application_start"], "2027-01-05")
        self.assertEqual(f["dates"]["last_date"], "2027-02-04")
        self.assertEqual(f["age"], {"as_on": "2027-08-01", "min": 18, "max": 32})
        self.assertEqual(f["fees"], {"general_obc_ews": 100, "sc_st_pwd": 0})
        self.assertEqual(f["total_posts"], 14582)
        self.assertEqual(f["links"]["apply"], "https://ssc.gov.in/login")
        self.assertEqual(f["links"]["notification"], "https://ssc.gov.in/cgl.pdf")
        self.assertEqual(f["confidence"], 1.0)

    def test_real_site_shapes(self):
        html = """
<table><tr><td><a href="https://www.sarkariresult.com/">Sarkari Result Official : WWW.SARKARIRESULT.COM</a>
Short Details of Notification</td></tr>
<tr><td><b>Important Dates</b></td></tr>
<tr><td>Application Begin : District Wise</td></tr><tr><td>Last Date for Apply Online : As per Schedule</td></tr>
<tr><td>Age Limit as on 01/01/2028</td></tr>
<tr><td>AFCAT Flying Batch : 20-24 Years.</td></tr><tr><td>Ground Duty Technical : 20-26 Years.</td></tr>
<tr><td>Vacancy Details Total : 284 Post</td></tr>
<tr><td><h2><b>Important Links</b></h2></td></tr>
<tr><td><h2><b>Apply Online</b></h2></td><td><h2><a href="https://bank.example/careers"><b>Link Will be Activate on 27/11/2026</b></a></h2></td></tr>
<tr><td><h2><b>Download Notification</b></h2></td><td><h2><a href="https://bank.example/advt.pdf"><b>Click Here</b></a></h2></td></tr>
<tr><td><h2><b>Signature Resizer, PDF Compress Tools</b></h2></td><td><a href="https://sarkariresultportal.com/">Tools</a></td></tr>
<tr><td><h2><b>Official Website</b></h2></td><td><a href="https://bank.example/">Bank Official Website</a></td></tr>
</table>"""
        f = extract(html, "Air Force AFCAT Online Form 2027")
        self.assertEqual(f["links"]["notification"], "https://bank.example/advt.pdf")   # not the homepage
        self.assertEqual(f["links"]["apply"], "https://bank.example/careers")
        self.assertEqual(f["links"]["apply_opens"], "2026-11-27")
        self.assertEqual(f["links"]["official_site"], "https://bank.example/")
        self.assertEqual(f["date_notes"], {"application_start": "District Wise", "last_date": "As per Schedule"})
        self.assertEqual(f["age"]["as_on"], "2028-01-01")
        self.assertEqual((f["age"]["min"], f["age"]["max"]), (20, 26))
        self.assertEqual(len(f["age"]["by_post"]), 2)
        self.assertEqual(f["total_posts"], 284)

    def test_offline_form_and_age_na(self):
        html = """<table>
<tr><td>Age Limit Details</td></tr><tr><td>Minimum Age : NA</td></tr><tr><td>Maximum Age : NA</td></tr>
<tr><td>Download Application Form &amp; Notification</td><td><a href="https://doc.sarkariresults.org.in/kgbv.pdf">Click Here</a></td></tr>
<tr><td>Find More Jobs</td><td><a href="https://www.sarkariresult.com/outsourcing/">Click Here</a></td></tr>
</table>"""
        f = extract(html, "KGBV Recruitment 2026")
        self.assertTrue(f["age"]["not_specified"])
        self.assertEqual(f["links"], {"apply_offline": "https://doc.sarkariresults.org.in/kgbv.pdf",
                                      "notification": "https://doc.sarkariresults.org.in/kgbv.pdf"})

    def test_vacancy_pdf_relative_and_short_links(self):
        f = extract('<table><tr><td>Download Vacancy Details</td><td><a href="https://doc.x.in/ecce.pdf">Click Here</a></td></tr></table>')
        self.assertEqual(f["links"]["notification"], "https://doc.x.in/ecce.pdf")
        f = extract('<table><tr><td>Download Notification</td><td><a href="/upload/npcil.pdf">Click Here</a></td></tr></table>',
                    "", "https://www.sarkariresult.com/2026/npcil/")
        self.assertEqual(f["links"]["notification"], "https://www.sarkariresult.com/upload/npcil.pdf")
        f = extract('<table><tr><td>Download Notification</td><td><a href="https://tinyurl.com/abc">Click Here</a></td></tr></table>')
        self.assertEqual(f["links"]["notification"], "https://tinyurl.com/abc")

    def test_min_age_na_and_link_priority(self):
        html = """<p>UPESSC has released the <a href="https://upessc.up.gov.in/">Head Master Notification 2026</a>.</p>
<table><tr><td>Age Limit as on 01/10/2026</td></tr><tr><td>Minimum Age : NA</td></tr><tr><td>Maximum Age : 27 Years</td></tr>
<tr><td><h2>Important Links</h2></td></tr>
<tr><td>Download Notification</td><td><a href="https://doc.x.in/upessc.pdf">Click Here</a></td></tr></table>"""
        f = extract(html)
        self.assertEqual(f["age"], {"as_on": "2026-10-01", "max": 27})
        self.assertEqual(f["links"]["notification"], "https://doc.x.in/upessc.pdf")

    def test_max_age_range_is_not_a_post_range(self):
        f = extract("<table><tr><td>Age Limit as on 01/06/2026</td></tr><tr><td>Minimum Age : 18 Years</td></tr>"
                    "<tr><td>Maximum Age : 27-32 Years</td></tr><tr><td>Vacancy Details</td></tr></table>")
        self.assertEqual(f["age"], {"as_on": "2026-06-01", "min": 18, "max": 27, "max_upto": 32})

    def test_empty_and_garbage(self):
        f = extract("", "")
        self.assertEqual(f["confidence"], 0.0)
        extract("<div><p>unclosed <b>tags", "x")  # must not raise

    def test_find_date(self):
        self.assertEqual(find_date("Last date: 31/12/2026"), "2026-12-31")
        self.assertEqual(find_date("on 5th Sept, 2026"), "2026-09-05")
        self.assertEqual(find_date("January 7, 2027"), "2027-01-07")
        self.assertIsNone(find_date("31/02/2026"))
        self.assertIsNone(find_date("Notify Soon"))

    def test_classify(self):
        self.assertEqual(classify("BSF Constable Tradesman CBT Exam Admit Card 2026"), "admit_card")
        self.assertEqual(classify("Railway RRB Group D CEN 09/2025 Result / Score Card 2026"), "result")
        self.assertEqual(classify("CMP Degree College Assistant Professor Online Form 2026"), "job")
        self.assertEqual(classify("SSC CGL Tier I Answer Key 2026"), "answer_key")
        self.assertEqual(classify("Something", ["Latest Job"]), "job")
        self.assertEqual(classify("Something"), "other")


# --------------------------------------------------------------------------- fake site
class FakeSite:
    def __init__(self, n=230):
        self.mode = "ok"            # ok | block | noapi | flaky | api_json_disabled
        self.requests = []
        self.n = n
        self.flaky_left = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        now = datetime.now(UTC)
        self.posts = []
        for i in range(n):
            mod = now - timedelta(hours=6 * i + 1)
            title = f"Dept {i} Admit Card 2026" if i % 5 == 0 else f"Dept {i} Recruitment Online Form 2026"
            self.posts.append({
                "id": 1000 + i, "slug": f"post-{i}", "link": f"{self.base}/2026/post-{i}/",
                "date_gmt": mod.strftime("%Y-%m-%dT%H:%M:%S"), "modified_gmt": mod.strftime("%Y-%m-%dT%H:%M:%S"),
                "title": {"rendered": title.replace(" ", " ", 1) + " &#8211; Apply"},
                "content": {"rendered": '<h1 class="wp-block-heading"></h1>'}, "categories": [5], "acf": [],
                "page_body": LIST_LAYOUT,
            })
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def page(self, p):
        return (f'<html><body><header><nav><a href="{self.base}/admit-card/">Admit Card</a>'
                f'<a href="https://www.sarkariresult.com/2026/upsc-cse/">UPSC CSE 2027 Notification</a></nav></header>'
                f'<div class="wrap"><article><div class="entry-content">{p["page_body"]}'
                f'<h2>FAQ</h2><p><b>What is the last date?</b></p><p>See above.</p></div></article>'
                f'<aside><a href="https://other.example/gd.pdf">SSC GD Notification</a></aside></div>'
                f'<footer><a href="https://play.google.com/x">App</a></footer></body></html>').encode()

    def close(self):
        self.server.shutdown()

    def _rss(self, items):
        body = "".join(
            f"<item><title>{p['title']['rendered']}</title><link>{p['link']}</link>"
            f"<guid>{self.base}/?p={p['id']}</guid>"
            f"<pubDate>{format_datetime(datetime.fromisoformat(p['date_gmt']).replace(tzinfo=UTC))}</pubDate>"
            f"<category><![CDATA[Latest Job]]></category>"
            f"<content:encoded><![CDATA[{p['content']['rendered']}]]></content:encoded></item>" for p in items)
        return ('<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
                f"<channel><title>x</title>{body}</channel></rss>").encode()

    def _handler(site):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype="application/json", headers=None):
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                site.requests.append((u.path, q))
                if site.mode == "block":
                    return self.send(403, b"<html>Access denied</html>", "text/html")
                if site.flaky_left > 0:
                    site.flaky_left -= 1
                    return self.send(503, b"busy", "text/html")
                if u.path.startswith("/wp-json"):
                    if site.mode == "noapi":
                        return self.send(404, b"<html>Not found</html>", "text/html")
                    if site.mode == "api_json_disabled":
                        return self.send(401, {"code": "rest_login_required", "message": "no"})
                    if u.path == "/wp-json/wp/v2/categories":
                        return self.send(200, [{"id": 5, "name": "Latest Job", "slug": "latest-job"}],
                                         headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"})
                    if u.path == "/wp-json/wp/v2/posts":
                        per, page = int(q.get("per_page", 10)), int(q.get("page", 1))
                        posts = sorted(site.posts, key=lambda p: p["modified_gmt"], reverse=True)
                        pages = max(1, math.ceil(len(posts) / per))
                        if page > pages:
                            return self.send(400, {"code": "rest_post_invalid_page_number"})
                        return self.send(200, posts[(page - 1) * per: page * per],
                                         headers={"X-WP-Total": str(len(posts)), "X-WP-TotalPages": str(pages)})
                if u.path == "/feed/":
                    page = int(q.get("paged", 1))
                    posts = sorted(site.posts, key=lambda p: p["date_gmt"], reverse=True)
                    chunk = posts[(page - 1) * 10: page * 10]
                    if not chunk:
                        return self.send(404, b"nope", "text/html")
                    return self.send(200, site._rss(chunk), "application/rss+xml")
                for p in site.posts:
                    if urlparse(p["link"]).path == u.path:
                        return self.send(200, site.page(p), "text/html; charset=utf-8")
                return self.send(404, b"nope", "text/html")
        return H


class SiteCase(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = sync.Config(base=self.site.base, db_path=os.path.join(self.tmp.name, "t.db"),
                               min_delay=0, max_delay=0, backoff=0, timeout=5)
        sync.log.handlers.clear()

    def tearDown(self):
        self.site.close()
        self.tmp.cleanup()

    def db(self):
        return sqlite3.connect(self.cfg.db_path)


class SyncTests(SiteCase):
    def test_first_run_then_incremental_then_update(self):
        self.assertEqual(sync.run(self.cfg), 0)
        n = self.db().execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        self.assertEqual(n, 230)                                    # 230 posts span ~57 days < 120
        self.assertEqual(len(self.site.requests), 1 + 3 + 184)      # categories + 3 API pages + 184 job pages
        f = json.loads(self.db().execute("SELECT fields FROM posts WHERE wp_id=1001").fetchone()[0])
        self.assertEqual(f["dates"]["last_date"], "2026-12-10")
        self.assertEqual(f["links"]["notification"], "https://indianbank.in/notice.pdf")   # not the menu/sidebar
        self.assertEqual(f["confidence"], 1.0)
        kinds = dict(self.db().execute("SELECT kind, COUNT(*) FROM posts GROUP BY kind").fetchall())
        self.assertEqual(kinds, {"job": 184, "admit_card": 46})
        title = self.db().execute("SELECT title FROM posts WHERE wp_id=1001").fetchone()[0]
        self.assertEqual(title, "Dept 1 Recruitment Online Form 2026 – Apply")

        # Second run: nothing changed, one page is enough.
        self.site.requests.clear()
        self.assertEqual(sync.run(self.cfg), 0)
        self.assertEqual(len(self.site.requests), 2)                # no pages refetched
        last = self.db().execute("SELECT new, updated FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last, (0, 0))

        # Approve a post, then the site edits it (e.g. last date extended).
        with self.db() as d:
            d.execute("UPDATE posts SET review_status='approved' WHERE wp_id=1003")
        p = next(p for p in self.site.posts if p["id"] == 1003)
        p["page_body"] = LIST_LAYOUT.replace("10/12/2026", "20/12/2026")
        self.site.requests.clear()
        p["modified_gmt"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertEqual(sync.run(self.cfg), 0)
        row = self.db().execute("SELECT review_status, fields FROM posts WHERE wp_id=1003").fetchone()
        self.assertEqual(row[0], "updated")
        self.assertEqual(json.loads(row[1])["dates"]["last_date"], "2026-12-20")
        self.assertEqual(sum(1 for path, _ in self.site.requests if path.startswith("/2026/")), 1)

    def test_blocked_pauses_and_makes_no_more_requests(self):
        self.site.mode = "block"
        self.assertEqual(sync.run(self.cfg), 2)
        self.assertEqual(len(self.site.requests), 1)                # stopped at the first 403
        self.site.requests.clear()
        self.site.mode = "ok"
        self.assertEqual(sync.run(self.cfg), 2)                     # still paused
        self.assertEqual(self.site.requests, [])
        with self.db() as d:
            d.execute("DELETE FROM meta WHERE key='blocked_until'")
        self.assertEqual(sync.run(self.cfg), 0)

    def test_rss_fallback_when_api_missing(self):
        self.site.mode = "noapi"
        self.cfg.first_run_days = 3                                 # ~12 posts -> 2 feed pages
        self.assertEqual(sync.run(self.cfg), 0)
        rows = self.db().execute("SELECT source, COUNT(*) FROM posts GROUP BY source").fetchall()
        self.assertEqual(rows[0][0], "rss")
        self.assertGreaterEqual(rows[0][1], 12)
        status = self.db().execute("SELECT status, source FROM runs").fetchone()
        self.assertEqual(status, ("ok", "rss"))

    def test_api_disabled_with_json_error_uses_rss(self):
        self.site.mode = "api_json_disabled"
        self.cfg.first_run_days = 3
        self.assertEqual(sync.run(self.cfg), 0)
        self.assertEqual(self.db().execute("SELECT source FROM runs").fetchone()[0], "rss")

    def test_retries_server_errors(self):
        self.site.flaky_left = 2
        self.assertEqual(sync.run(self.cfg), 0)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM posts").fetchone()[0], 230)

    def test_request_cap_stops_without_advancing_cursor(self):
        self.cfg.max_requests = 2
        self.assertEqual(sync.run(self.cfg), 2)
        d = self.db()
        self.assertEqual(d.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 100)   # newest page kept
        self.assertIsNone(d.execute("SELECT value FROM meta WHERE key='last_success'").fetchone())

    def test_lock_prevents_parallel_runs(self):
        open(self.cfg.db_path + ".lock", "w").close()
        self.assertEqual(sync.run(self.cfg), 3)
        self.assertEqual(self.site.requests, [])

    def test_export_open_only(self):
        sync.run(self.cfg)
        out = os.path.join(self.tmp.name, "jobs.json")
        n_all = sync.export(self.cfg, out)
        data = json.load(open(out, encoding="utf-8"))
        self.assertEqual(n_all, 184)
        self.assertNotIn("content_html", data[0])
        self.assertEqual(data[0]["dates"]["last_date"], "2026-12-10")


if __name__ == "__main__":
    unittest.main()


class PageCapTests(SiteCase):
    def test_pages_continue_next_run(self):
        self.cfg.max_requests = 50
        self.assertEqual(sync.run(self.cfg), 0)                     # API done, 46 pages, rest pending
        d = self.db()
        done = d.execute("SELECT COUNT(*) FROM posts WHERE page_for IS NOT NULL").fetchone()[0]
        self.assertEqual(done, 46)
        self.cfg.max_requests = 400
        self.assertEqual(sync.run(self.cfg), 0)
        done = self.db().execute("SELECT COUNT(*) FROM posts WHERE kind='job' AND page_for IS NOT NULL").fetchone()[0]
        self.assertEqual(done, 184)

    def test_main_content_drops_menus(self):
        html = self.site.page(self.site.posts[1]).decode()
        body = main_content(html)
        self.assertIn("Important Dates", body)
        self.assertNotIn("UPSC CSE 2027", body)
        self.assertNotIn("other.example", body)
