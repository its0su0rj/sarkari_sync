"""Python and JavaScript engines must agree. Needs Node.js (skipped if missing).

Uses the jobs in sarkari.db (or $SR_JOBS_JSON) and many generated profiles.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class ParityTest(unittest.TestCase):
    def test_js_matches_python(self):
        src = os.environ.get("SR_JOBS_JSON")
        tmp = tempfile.TemporaryDirectory()
        if not src:
            db = os.path.join(ROOT, "sarkari.db")
            if not os.path.exists(db):
                self.skipTest("no sarkari.db and no SR_JOBS_JSON")
            from eligibility import load_jobs
            src = os.path.join(tmp.name, "jobs.json")
            json.dump(load_jobs(db), open(src, "w", encoding="utf-8"))
        golden = os.path.join(tmp.name, "golden.json")
        subprocess.run([sys.executable, os.path.join(HERE, "golden", "make_golden.py"), src, golden],
                       check=True, capture_output=True)
        r = subprocess.run(["node", os.path.join(HERE, "golden", "check_golden.js"), golden],
                           capture_output=True, text=True)
        tmp.cleanup()
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
