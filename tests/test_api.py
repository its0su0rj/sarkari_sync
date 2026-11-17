"""API tests on a small temporary database.  Run: python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TMP = tempfile.TemporaryDirectory()
os.environ["SR_DB"] = os.path.join(TMP.name, "api.db")

import sync  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402

SOON = (date.today() + timedelta(days=10)).isoformat()
PAST = (date.today() - timedelta(days=10)).isoformat()


def add_job(db, link, title, quals, last, age=None):
    fields = {"dates": {"last_date": last}, "date_notes": {}, "age": age or {"min": 18, "max": 30},
              "fees": {}, "total_posts": 100, "qualification_text": quals,
              "links": {"apply": "https://apply.example/", "notification": "https://x.example/n.pdf"},
              "missing": [], "confidence": 1.0}
    db.execute("INSERT INTO posts(link, title, kind, categories, fields, review_status, page_for) "
               "VALUES (?,?,?,?,?,?,?)", (link, title, "job", "[]", json.dumps(fields), "new", "v"))


def setUpModule():
    db = sync.open_db(os.environ["SR_DB"])
    add_job(db, "https://s/1", "Bank Clerk Online Form 2026", ["Bachelor Degree in Any Stream"], SOON)
    add_job(db, "https://s/2", "Railway Apprentice Online Form 2026", ["Class 10 and ITI Certificate"], SOON)
    add_job(db, "https://s/3", "Old Post Online Form 2026", ["10th Passed"], PAST)
    add_job(db, "https://s/4", "Bihar BSSC Inter Level Online Form 2026", ["10+2 Passed"], SOON)
    db.execute("INSERT INTO meta VALUES ('last_success', '2026-11-17T11:38:59+00:00')")
    db.commit()
    db.close()


def tearDownModule():
    TMP.cleanup()


PROFILE = {"dob": "2001-03-10", "gender": "male", "category": "OBC", "domicile": "UP",
           "education": [{"level": "10th", "percent": 78}, {"level": "12th", "percent": 72},
                         {"level": "graduate", "degree": "B.Sc", "subjects": ["physics"], "percent": 65}]}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(api.app)

    def test_page(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ExamMatch", r.text)

    def test_status(self):
        j = self.c.get("/api/status").json()
        self.assertEqual(j["jobs"], 4)
        self.assertEqual(j["open_jobs"], 3)
        self.assertTrue(j["last_sync"].startswith("2026-11-17"))

    def test_jobs_open_only(self):
        j = self.c.get("/api/jobs").json()
        self.assertEqual(j["count"], 3)
        self.assertEqual(self.c.get("/api/jobs?all=true").json()["count"], 4)

    def test_match(self):
        j = self.c.post("/api/match", json=PROFILE).json()
        self.assertEqual(j["counts"], {"eligible": 1, "check_manually": 1, "not_eligible": 1, "closed": 1})
        statuses = {r["title"]: r["status"] for r in j["results"]}
        self.assertEqual(statuses, {"Bank Clerk Online Form 2026": "eligible",
                                    "Bihar BSSC Inter Level Online Form 2026": "check_manually"})
        bank = next(r for r in j["results"] if r["status"] == "eligible")
        self.assertEqual(bank["days_left"], 10)
        self.assertEqual(bank["apply"], "https://apply.example/")

    def test_match_include_all(self):
        j = self.c.post("/api/match?include_not_eligible=true", json=PROFILE).json()
        self.assertEqual(len(j["results"]), 4)
        iti = next(r for r in j["results"] if "Apprentice" in r["title"])
        self.assertEqual(iti["status"], "not_eligible")
        self.assertIn("ITI", iti["reasons"][0])

    def test_validation(self):
        bad = dict(PROFILE, category="XYZ")
        self.assertEqual(self.c.post("/api/match", json=bad).status_code, 422)
        bad = dict(PROFILE, dob="2050-01-01")
        self.assertEqual(self.c.post("/api/match", json=bad).status_code, 422)
        bad = dict(PROFILE, education=[])
        self.assertEqual(self.c.post("/api/match", json=bad).status_code, 422)
        bad = dict(PROFILE, education=[{"level": "masters"}])
        self.assertEqual(self.c.post("/api/match", json=bad).status_code, 422)

    def test_web_form_shape(self):
        # exactly what the page sends
        body = {"name": None, "dob": "2003-05-14", "gender": "female", "category": "SC", "pwd": False,
                "ex_serviceman": False, "domicile": "BR",
                "education": [{"level": "10th", "percent": None}, {"level": "12th", "percent": 60,
                              "stream": "arts", "subjects": []}],
                "pursuing": {"level": "graduate", "final_year": True}, "certificates": ["CCC"],
                "certificates_complete": True, "experience_years": 0}
        r = self.c.post("/api/match", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        statuses = {x["title"]: x["status"] for x in r.json()["results"]}
        self.assertEqual(statuses.get("Bihar BSSC Inter Level Online Form 2026"), "eligible")


if __name__ == "__main__":
    unittest.main()
