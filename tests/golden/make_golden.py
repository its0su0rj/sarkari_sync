"""
Builds the parity test data: many profiles x real jobs, results from the Python engine.
    python tests/golden/make_golden.py <jobs.json> tests/golden/golden.json
jobs.json: an export from `sync.py --export file --kinds job` (any open/closed mix).
"""
import json, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from datetime import date
from eligibility import Profile, match

EDU = {
    "10th": [{"level": "10th", "percent": 61}],
    "12th_arts": [{"level": "10th", "percent": 70}, {"level": "12th", "stream": "arts", "subjects": [], "percent": 58}],
    "12th_pcm": [{"level": "10th", "percent": 82}, {"level": "12th", "stream": "science maths", "subjects": ["physics", "chemistry", "mathematics"], "percent": 76}],
    "diploma_civil": [{"level": "10th", "percent": 66}, {"level": "diploma", "branch": "Civil Engineering", "percent": 71}],
    "ba_hindi": [{"level": "10th", "percent": 64}, {"level": "12th", "percent": 60}, {"level": "graduate", "degree": "B.A", "subjects": ["hindi", "history"], "percent": 55.5}],
    "bsc_pcm": [{"level": "10th", "percent": 78}, {"level": "12th", "percent": 72}, {"level": "graduate", "degree": "B.Sc", "subjects": ["physics", "chemistry", "mathematics"], "percent": 65}],
    "bsc_home": [{"level": "10th"}, {"level": "12th"}, {"level": "graduate", "degree": "B.Sc", "subjects": ["home science"], "percent": 62}],
    "btech_civil": [{"level": "10th", "percent": 85}, {"level": "12th", "percent": 80}, {"level": "graduate", "degree": "B.Tech", "branch": "Civil Engineering", "percent": 70}],
    "btech_mech": [{"level": "10th", "percent": 75}, {"level": "12th", "percent": 70}, {"level": "graduate", "degree": "B.Tech / B.E", "branch": "Mechanical", "subjects": ["mechanical"], "percent": 59}],
    "bcom": [{"level": "10th", "percent": 72}, {"level": "12th", "stream": "commerce", "subjects": ["commerce"], "percent": 68}, {"level": "graduate", "degree": "B.Com", "subjects": [], "percent": 61}],
    "ma_english": [{"level": "10th"}, {"level": "12th"}, {"level": "graduate", "degree": "B.A", "subjects": ["english"]}, {"level": "postgraduate", "degree": "M.A", "subjects": ["english"], "percent": 57}],
    "mbbs": [{"level": "10th", "percent": 90}, {"level": "12th", "percent": 88}, {"level": "graduate", "degree": "MBBS", "subjects": [], "percent": 66}],
    "bsc_nursing": [{"level": "10th"}, {"level": "12th", "percent": 64}, {"level": "graduate", "degree": "B.Sc Nursing", "subjects": [], "percent": None}],
    "web_nulls": [{"level": "10th", "percent": None}, {"level": "12th", "percent": 60, "stream": "arts", "subjects": []},
                  {"level": "graduate", "degree": None, "stream": None, "branch": None, "subjects": ["Hindi"], "percent": None}],
}
CERTS = [[], ["ITI Fitter"], ["B.Ed"], ["D.El.Ed"], ["CTET"], ["CCC"], ["UPSSSC PET 2025"], ["Driving Licence"],
         ["B.Ed", "CTET"], ["ITI", "CCC"], ["NTT"], ["NET"]]


def profiles(n=60, seed=7):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        y = rnd.randint(1978, 2010)
        p = {"dob": f"{y}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
             "gender": rnd.choice(["male", "female", "female", "male", "other"]),
             "category": rnd.choice(["GEN", "EWS", "OBC", "SC", "ST", "General", "OBC-NCL"]),
             "pwd": rnd.random() < 0.12, "ex_serviceman": rnd.random() < 0.08,
             "domicile": rnd.choice(["UP", "UP", "BR", "RJ", "DL", "MP", None, "up"]),
             "education": EDU[rnd.choice(list(EDU))],
             "certificates": rnd.choice(CERTS),
             "certificates_complete": rnd.random() < 0.8,
             "experience_years": rnd.choice([0, 0, 0, 1, 2.5, 5, 9])}
        if rnd.random() < 0.2:
            p["pursuing"] = {"level": rnd.choice(["graduate", "postgraduate", "12th"]), "final_year": rnd.random() < 0.7,
                             "degree": rnd.choice([None, "B.Sc", "B.A"])}
        out.append(p)
    return out


def main(src, dst):
    jobs = [j for j in json.load(open(src, encoding="utf-8")) if j.get("kind", "job") == "job"]
    keep = ("link", "title", "categories", "dates", "date_notes", "age", "fees", "total_posts",
            "qualification_text", "links")
    jobs = [{k: j.get(k) for k in keep} for j in jobs]
    profs = profiles()
    cases = []
    for today in ("2026-08-01", "2026-11-17"):
        for p in profs:
            cases.append({"today": today, "profile": p,
                          "expected": match(Profile.load(p), jobs, date.fromisoformat(today))})
    json.dump({"jobs": jobs, "cases": cases}, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(cases)} cases x {len(jobs)} jobs = {len(cases) * len(jobs)} checks -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
