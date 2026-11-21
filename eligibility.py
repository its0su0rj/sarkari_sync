#!/usr/bin/env python3
"""
Eligibility engine: matches a student's profile against the jobs in sarkari.db.

Every job gets one of four results, always with reasons:
    eligible        all checkable rules pass
    check_manually  nothing rules the student out, but something can't be confirmed
    not_eligible    a clear rule fails (age, qualification level, gender, experience, marks)
    closed          the last date has passed

Usage
    python eligibility.py profile.json                 # open jobs: eligible + check manually
    python eligibility.py profile.json --all           # also not eligible / closed, with reasons
    python eligibility.py profile.json --out matches.json

The rules are read from the post text by simple patterns. They are a filter to save the
student time, not a final answer: the official notification is the source of truth.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date

# =========================================================================== vocabulary
LEVELS = {"8th": 0, "10th": 1, "12th": 2, "diploma": 2.5, "graduate": 3, "postgraduate": 4, "phd": 5}
LEVEL_NAMES = {v: k for k, v in LEVELS.items()}

# Checked highest first; the highest level named in an option is its requirement.
_LEVEL_RX = [
    ("phd", r"\bph\.?\s?d\b|doctorate"),
    ("postgraduate", r"post\s*-?\s*graduat\w*|\bmaster\w*|\bm\.\s?a\b|\bm\.?\s?sc\b|\bm\.?\s?com\b|\bmba\b|"
                     r"\bm\.?\s?tech\b|\bm\.?\s?e\.|\bmca\b|\bpg\b(?!\s*diploma)|\bm\.?\s?ed\b|\bllm\b|\bmd\b|\bms\b(?=\s)"),
    ("graduate", r"graduat\w*|bachelor\w*|\bdegree\b|\bb\.\s?a\b|\bb\.?\s?sc\b|\bb\.?\s?com\b|\bb\.?\s?tech\b|"
                 r"\bb\.?\s?e\b\.?|\bbca\b|\bbba\b|\bllb\b|\bmbbs\b|\bbams\b|\bbds\b|\bb\.?\s?pharm|\bshastri\b"),
    ("diploma", r"\bdiploma\b|polytechnic"),
    ("12th", r"10\s*\+\s*2|\b12th\b|intermediate|class\s*(?:12|xii)\b|higher\s*secondary|\bsenior\s*secondary"),
    ("10th", r"\b10th\b|high\s*school|matric\w*|class\s*(?:10|x)\b|secondary\s*school|\bssc\s*/"),
    ("8th", r"\b8th\b|class\s*(?:8|viii)\b|junior\s*high\s*school\s*(?:pass|exam)"),
]
_LEVEL_RX = [(lvl, re.compile(rx, re.I)) for lvl, rx in _LEVEL_RX]

# Certificates / professional qualifications that must be held in addition to a level.
TAGS = {
    "iti": r"\biti\b|\bncvt\b|\bscvt\b",
    "bed": r"\bb\.?\s?ed\b",
    "deled": r"\bd\.?\s?el\.?\s?ed\b|\bbtc\b|\bjbt\b|\bd\.?\s?ed\b",
    "tet": r"\bctet\b|\b[a-z]{0,4}tet\b|teacher\s*eligibility",
    "engineering": r"\bb\.?\s?tech\b|\bb\.?\s?e\b\.?|engineering\s*degree|degree\s*in\s*\w*\s*engineering|\bbe\s*/",
    "nursing": r"nursing|\bgnm\b|\banm\b",
    "medical": r"\bmbbs\b|\bbams\b|\bbhms\b|\bbums\b|\bbds\b|dental\s*surgery|medicine\s*and\s*surgery|\bmedical\s*degree|ayurved|homo?eopath|unani|\bayush\b",
    "pharmacy": r"pharm",
    "law": r"\bllb\b|law\s*graduate|degree\s*in\s*law",
    "ca": r"\bca\b|chartered\s*accountant|\bicwa\b|\bcma\b",
    "ccc": r"\bccc\b|o\s*level|course\s*on\s*computer\s*concepts",
    "nursery_teacher": r"\bntt\b|\bdpse\b|nursery\s*teacher|pre\s*-?\s*school\s*education",
    "special_education": r"special\s*education|\brci\b",
    "physiotherapy": r"physiotherap",
    "typing": r"typing|stenograph",
    "driving_licence": r"driving\s*licen[cs]e",
    "pet": r"\bpet\b|preliminary\s*eligibility\s*test",
    "net": r"\bnet\b|\bslet\b|\bset\b(?=\s*/|\s*exam)|\bjrf\b",
}
TAGS = {k: re.compile(v, re.I) for k, v in TAGS.items()}
TAG_NAMES = {"iti": "ITI certificate", "bed": "B.Ed", "deled": "D.El.Ed / BTC", "tet": "TET / CTET",
             "engineering": "B.Tech / B.E", "nursing": "Nursing (GNM/ANM/B.Sc)", "medical": "MBBS / AYUSH degree",
             "pharmacy": "Pharmacy (D/B.Pharm)", "law": "LLB", "ca": "CA / CMA", "ccc": "CCC / O Level",
             "nursery_teacher": "NTT / DPSE", "special_education": "Special Education (RCI)",
             "physiotherapy": "Physiotherapy", "typing": "Typing / Steno", "driving_licence": "Driving licence",
             "pet": "UPSSSC PET scorecard", "net": "NET / SET"}

# Missing one of these certificates is a clear "not eligible" (when the student says their list is complete).
HARD_TAGS = {"iti", "nursing", "medical", "engineering", "pharmacy", "law", "bed", "deled", "physiotherapy", "ca"}
# Teacher training: either one is usually accepted.
TEACHER_TRAINING = {"bed", "deled"}
# Certificates that are alternatives to a degree (a line naming only them is an option, not an extra rule).
ALT_TAGS = {"iti", "nursing", "nursery_teacher", "special_education", "physiotherapy", "pharmacy"}

# Requirements implied by the post name, applied on top of the text.
TITLE_RULES = [
    # (pattern, kind, value, reason)
    (r"medical\s*officer|senior\s*resident|\bmbbs\b", "tag", "medical", None),
    (r"staff\s*nurse|nursing\s*officer", "tag", "nursing", None),
    (r"\bpharmacist\b", "tag", "pharmacy", None),
    (r"junior\s*engineer|assistant\s*engineer|engineer\s*trainee|\bje\b(?!e)|\bae\b", "engineer", None, None),
    (r"assistant\s*professor|\blecturer\b|\bpgt\b", "level", "postgraduate", "teaching post"),
    (r"\btechnician\b", "maybe", None, "Technician posts usually need ITI or a diploma"),
    (r"\bdriver\b", "maybe", None, "Driver posts need a valid driving licence"),
    (r"upsssc(?!.*\bpet\b\s*(?:20\d\d)?\s*(?:online|admit|result|answer))", "pet", None, None),
]
TITLE_RULES = [(re.compile(rx, re.I), k, v, why) for rx, k, v, why in TITLE_RULES]

SUBJECTS = ["hindi", "english", "sanskrit", "urdu", "mathematics", "physics", "chemistry", "zoology", "botany",
            "biology", "home science", "computer", "commerce", "economics", "history", "geography",
            "political science", "sociology", "agriculture", "civil", "mechanical", "electrical", "electronics",
            "statistics", "psychology", "fine art", "music", "physical education", "library", "veterinary",
            "nutrition", "social work", "information technology", "aeronautical"]
_SUBJ_ALIASES = {"maths": "mathematics", "math": "mathematics", "it": "information technology",
                 "cs": "computer", "computer science": "computer", "pcm": "mathematics", "eee": "electrical",
                 "ece": "electronics", "cse": "computer", "me": "mechanical", "ce": "civil", "home sc": "home science"}
_ANY = re.compile(r"\bany\s*(?:stream|discipline|subject|degree|graduat\w*|branch|faculty|field)|all\s*stream", re.I)
_PCT = re.compile(r"(\d{2})(?:\.\d+)?\s*%", re.I)
_EXP = re.compile(r"(\d{1,2})\s*(?:\+\s*)?years?['’]?\s*(?:of\s*)?(?:\w+\s*){0,3}?experience", re.I)
_APPEARING = re.compile(r"appearing|final\s*year|awaiting|result\s*awaited", re.I)
_OR = re.compile(r"\s+\bor\b\s+|\s*/\s*(?=(?:\d+\s*years?\s*)?(?:diploma|graduat|bachelor|master|post|b\.|m\.|10|12|iti)\b)",
                 re.I)

# =========================================================================== age relaxation
CENTRAL_RELAX = {"GEN": 0, "EWS": 0, "OBC": 3, "SC": 5, "ST": 5}
CENTRAL_PWD = 10
# States where the rule is well known. Relaxation applies only to the state's own domicile.
STATE_RELAX = {
    "UP": ({"GEN": 0, "EWS": 0, "OBC": 5, "SC": 5, "ST": 5}, 15),
}

STATES = {
    "UP": r"\bup\b|upsssc|uppsc|upessc|uppbpb|upprpb|uttar\s*pradesh|sewayojan|\bkgbv\b|ecce\s*educator",
    "BR": r"bihar|\bbpsc\b|\bbssc\b|\bcsbc\b|\bbtsc\b",
    "RJ": r"rajasthan|\brpsc\b|rsmssb|\brssb\b",
    "MP": r"\bmp\b|mppsc|mpesb|madhya\s*pradesh",
    "HP": r"hppsc|hpssc|himachal|\bhp\b",
    "UK": r"uttarakhand|ukpsc|uksssc",
    "HR": r"haryana|\bhpsc\b|\bhssc\b",
    "PB": r"punjab|\bppsc\b|psssb",
    "DL": r"delhi|dsssb",
    "JH": r"jharkhand|\bjssc\b|\bjpsc\b",
    "CG": r"chhattisgarh|cgpsc|cg\s*vyapam",
    "GJ": r"gujarat|\bgpsc\b|gsssb",
    "MH": r"maharashtra|\bmpsc\b",
    "WB": r"west\s*bengal|wbpsc|wbssc",
    "OD": r"odisha|\bopsc\b|\bossc\b",
    "TN": r"tamil\s*nadu|tnpsc",
    "KA": r"karnataka|\bkpsc\b",
    "AP": r"andhra|appsc",
    "TS": r"telangana|tspsc|tgpsc",
    "AS": r"\bassam\b|\bapsc\b",
    "JK": r"jammu|jkssb|jkpsc",
}
STATES = {k: re.compile(v, re.I) for k, v in STATES.items()}
_CENTRAL_HINT = re.compile(r"\bupsc\b|\bssc\b|railway|\brrb\b|\brrc\b|\bibps\b|\bsbi\b|\bnta\b|army|navy|air\s*force|"
                           r"rifles|\bindian\b|national|central|\bnpcil\b|\bongc\b|\bpgcil\b|power\s*grid|"
                           r"\brites\b|\bbhu\b|supreme\s*court|\bafms\b|\bicar\b|\basrb\b|\bnielit\b|"
                           r"\bbsf\b|\bcrpf\b|\bcisf\b|\bitbp\b|\bssb\b|\bdrdo\b|\bisro\b|\bbhel\b|\bntpc\b|"
                           r"\baiims\b|\besic\b|\bkvs\b|\bnvs\b|\bindia\b", re.I)
# Category age relaxation does not apply to school / college entrance tests or most defence officer entries.
_NO_RELAX = re.compile(r"\bnta\b|aissee|sainik|admission|entrance|\bjee\b|\bneet\b|\bcuet\b|cadet\s*college|"
                       r"\bnda\b|\bcds\b|afcat|agniveer|\bnavy\b|air\s*force|territorial\s*army", re.I)
_FEMALE_ONLY = re.compile(r"anganwadi|aanganwadi|\bmahila\b|\bkgbv\b|kasturba|female\s*only|only\s*female|"
                          r"women\s*only|only\s*women|female\s*candidates\s*only|\(female\)", re.I)


# =========================================================================== requirements
@dataclass
class Option:
    """One way to qualify, e.g. 'Graduate in Civil Engineering with 50% marks'."""
    text: str
    level: str | None = None
    tags: set = field(default_factory=set)
    subjects: set = field(default_factory=set)
    min_percent: int | None = None
    appearing_ok: bool = False
    any_stream: bool = False
    specific_degree: str | None = None    # e.g. "planning" from "Bachelor of Planning"
    unknown_field: str | None = None      # "Degree in Fisheries": a field we can't verify


_SPECIFIC = re.compile(r"(?:bachelor|master)\s*(?:'?s)?\s*of\s*([a-z]+(?:\s+[a-z]+)?)", re.I)
_GENERIC_DEGREES = {"arts", "science", "commerce", "any", "degree", "recognized", "recognised"}
_DEGREE_IN = re.compile(r"(?:degree|graduat\w*|diploma|bachelor\w*|master\w*|b\.?\s?sc|m\.?\s?sc|b\.?\s?a|m\.?\s?a)\.?"
                        r"\s*(?:\(\w+\)\s*)?in\s+([a-z]+)", re.I)
_RELATED = {"related", "relevant", "concerned", "respective", "appropriate"}
_FILLER = {"any", "the", "a", "an", "all", "first", "second", "full", "regular"}
_DEGREE_TAG = {"technology": "engineering", "engineering": "engineering", "education": "bed", "laws": "law",
               "law": "law", "pharmacy": "pharmacy", "dental": "medical", "medicine": "medical",
               "physiotherapy": "physiotherapy", "nursing": "nursing"}


@dataclass
class Requirements:
    options: list                   # OR: qualify through any one
    common_tags: set                # AND: needed whatever the option ("B.Ed Passed", "CCC Passed")
    experience_years: int | None
    state: str | None
    female_only: bool
    district_level: bool
    marks_relaxation: bool
    title_rules: list = field(default_factory=list)   # (kind, value, reason)
    no_relaxation: bool = False                       # entrance exams / defence entries
    branch: str | None = None                         # engineering branch named in the title
    teacher_training: bool = False                    # needs B.Ed or D.El.Ed (either)


def _subjects_in(text: str) -> set:
    low = text.lower()
    found = {s for s in SUBJECTS if re.search(rf"\b{re.escape(s)}\b", low)}
    for alias, s in _SUBJ_ALIASES.items():
        if len(alias) > 3 and re.search(rf"\b{re.escape(alias)}\b", low):
            found.add(s)
    return found


def _level_of(text: str) -> str | None:
    for lvl, rx in _LEVEL_RX:
        if rx.search(text):
            return lvl
    return None


def _tags_of(text: str) -> set:
    return {t for t, rx in TAGS.items() if rx.search(text)}


def parse_option(text: str) -> Option:
    o = Option(text=text)
    o.level = _level_of(text)
    o.tags = _tags_of(text)
    # A degree implies its level; the tag is what matters.
    if o.tags & {"engineering", "law", "medical"} and (o.level is None or LEVELS[o.level] < 3):
        o.level = "graduate"
    if o.tags == {"engineering"} and o.level == "diploma":
        o.tags = set()                            # "Diploma in Civil Engineering": a diploma, not a B.Tech
    if "iti" in o.tags and o.level is None:
        o.level = "10th"
    o.any_stream = bool(_ANY.search(text))
    if not o.any_stream:
        o.subjects = _subjects_in(text)
    m = _PCT.search(text)
    if m and 33 <= int(m.group(1)) <= 90:
        o.min_percent = int(m.group(1))
    o.appearing_ok = bool(_APPEARING.search(text))
    m = _SPECIFIC.search(text)
    if m:
        words = m.group(1).lower().split()
        if words and words[0] not in _GENERIC_DEGREES:
            tag = _DEGREE_TAG.get(words[0])
            if tag:
                o.tags.add(tag)
            else:
                o.specific_degree = words[0]
    m = _DEGREE_IN.search(text)
    if m and not o.any_stream and not o.subjects and not o.tags and not o.specific_degree:
        word = m.group(1).lower()
        if word in _RELATED:
            o.unknown_field = "the related field / trade"
        elif word not in _FILLER:
            o.unknown_field = word
    return o


_JUNK = re.compile(r"^step\s*\d|candidates?\s*will\s*have\s*to|how\s*to|read\s*the\s*notification|"
                   r"\bexam\b\s*$|selection\s*process|pay\s*scale|syllabus|recruitment\s*20\d\d|online\s*form|"
                   r"apply\s*online|notification|\bage\b\s*(?:limit|:)|maximum\s*age|minimum\s*age|\bfee\b|"
                   r"\d+\s*/-|vacancy|post\s*date|exam\s*date|admit\s*card|salary|pay\s*level|age\s*relaxation|"
                   r"^(?:graduate|diploma|trade)(?:\s*[,&]\s*(?:graduate|diploma|trade))+\s*apprentice", re.I)


REQ_VERSION = 1


def requirements_to_json(req: Requirements) -> dict:
    """Pre-parsed rules, stored in jobs.json so phones and the bot skip the slow parsing step."""
    return {
        "v": REQ_VERSION,
        "options": [{"text": o.text, "level": o.level, "tags": sorted(o.tags), "subjects": sorted(o.subjects),
                     "min_percent": o.min_percent, "appearing_ok": o.appearing_ok, "any_stream": o.any_stream,
                     "specific_degree": o.specific_degree, "unknown_field": o.unknown_field} for o in req.options],
        "common_tags": sorted(req.common_tags), "experience_years": req.experience_years, "state": req.state,
        "female_only": req.female_only, "district_level": req.district_level,
        "marks_relaxation": req.marks_relaxation, "title_rules": [list(r) for r in req.title_rules],
        "no_relaxation": req.no_relaxation, "branch": req.branch, "teacher_training": req.teacher_training,
    }


def requirements_from_json(d: dict) -> Requirements:
    opts = [Option(text=o["text"], level=o["level"], tags=set(o["tags"]), subjects=set(o["subjects"]),
                   min_percent=o["min_percent"], appearing_ok=o["appearing_ok"], any_stream=o["any_stream"],
                   specific_degree=o["specific_degree"], unknown_field=o["unknown_field"]) for o in d["options"]]
    return Requirements(options=opts, common_tags=set(d["common_tags"]), experience_years=d["experience_years"],
                        state=d["state"], female_only=d["female_only"], district_level=d["district_level"],
                        marks_relaxation=d["marks_relaxation"], title_rules=[tuple(r) for r in d["title_rules"]],
                        no_relaxation=d["no_relaxation"], branch=d["branch"], teacher_training=d["teacher_training"])


def job_requirements(job: dict) -> Requirements:
    r = job.get("req")
    if isinstance(r, dict) and r.get("v") == REQ_VERSION:
        return requirements_from_json(r)
    return parse_requirements(job)


def parse_requirements(job: dict) -> Requirements:
    title = job.get("title") or ""
    lines = [q for q in (job.get("qualification_text") or []) if not _JUNK.search(q)]
    options, common = [], set()
    for line in lines:
        pieces = [p.strip(" .,;:()") for p in _OR.split(line) if p and p.strip(" .,;:()")]
        line_opts = []
        for piece in pieces:
            o = parse_option(piece)
            if o.level is None and not o.tags:
                if line_opts:                     # a fragment such as "Hindi" after "M.A. in English or"
                    line_opts[-1].subjects |= _subjects_in(piece)
                continue
            line_opts.append(o)
        # A line naming only a certificate: "B.Ed Passed" is an extra rule for every option,
        # but "NTT / DPSE" or "ITI in related trade" is another way to qualify.
        if line_opts and all(o.level is None for o in line_opts):
            for o in line_opts:
                if o.tags & ALT_TAGS:
                    o.level = "10th" if o.tags == {"iti"} else "12th"
                    options.append(o)
                else:
                    common |= o.tags
            continue
        options.extend(o for o in line_opts if o.level is not None)

    full = " ".join(lines)
    exp = _EXP.search(full)
    text_all = f"{title} {job.get('link', '')} {' '.join(job.get('categories') or [])}"
    state = None
    if not _CENTRAL_HINT.search(title):
        for code, rx in STATES.items():
            if rx.search(text_all):
                state = code
                break
    rules = [(k, v, why) for rx, k, v, why in TITLE_RULES if rx.search(title)]
    no_relax = bool(_NO_RELAX.search(title))
    branch = next((b for b in ("civil", "mechanical", "electrical", "electronics", "computer")
                   if re.search(rf"\b{b}\b", title, re.I)), None)
    teacher_training = bool(common & TEACHER_TRAINING) and len(common & TEACHER_TRAINING) > 1
    if teacher_training:
        common -= TEACHER_TRAINING
    return Requirements(
        options=options,
        common_tags=common,
        title_rules=rules,
        no_relaxation=no_relax,
        branch=branch,
        teacher_training=teacher_training,
        experience_years=int(exp.group(1)) if exp else None,
        state=state,
        female_only=bool(_FEMALE_ONLY.search(f"{title} {full}")),
        district_level=bool(re.search(r"\bdistrict\b|\bjila\b", title, re.I)) or "ecce educator" in title.lower(),
        marks_relaxation=bool(re.search(r"relaxation\s*in\s*(?:the\s*)?minimum\s*marks|marks\s*relaxation", full, re.I)),
    )


# =========================================================================== profile
@dataclass
class Profile:
    dob: date
    gender: str                     # male | female | other
    category: str                   # GEN | EWS | OBC | SC | ST
    pwd: bool
    ex_serviceman: bool
    domicile: str | None            # state code, e.g. "UP"
    level: float                    # highest completed level
    pursuing_level: float | None    # in final year of this level
    tags: set
    subjects: set
    percents: dict                  # level name -> percent
    experience_years: float
    tags_complete: bool
    degrees_text: str = ""

    @classmethod
    def load(cls, d: dict) -> "Profile":
        edu = d.get("education") or []
        level, percents, tags, subjects, degrees = -1.0, {}, set(), set(), []
        for e in edu:
            lvl = e.get("level")
            if lvl not in LEVELS:
                raise ValueError(f"education level must be one of {list(LEVELS)}, got {lvl!r}")
            level = max(level, LEVELS[lvl])
            if e.get("percent") is not None:
                percents[lvl] = float(e["percent"])
            desc = " ".join(str(e.get(k, "")) for k in ("degree", "stream", "branch", "name"))
            desc += " " + " ".join(e.get("subjects") or [])
            degrees.append(desc.lower())
            tags |= _tags_of(desc)
            subjects |= _subjects_in(desc) | {s.lower() for s in e.get("subjects") or []}
            if lvl == "diploma":
                tags.add("diploma")
            if lvl == "phd" or lvl == "postgraduate":
                tags.discard("diploma")
        for c in d.get("certificates") or []:
            tags |= _tags_of(c) or {c.lower()}
        subjects = {_SUBJ_ALIASES.get(s, s) for s in subjects}
        pursuing = d.get("pursuing") or {}
        p_level = LEVELS.get(pursuing.get("level")) if pursuing.get("final_year") else None
        if pursuing:
            desc = " ".join(str(pursuing.get(k, "")) for k in ("degree", "stream", "branch"))
            subjects |= _subjects_in(desc)
        cat = (d.get("category") or "GEN").upper().replace("GENERAL", "GEN").replace("UR", "GEN")
        cat = "OBC" if cat.startswith("OBC") else cat
        if cat not in CENTRAL_RELAX:
            raise ValueError("category must be GEN, EWS, OBC, SC or ST")
        return cls(
            dob=date.fromisoformat(d["dob"]),
            gender=(d.get("gender") or "").lower(),
            category=cat,
            pwd=bool(d.get("pwd")),
            ex_serviceman=bool(d.get("ex_serviceman")),
            domicile=(d.get("domicile") or "").upper() or None,
            level=level,
            pursuing_level=p_level,
            tags=tags,
            subjects=subjects,
            percents=percents,
            experience_years=float(d.get("experience_years") or 0),
            tags_complete=bool(d.get("certificates_complete", True)),
            degrees_text=" ".join(degrees),
        )


def age_on(dob: date, on: date) -> tuple[int, int]:
    """Completed years and months on a date."""
    months = (on.year - dob.year) * 12 + (on.month - dob.month) - (1 if on.day < dob.day else 0)
    return months // 12, months % 12


# =========================================================================== check
@dataclass
class Result:
    status: str
    reasons: list           # why not eligible / what to check
    notes: list             # helpful facts (relaxation used, deadline text...)
    age: str | None = None
    via: str | None = None  # the qualification option that matched


def _relaxation(p: Profile, req: Requirements) -> tuple[int, str | None]:
    """Years of upper-age relaxation, and a caveat if the rule is uncertain."""
    if req.no_relaxation:
        return 0, None
    if req.state:
        if p.domicile != req.state:
            return 0, None                      # other-state candidates are treated as General
        if req.state in STATE_RELAX:
            table, pwd = STATE_RELAX[req.state]
            return table[p.category] + (pwd if p.pwd else 0), None
        years = CENTRAL_RELAX[p.category] + (CENTRAL_PWD if p.pwd else 0)
        return years, f"{req.state} relaxation rules may differ"
    return CENTRAL_RELAX[p.category] + (CENTRAL_PWD if p.pwd else 0), None


def _check_age(p, job, req, today, fail, check, notes):
    age = job.get("age") or {}
    on = date.fromisoformat(age["as_on"]) if age.get("as_on") else None
    if on is None:
        last = (job.get("dates") or {}).get("last_date")
        on = date.fromisoformat(last) if last else today
        if age.get("min") or age.get("max"):
            notes.append(f"Age date not stated; counted on {on.isoformat()}")
    years, months = age_on(p.dob, on)
    shown = f"{years}y {months}m on {on.isoformat()}"
    if age.get("not_specified") or not (age.get("min") or age.get("max") or age.get("by_post")):
        if not age.get("not_specified"):
            check.append("Age limit not found in the post; read the notification")
        return shown

    ranges = age.get("by_post") or [{"post": None, "min": age.get("min"), "max": age.get("max")}]
    relax, caveat = _relaxation(p, req)
    # "Maximum 27 years" = must not have completed 28 years on the date (27y 11m is fine).
    best = None
    for r in ranges:
        lo, hi = r.get("min"), r.get("max")
        if lo is not None and years < lo:
            continue
        if hi is None or years <= hi:
            best = (r, 0)
            break
        if relax and years <= hi + relax and best is None:
            best = (r, relax)
    if best is None:
        lo_all = min((r["min"] for r in ranges if r.get("min") is not None), default=None)
        hi_all = max((r["max"] for r in ranges if r.get("max") is not None), default=None)
        if lo_all is not None and years < lo_all:
            fail.append(f"Too young: {years} years on {on.isoformat()}, minimum {lo_all}")
            return shown
        upto = age.get("max_upto")
        if upto and years <= upto + relax:
            check.append(f"Age {years}: above the {hi_all}-year limit but within {upto}"
                         f"{f' (+{relax})' if relax else ''} allowed for some posts; check which posts")
            return shown
        msg = f"Over age: {years}y {months}m on {on.isoformat()}, maximum {hi_all}"
        if relax:
            msg += f" (+{relax} relaxation = {hi_all + relax})"
        if p.ex_serviceman:
            check.append(msg + "; ex-serviceman relaxation may apply")
        elif req.state and p.domicile != req.state and p.category in ("OBC", "SC", "ST"):
            fail.append(msg + f"; category relaxation is only for {req.state} domicile")
        else:
            fail.append(msg)
        return shown
    r, used = best
    if r.get("post"):
        notes.append(f"Age fits: {r['post']} ({r.get('min')}-{r.get('max')})")
    if used:
        if caveat:
            check.append(f"Eligible only with {used}-year {p.category}{'/PwD' if p.pwd else ''} relaxation; {caveat}")
        else:
            notes.append(f"Uses {used}-year {p.category}{'/PwD' if p.pwd else ''} age relaxation")
    return shown


def _option_result(p: Profile, o: Option, req: Requirements):
    """('ok'|'maybe'|'no', reason)"""
    need = LEVELS[o.level]
    have = p.level
    if have < need:
        if o.appearing_ok and p.pursuing_level is not None and p.pursuing_level >= need:
            pass                                     # final-year students allowed
        elif p.pursuing_level is not None and p.pursuing_level >= need:
            return "maybe", f"needs {o.level}; final-year students only if the notification allows"
        else:
            return "no", f"needs {o.level}"
    # Diploma / ITI posts: need that certificate, or a degree in the same subject.
    if o.level == "diploma" and "diploma" not in p.tags:
        if not (have >= 3 and o.subjects and o.subjects & p.subjects):
            return ("no" if p.tags_complete else "maybe"), "needs a diploma" + (
                f" in {', '.join(sorted(o.subjects))}" if o.subjects else "")
    missing = (o.tags - p.tags) - {"diploma"}
    if missing:
        names = ", ".join(TAG_NAMES.get(t, t) for t in sorted(missing))
        return ("no" if p.tags_complete else "maybe"), f"needs {names}"
    if o.specific_degree and o.specific_degree not in p.degrees_text:
        return "no", f"needs Bachelor/Master of {o.specific_degree.title()}"
    if o.unknown_field and o.unknown_field not in p.degrees_text and o.unknown_field not in p.subjects:
        return "maybe", f"needs a qualification in {o.unknown_field}"
    if o.subjects and not (o.subjects & p.subjects):
        return "maybe", f"needs subject: {', '.join(sorted(o.subjects))}"
    if o.min_percent:
        got = p.percents.get(o.level) if o.level != "diploma" else p.percents.get("diploma")
        if got is None:
            return "maybe", f"needs {o.min_percent}% at {o.level}; add your marks to check"
        if got < o.min_percent:
            if req.marks_relaxation and p.category in ("SC", "ST", "OBC") or p.pwd:
                return "maybe", f"needs {o.min_percent}% (you have {got:g}%); reserved-category marks relaxation may apply"
            return "no", f"needs {o.min_percent}% at {o.level} (you have {got:g}%)"
    return "ok", None


def check(profile: Profile, job: dict, today: date | None = None) -> Result:
    today = today or date.today()
    req = job_requirements(job)
    fail, check_, notes = [], [], []
    dates = job.get("dates") or {}
    links = job.get("links") or {}

    # deadline
    last = dates.get("last_date")
    if last and date.fromisoformat(last) < today:
        return Result("closed", [f"Last date was {last}"], [])
    if not last:
        txt = (job.get("date_notes") or {}).get("last_date")
        check_.append(f"Last date: {txt}" if txt else "Last date not found; read the notification")
    if links.get("apply_opens") and date.fromisoformat(links["apply_opens"]) > today:
        notes.append(f"Apply link opens on {links['apply_opens']}")

    # gender
    if req.female_only and profile.gender == "male":
        fail.append("Only for female candidates")

    # age
    age_txt = _check_age(profile, job, req, today, fail, check_, notes)

    # qualification
    via = None
    missing_common = req.common_tags - profile.tags
    hard = sorted(missing_common & HARD_TAGS)
    soft = sorted(missing_common - HARD_TAGS)
    if hard:
        names = ", ".join(TAG_NAMES.get(t, t) for t in hard)
        (fail if profile.tags_complete else check_).append(f"Also needs {names}")
    if soft:
        check_.append("May also need " + ", ".join(TAG_NAMES.get(t, t) for t in soft))
    if req.teacher_training and not (profile.tags & TEACHER_TRAINING):
        (fail if profile.tags_complete else check_).append("Needs teacher training (B.Ed or D.El.Ed / BTC)")
    # "Junior Engineer and MTS", "Various Post": the title rule covers only some of the posts.
    multi = bool(re.search(r"\band\b|&|various|multiple|\bmts\b|non\s*teaching", job.get("title") or "", re.I))
    strict = fail if (profile.tags_complete and not multi) else check_
    for kind, value, why in req.title_rules:
        if kind == "tag" and value not in profile.tags:
            strict.append(f"Post needs {TAG_NAMES[value]}" + (" (for some posts)" if multi else ""))
        elif kind == "engineer" and not ({"engineering", "diploma"} & profile.tags):
            strict.append("Engineer post: needs B.Tech / B.E or an engineering diploma" + (" (for some posts)" if multi else ""))
        elif kind == "engineer" and req.branch and req.branch not in profile.subjects:
            strict.append(f"Engineer post for {req.branch.title()} branch")
        elif kind == "level" and profile.level < LEVELS[value]:
            (check_ if multi else fail).append(f"Needs {value} ({why})")
        elif kind == "pet" and "pet" not in profile.tags:
            check_.append("UPSSSC posts need a valid UPSSSC PET scorecard (add 'UPSSSC PET' to certificates if you have it)")
        elif kind == "maybe":
            if not ("iti" in profile.tags or "diploma" in profile.tags) or "licence" in why:
                if not ("licence" in why and "driving_licence" in profile.tags):
                    check_.append(why)
    if not req.options:
        check_.append("Qualification not found in the post; read the notification")
    else:
        results = [(o, *_option_result(profile, o, req)) for o in req.options]
        ok = [o for o, r, _ in results if r == "ok"]
        maybe = [(o, why) for o, r, why in results if r == "maybe"]
        if ok:
            via = ok[0].text[:120]
        elif maybe:
            check_.append(f"Qualification: {maybe[0][1]}")
            via = maybe[0][0].text[:120]
        else:
            levels = sorted({o.level for o in req.options}, key=lambda lv: LEVELS[lv])
            easiest = min(results, key=lambda r: LEVELS[r[0].level])
            reason = easiest[2] if len(results) == 1 else \
                f"Qualification not met (lowest accepted: {levels[0]}; {easiest[2]})"
            fail.append(reason if reason.startswith("Qual") else f"Qualification: {reason}")

    # experience
    if req.experience_years:
        if profile.experience_years >= req.experience_years:
            notes.append(f"Needs {req.experience_years} years experience (you have {profile.experience_years:g})")
        elif profile.experience_years == 0:
            check_.append(f"Mentions {req.experience_years} years experience; may apply to some posts only")
        else:
            fail.append(f"Needs {req.experience_years} years experience (you have {profile.experience_years:g})")

    # domicile
    if req.state and profile.domicile != req.state:
        if req.district_level:
            check_.append(f"{req.state} district-level post: usually only for local residents")
        else:
            check_.append(f"{req.state} state post: check whether other-state candidates can apply")
    elif req.district_level:
        notes.append("District-level post: check the district residence rule")

    if fail:
        return Result("not_eligible", fail, notes, age_txt, via)
    if check_:
        return Result("check_manually", check_, notes, age_txt, via)
    return Result("eligible", [], notes, age_txt, via)


# =========================================================================== CLI
def load_jobs(db_path: str) -> list[dict]:
    """Jobs from sarkari.db, or from a JSON export made by `sync.py --export`."""
    if db_path.lower().endswith(".json"):
        with open(db_path, encoding="utf-8") as fh:
            return [j for j in json.load(fh) if j.get("kind", "job") == "job"]
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT link, title, categories, fields FROM posts WHERE kind='job'").fetchall()
    db.close()
    jobs = []
    for link, title, cats, fields in rows:
        j = json.loads(fields or "{}")
        j.update(link=link, title=title, categories=json.loads(cats or "[]"))
        jobs.append(j)
    return jobs


def match(profile: Profile, jobs: list[dict], today: date | None = None) -> list[dict]:
    order = {"eligible": 0, "check_manually": 1, "not_eligible": 2, "closed": 3}
    out = []
    for j in jobs:
        r = check(profile, j, today)
        out.append({"status": r.status, "title": j["title"], "link": j["link"],
                    "last_date": (j.get("dates") or {}).get("last_date"),
                    "apply": (j.get("links") or {}).get("apply") or (j.get("links") or {}).get("apply_offline"),
                    "notification": (j.get("links") or {}).get("notification"),
                    "total_posts": j.get("total_posts"), "age": r.age, "qualified_via": r.via,
                    "reasons": r.reasons, "notes": r.notes})
    out.sort(key=lambda m: (order[m["status"]], m["last_date"] or "9999"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Match a student profile against synced jobs.")
    ap.add_argument("profile", help="profile JSON file")
    ap.add_argument("--db", default="sarkari.db", help="sarkari.db or a JSON export")
    ap.add_argument("--out", default="matches.json")
    ap.add_argument("--all", action="store_true", help="also list not eligible and closed jobs")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with open(args.profile, encoding="utf-8") as fh:
        profile = Profile.load(json.load(fh))
    results = match(profile, load_jobs(args.db))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("eligible", "check_manually", "not_eligible", "closed")}
    print(f"Eligible: {counts['eligible']}   Check manually: {counts['check_manually']}   "
          f"Not eligible: {counts['not_eligible']}   Closed: {counts['closed']}\n")
    show = ("eligible", "check_manually") + (("not_eligible", "closed") if args.all else ())
    labels = {"eligible": "ELIGIBLE", "check_manually": "CHECK MANUALLY",
              "not_eligible": "NOT ELIGIBLE", "closed": "CLOSED"}
    for status in show:
        group = [r for r in results if r["status"] == status]
        if not group:
            continue
        print(f"=== {labels[status]} ({len(group)}) ===")
        for r in group:
            print(f"- {r['title']}  [last date: {r['last_date'] or '?'}]")
            for x in r["reasons"]:
                print(f"    ! {x}")
            for x in r["notes"]:
                print(f"    * {x}")
        print()
    print(f"Full results with links saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
