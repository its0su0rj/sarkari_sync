"""Rules of the eligibility engine.  Run: python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eligibility import Profile, check  # noqa: E402

TODAY = date(2026, 11, 17)


def profile(**kw):
    d = {"dob": "2000-01-15", "gender": "male", "category": "GEN", "domicile": "UP",
         "education": [{"level": "10th", "percent": 70}, {"level": "12th", "percent": 70},
                       {"level": "graduate", "degree": "B.A", "subjects": ["hindi", "history"], "percent": 60}]}
    d.update(kw)
    return Profile.load(d)


def job(title="SSC CGL Online Form 2026", quals=("Bachelor Degree in Any Stream",), age=None, last="2026-12-10", **kw):
    j = {"title": title, "link": "https://x/", "qualification_text": list(quals),
         "dates": {"last_date": last} if last else {}, "age": age if age is not None else
         {"as_on": "2026-08-01", "min": 18, "max": 27}, "links": {}}
    j.update(kw)
    return j


class AgeTests(unittest.TestCase):
    def test_upper_limit_means_not_completed_next_year(self):
        p = profile(dob="1998-08-02")                     # 27y 11m on 2026-08-01
        self.assertEqual(check(p, job(), TODAY).status, "eligible")
        p = profile(dob="1998-08-01")                     # exactly 28
        r = check(p, job(), TODAY)
        self.assertEqual(r.status, "not_eligible")
        self.assertIn("Over age", r.reasons[0])

    def test_too_young(self):
        r = check(profile(dob="2009-01-01"), job(), TODAY)
        self.assertIn("Too young", r.reasons[0])

    def test_central_obc_relaxation(self):
        p = profile(dob="1996-09-01", category="OBC")     # 29y on 2026-08-01, 27 + 3
        r = check(p, job(), TODAY)
        self.assertEqual(r.status, "eligible")
        self.assertIn("3-year OBC", r.notes[0])

    def test_state_relaxation_only_for_domicile(self):
        up_job = job(title="UPSSSC Junior Assistant Online Form 2026", quals=("Intermediate Passed",),
                     age={"as_on": "2026-07-01", "min": 18, "max": 40})
        p_up = profile(dob="1982-01-01", category="OBC", domicile="UP", certificates=["UPSSSC PET 2025"])
        self.assertEqual(check(p_up, up_job, TODAY).status, "eligible")          # 44: 40 + 5
        p_br = profile(dob="1982-01-01", category="OBC", domicile="BR", certificates=["UPSSSC PET 2025"])
        r = check(p_br, up_job, TODAY)
        self.assertEqual(r.status, "not_eligible")
        self.assertIn("only for UP domicile", r.reasons[0])

    def test_no_relaxation_for_entrance_exams(self):
        j = job(title="NTA All India Sainik Schools AISSEE 2027 Online Form", quals=("Class VIII Exam Passed",),
                age={"as_on": "2027-03-31", "min": 13, "max": 15})
        r = check(profile(dob="2010-01-01", category="SC"), j, TODAY)
        self.assertEqual(r.status, "not_eligible")

    def test_max_age_varies_by_post(self):
        j = job(age={"as_on": "2026-08-01", "min": 18, "max": 27, "max_upto": 32})
        r = check(profile(dob="1996-01-01"), j, TODAY)                          # 30
        self.assertEqual(r.status, "check_manually")
        self.assertIn("some posts", r.reasons[0])

    def test_age_by_post(self):
        j = job(age={"as_on": "2028-01-01", "by_post": [{"post": "Flying", "min": 20, "max": 24},
                                                         {"post": "Ground Duty", "min": 20, "max": 26}]})
        r = check(profile(dob="2002-06-01"), j, TODAY)                          # 25
        self.assertEqual(r.status, "eligible")
        self.assertIn("Ground Duty", r.notes[0])


class QualificationTests(unittest.TestCase):
    def test_level_too_low(self):
        p = profile(education=[{"level": "10th"}, {"level": "12th"}])
        r = check(p, job(), TODAY)
        self.assertEqual(r.status, "not_eligible")
        self.assertIn("graduate", r.reasons[0])

    def test_higher_level_accepted(self):
        self.assertEqual(check(profile(), job(quals=("10+2 Intermediate Passed",)), TODAY).status, "eligible")

    def test_or_options(self):
        j = job(quals=("Bachelor Degree in Civil Engineering OR Three years Diploma in Civil Engineering",))
        self.assertEqual(check(profile(), j, TODAY).status, "not_eligible")
        p = profile(education=[{"level": "10th"}, {"level": "diploma", "branch": "Civil Engineering"}])
        self.assertEqual(check(p, j, TODAY).status, "eligible")

    def test_iti_required(self):
        j = job(title="Railway Apprentice Online Form", quals=("Class 10 With 50% Marks and ITI / NCVT Certificate",))
        self.assertIn("ITI", check(profile(), j, TODAY).reasons[0])
        p = profile(certificates=["ITI Fitter"])
        self.assertEqual(check(p, j, TODAY).status, "eligible")

    def test_marks(self):
        j = job(quals=("Bachelor Degree with Minimum 65% Marks",))
        r = check(profile(), j, TODAY)                                                    # has 60%
        self.assertEqual(r.status, "not_eligible")
        self.assertIn("65%", r.reasons[0])
        p = profile(education=[{"level": "graduate", "degree": "B.A"}])
        self.assertEqual(check(p, j, TODAY).status, "check_manually")                    # marks unknown

    def test_marks_exact_is_enough(self):
        j = job(quals=("Bachelor Degree with Minimum 55% Marks",))
        self.assertEqual(check(profile(), j, TODAY).status, "eligible")

    def test_bed_and_teacher_training(self):
        j = job(title="UP TGT Teacher Online Form", quals=("Graduation in any stream", "B.Ed Passed"))
        self.assertIn("B.Ed", check(profile(), j, TODAY).reasons[0])
        self.assertEqual(check(profile(certificates=["B.Ed"]), j, TODAY).status, "eligible")
        tet = job(title="Super TET Assistant Teacher", quals=("Bachelor Degree", "B.Ed Passed", "BTC / D.El.Ed Passed"))
        self.assertNotEqual(check(profile(certificates=["D.El.Ed"]), tet, TODAY).status, "not_eligible")

    def test_title_medical_officer(self):
        j = job(title="AFMS Medical Officer Online Form 2026", quals=("Maximum Age : 30 Years for UG Degree",))
        r = check(profile(), j, TODAY)
        self.assertEqual(r.status, "not_eligible")
        self.assertIn("MBBS", r.reasons[0])

    def test_specific_degree(self):
        j = job(quals=("Bachelor of Planning",))
        self.assertEqual(check(profile(), j, TODAY).status, "not_eligible")

    def test_subject_needed_is_check(self):
        j = job(quals=("Graduation with Home Science as a main subject",))
        self.assertEqual(check(profile(), j, TODAY).status, "check_manually")
        p = profile(education=[{"level": "graduate", "degree": "B.Sc", "subjects": ["home science"], "percent": 60}])
        self.assertEqual(check(p, j, TODAY).status, "eligible")

    def test_final_year(self):
        p = profile(education=[{"level": "12th"}], pursuing={"level": "graduate", "final_year": True})
        self.assertEqual(check(p, job(quals=("Passed / Appearing Bachelor Degree",)), TODAY).status, "eligible")
        self.assertEqual(check(p, job(), TODAY).status, "check_manually")


class OtherRuleTests(unittest.TestCase):
    def test_closed(self):
        self.assertEqual(check(profile(), job(last="2026-11-01"), TODAY).status, "closed")

    def test_female_only(self):
        j = job(title="UP Anganwadi Worker Online Form 2026", quals=("10+2 Passed",))
        self.assertEqual(check(profile(), j, TODAY).status, "not_eligible")
        self.assertNotIn("Only for female candidates", check(profile(gender="female"), j, TODAY).reasons)

    def test_other_state_is_check(self):
        j = job(title="Bihar BSSC Inter Level Online Form 2026", quals=("10+2 Passed",))
        r = check(profile(domicile="UP"), j, TODAY)
        self.assertEqual(r.status, "check_manually")
        self.assertIn("BR state post", r.reasons[0])

    def test_experience(self):
        j = job(quals=("Graduation in any stream with Minimum 2 Years’ Experience in Bank",))
        self.assertEqual(check(profile(experience_years=1), j, TODAY).status, "not_eligible")
        self.assertEqual(check(profile(experience_years=3), j, TODAY).status, "eligible")

    def test_missing_data_is_check_not_eligible(self):
        j = job(quals=(), age={}, last=None)
        r = check(profile(), j, TODAY)
        self.assertEqual(r.status, "check_manually")
        self.assertEqual(len(r.reasons), 3)

    def test_concerned_subject_is_check(self):
        j = job(quals=("Graduation in Concerned Subject",))
        self.assertEqual(check(profile(), j, TODAY).status, "check_manually")

    def test_engineer_branch(self):
        j = job(title="UPSSSC Junior Engineer Mechanical Online Form", quals=("Diploma in Mechanical Engineering",))
        civil = profile(education=[{"level": "graduate", "degree": "B.Tech", "branch": "Civil Engineering"}])
        self.assertEqual(check(civil, j, TODAY).status, "not_eligible")


if __name__ == "__main__":
    unittest.main()
