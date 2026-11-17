"""Bot and encrypted-state tests with a fake Telegram API and a local git remote."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot  # noqa: E402

TODAY = date(2026, 11, 17)


def code_for(profile: dict) -> str:
    return "EM1." + base64.urlsafe_b64encode(json.dumps(profile).encode()).decode().rstrip("=")


PROFILE = {"name": "Rahul", "dob": "2001-03-10", "gender": "male", "category": "OBC", "domicile": "UP",
           "education": [{"level": "10th", "percent": 78}, {"level": "12th", "percent": 72},
                         {"level": "graduate", "degree": "B.Sc", "subjects": ["physics"], "percent": 65}],
           "certificates": [], "certificates_complete": True, "experience_years": 0}


def job(n, title, quals, last, age=None):
    return {"link": f"https://s/{n}", "title": title, "categories": [], "qualification_text": quals,
            "dates": {"last_date": last}, "age": age or {"min": 18, "max": 30}, "date_notes": {},
            "links": {"apply": f"https://apply/{n}", "notification": f"https://n/{n}.pdf"}}


def day(n):
    return (TODAY + timedelta(days=n)).isoformat()


class FakeTelegram:
    def __init__(self):
        self.updates, self.sent, self.blocked, self.flood_once = [], [], set(), False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def say(self, chat_id, text, kind="private"):
        uid = 1000 + len(self.updates)
        self.updates.append({"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat_id, "type": kind},
                                                           "text": text}})

    def _handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                code, out = 200, {"ok": True, "result": True}
                if method == "getUpdates":
                    off = body.get("offset", 0)
                    out = {"ok": True, "result": [u for u in fake.updates if u["update_id"] >= off]}
                elif method == "sendMessage":
                    if fake.flood_once:
                        fake.flood_once = False
                        code, out = 429, {"ok": False, "parameters": {"retry_after": 0}}
                    elif str(body["chat_id"]) in fake.blocked:
                        code, out = 403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}
                    else:
                        fake.sent.append((str(body["chat_id"]), body["text"]))
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        return H


class BotTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.tg = bot.Telegram("TOKEN", self.fake.base)
        self.st = bot.load_state("/nonexistent")
        self.jobs = [job(1, "Bank Clerk Online Form 2026", ["Bachelor Degree in Any Stream"], day(20)),
                     job(2, "Railway Apprentice Online Form 2026", ["Class 10 and ITI Certificate"], day(20)),
                     job(3, "SSC MTS Online Form 2026", ["10th Passed"], day(2))]

    def tearDown(self):
        self.fake.server.shutdown()

    def poll(self):
        return bot.poll(self.tg, self.st, self.jobs, TODAY, "https://site/")

    def test_subscribe_and_matches(self):
        self.fake.say(11, "/start hi")
        self.fake.say(11, code_for(PROFILE))
        self.assertEqual(self.poll(), 2)
        self.assertIn("Copy my code", self.fake.sent[0][1])
        welcome = self.fake.sent[1][1]
        self.assertIn("Subscribed", welcome)
        self.assertIn("Bank Clerk", welcome)
        self.assertIn("SSC MTS", welcome)
        self.assertNotIn("Railway Apprentice", welcome)                    # needs ITI
        sub = self.st["subscribers"]["11"]
        self.assertNotIn("name", sub["profile"])                           # name is not kept
        self.assertEqual(set(sub["notified"]), {"https://s/1", "https://s/3"})
        self.assertEqual(self.st["offset"], 1002)
        self.assertEqual(self.poll(), 0)                                    # already handled
        self.fake.say(11, "/matches")
        self.poll()
        self.assertIn("Your exams today", self.fake.sent[-1][1])

    def test_bad_code_and_groups(self):
        self.fake.say(12, "EM1.not-base64!!")
        self.fake.say(12, code_for(dict(PROFILE, category="XYZ")))
        self.fake.say(13, code_for(PROFILE), kind="group")
        self.poll()
        self.assertEqual(len(self.fake.sent), 2)
        self.assertTrue(all("didn't work" in t for _, t in self.fake.sent))
        self.assertEqual(self.st["subscribers"], {})

    def test_stop_deletes(self):
        self.fake.say(14, code_for(PROFILE))
        self.fake.say(14, "/stop")
        self.poll()
        self.assertEqual(self.st["subscribers"], {})
        self.assertIn("deleted", self.fake.sent[-1][1])

    def test_alerts_new_and_reminder_once(self):
        self.fake.say(15, code_for(PROFILE))
        self.poll()
        self.fake.sent.clear()
        # a new matching job appears; job 3 is due in 2 days
        self.jobs.append(job(4, "UPPSC RO ARO Online Form 2026", ["Bachelor Degree in Any Stream"], day(10)))
        s = bot.alerts(self.tg, self.st, self.jobs, TODAY, "https://site/")
        self.assertEqual(s["messages"], 1)
        msg = self.fake.sent[0][1]
        self.assertIn("New exam", msg)
        self.assertIn("UPPSC RO ARO", msg)
        self.assertIn("Last date soon", msg)
        self.assertIn("SSC MTS", msg)
        self.assertNotIn("Bank Clerk", msg)                                 # already told, not due yet
        self.fake.sent.clear()
        bot.alerts(self.tg, self.st, self.jobs, TODAY, "https://site/")
        self.assertEqual(self.fake.sent, [])                                # nothing repeated

    def test_closed_jobs_pruned_and_blocked_user_removed(self):
        self.fake.say(16, code_for(PROFILE))
        self.poll()
        self.jobs = self.jobs[1:] + [job(5, "Bank PO Online Form 2026", ["Graduate"], day(15))]
        self.fake.blocked.add("16")
        s = bot.alerts(self.tg, self.st, self.jobs, TODAY, "")
        self.assertEqual(s["removed"], 1)
        self.assertEqual(self.st["subscribers"], {})

    def test_flood_control_retry(self):
        self.fake.flood_once = True
        self.assertEqual(self.tg.send(17, "hi"), "ok")
        self.assertEqual(self.fake.sent, [("17", "hi")])

    def test_long_messages_are_split(self):
        lines = [f"• <b>Job {i}</b> " + "x" * 300 for i in range(40)]
        parts = bot.split_message("\n".join(lines))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 3800 for p in parts))
        self.assertEqual("\n".join(parts), "\n".join(lines))
        self.assertEqual(self.tg.send(18, "\n".join(lines)), "ok")
        self.assertEqual(len(self.fake.sent), len(parts))

    def test_html_is_escaped(self):
        self.jobs.append(job(6, "A <b>&</b> Online Form", ["Bachelor Degree in Any Stream"], day(9)))
        line = bot.job_line(bot.match(bot.Profile.load(PROFILE), self.jobs, TODAY)[1], TODAY)
        self.assertNotIn("<b>&</b>", line)


class StateTests(unittest.TestCase):
    def test_round_trip_and_no_history(self):
        with tempfile.TemporaryDirectory() as d:
            remote = os.path.join(d, "remote.git")
            subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
            work = os.path.join(d, "work")
            os.makedirs(work)
            env = dict(os.environ, STATE_PASSWORD="x" * 32, STATE_REMOTE=remote)
            script = os.path.join(os.path.dirname(__file__), "..", "state.py")
            run = lambda *a, **kw: subprocess.run([sys.executable, script, *a], cwd=work, env=kw.get("env", env),
                                                  capture_output=True, text=True)
            self.assertIn("no 'state' branch", run("pull").stdout)
            with open(os.path.join(work, "bot_state.json"), "w") as fh:
                fh.write('{"subscribers": {"1": {"profile": {"dob": "2001-01-01"}}}}')
            self.assertEqual(run("push").returncode, 0)
            run("push")
            # remote holds one commit and the file is not readable
            log = subprocess.run(["git", "--git-dir", remote, "log", "--oneline", "state"], capture_output=True, text=True)
            self.assertEqual(len(log.stdout.strip().splitlines()), 1)
            blob = subprocess.run(["git", "--git-dir", remote, "show", "state:bot_state.json.enc"],
                                  capture_output=True, text=True).stdout
            self.assertNotIn("2001-01-01", blob)
            os.remove(os.path.join(work, "bot_state.json"))
            self.assertEqual(run("pull").returncode, 0)
            self.assertIn("2001-01-01", open(os.path.join(work, "bot_state.json")).read())
            wrong = dict(env, STATE_PASSWORD="y" * 32)
            r = run("pull", env=wrong)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot decrypt", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
