"""
Turn a sarkariresult.com post body (HTML) into structured facts:
dates, age limits, fees, total posts, qualification text and key links.

Works on both layouts the site uses (bullet lists under headings, and
two-column tables), because it reads the page as a list of text lines
and looks for labels, not fixed positions.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BLOCKS = ["p", "li", "td", "th", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
          "div", "dd", "dt", "blockquote", "caption", "section", "table", "ul", "ol"]
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS.sub(" ", (s or "").replace("\xa0", " ")).strip()


# --------------------------------------------------------------------------- text
def html_to_lines(html: str) -> list[str]:
    """Each block element (row cell, list item, heading, paragraph) becomes a line."""
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup(["script", "style", "noscript", "iframe", "svg"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for el in soup.find_all(BLOCKS):
        el.insert_before("\n")
        el.insert_after("\n")
    lines = []
    for part in soup.get_text("").split("\n"):
        part = _clean(part)
        if part:
            lines.append(part)
    return lines


# --------------------------------------------------------------------------- dates
_MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = re.compile(r"""
    (?<!\d)(?P<d1>\d{1,2})[./-](?P<m1>\d{1,2})[./-](?P<y1>\d{4})(?!\d)
  | (?<!\d)(?P<d2>\d{1,2})(?:st|nd|rd|th)?[\s-]+(?P<mn2>[A-Za-z]{3,9})\.?,?[\s-]+(?P<y2>\d{4})(?!\d)
  | \b(?P<mn3>[A-Za-z]{3,9})\.?\s+(?P<d3>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y3>\d{4})(?!\d)
""", re.X)


def find_date(s: str) -> str | None:
    """First valid date in the text, as YYYY-MM-DD. Numeric dates are read as DD/MM/YYYY."""
    for m in _DATE.finditer(s or ""):
        try:
            if m.group("d1"):
                d, mo, y = int(m.group("d1")), int(m.group("m1")), int(m.group("y1"))
            elif m.group("d2"):
                mo = _MON.get(m.group("mn2")[:3].lower())
                d, y = int(m.group("d2")), int(m.group("y2"))
            else:
                mo = _MON.get(m.group("mn3")[:3].lower())
                d, y = int(m.group("d3")), int(m.group("y3"))
            if not mo or not (2000 <= y <= 2100):
                continue
            return date(y, mo, d).isoformat()
        except ValueError:
            continue
    return None


# Checked in order; the first label that matches a line wins for that line.
DATE_LABELS = [
    ("fee_last_date", ("last date for fee", "fee payment last", "pay exam fee", "fee last date",
                       "last date fee", "fee payment date")),
    ("correction", ("correction",)),
    ("application_start", ("application begin", "application start", "apply start", "starting date",
                           "start date", "online apply start", "registration start", "opening date")),
    ("last_date", ("last date", "closing date", "end date", "registration last", "apply last")),
    ("exam_date", ("exam date", "examination date", "cbt date", "test date")),
    ("admit_card", ("admit card",)),
    ("result", ("result",)),
]


def _is_value_only(line: str) -> bool:
    """A line that is just a date (the value cell next to a label cell)."""
    if len(line) > 40 or ":" in line or find_date(line) is None:
        return False
    return not re.search(r"[A-Za-z]{3,}", _DATE.sub("", line))


def _dates(lines):
    """Returns (dates, notes). notes holds text values such as 'District Wise' or 'As per Schedule'."""
    out, notes = {}, {}
    for i, line in enumerate(lines):
        if len(line) > 140 or line.endswith("?") or "sarkari" in line.lower():
            continue
        low = line.lower()
        for key, words in DATE_LABELS:
            if any(w in low for w in words):
                v = find_date(line)
                has_text_value = ":" in line and line.split(":", 1)[1].strip() != ""
                if v is None and not has_text_value and i + 1 < len(lines) and _is_value_only(lines[i + 1]):
                    v = find_date(lines[i + 1])
                if v:
                    out.setdefault(key, v)
                elif ":" in line and key not in out:
                    text = line.split(":", 1)[1].strip()
                    if 1 < len(text) <= 40:
                        notes.setdefault(key, text)
                break
    for k in list(notes):
        if k in out:
            del notes[k]
    return out, notes


# --------------------------------------------------------------------------- age
_NUM = re.compile(r"(?<!\d)(\d{2})(?!\d)")
_MIN = re.compile(r"\bmin(?:imum)?\.?\s*age", re.I)
_MAX = re.compile(r"\bmax(?:imum)?\.?\s*age", re.I)
_RANGE = re.compile(r"age\s*limit[^0-9]{0,40}?(\d{2})\s*(?:-|to|–)\s*(\d{2})\s*(?:years?|yrs?)", re.I)


def _age_after(lines, i, start):
    own = lines[i][start:]
    # "Minimum Age : NA" has its own value; never borrow the next line's number then.
    has_value = re.sub(r"[\s:\-–]", "", own) != ""
    nxt = lines[i + 1] if i + 1 < len(lines) and not has_value and len(lines[i + 1]) <= 20 else ""
    for text in (own, nxt):
        m = _NUM.search(text)
        if m and 14 <= int(m.group(1)) <= 70:
            return int(m.group(1))
    return None


_ANY_RANGE = re.compile(r"(?<!\d)(\d{2})\s*(?:-|to|–)\s*(\d{2})\s*(?:years?|yrs?)\b", re.I)
_NA = re.compile(r":\s*(?:na|n/a|not applicable|nil|no age limit|as per rules?)\.?\s*$", re.I)


def _age(lines):
    age = {}
    by_post = []
    window = 0                                    # lines left in the current "Age Limit" block
    for i, line in enumerate(lines):
        low_full = line.lower()
        if "age limit" in low_full and len(line) <= 160:
            window = 8
        elif window:
            window -= 1
            if any(w in low_full for w in ("vacancy", "important dates", "application fee", "eligibility")):
                window = 0
        if window and len(line) <= 160:
            m = _ANY_RANGE.search(line)
            if m and not re.search(r"\bage\s*limit\b", low_full):
                label = line[:m.start()].strip(" :-–|")
                lo, hi = int(m.group(1)), int(m.group(2))
                if 14 <= lo <= hi <= 70:
                    if _MAX.search(label):        # "Maximum Age : 27-32 Years" = max differs by post
                        age.setdefault("max", lo)
                        age["max_upto"] = hi
                        continue
                    if _MIN.search(label):        # "Minimum Age : 18-21 Years"
                        age.setdefault("min", lo)
                        continue
                    by_post.append({"post": label or None, "min": lo, "max": hi})
            if (_MIN.search(line) or _MAX.search(line)) and _NA.search(line):
                age["not_specified"] = True
        if len(line) > 160 or line.endswith("?"):
            continue
        low = line.lower()
        if "as on" in low and "age" in low and "as_on" not in age:
            v = find_date(line) or (find_date(lines[i + 1]) if i + 1 < len(lines) else None)
            if v:
                age["as_on"] = v
        m = _MIN.search(line)
        if m and "min" not in age:
            v = _age_after(lines, i, m.end())
            if v:
                age["min"] = v
        m = _MAX.search(line)
        if m and "max" not in age:
            v = _age_after(lines, i, m.end())
            if v:
                age["max"] = v
        m = _RANGE.search(line)
        if m:
            age.setdefault("min", int(m.group(1)))
            age.setdefault("max", int(m.group(2)))
    if by_post:
        age["by_post"] = by_post
        age.setdefault("min", min(b["min"] for b in by_post))
        age.setdefault("max", max(b["max"] for b in by_post))
    if "max" in age:
        age.pop("not_specified", None)
    return age


# --------------------------------------------------------------------------- fees
_AMT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+)|(?<![\d/])([\d,]+)\s*/-", re.I)
_NIL = re.compile(r"\b(nil|no fee|exempt\w*|free)\b", re.I)
_GEN = re.compile(r"\b(general|gen|ur|obc|ews|unreserved)\b", re.I)
_RES = re.compile(r"\b(sc|st|ph|pwd|pwbd)\b", re.I)


def _fees(lines):
    fees = {}
    for line in lines:
        if len(line) > 150 or line.endswith("?"):
            continue
        m = _AMT.search(line)
        if m:
            amt = int((m.group(1) or m.group(2)).replace(",", "") or 0)
        elif _NIL.search(line) and (_GEN.search(line) or _RES.search(line)):
            amt = 0
        else:
            continue
        low = line.lower()
        if _GEN.search(line):
            fees.setdefault("general_obc_ews", amt)
        elif _RES.search(line):
            fees.setdefault("sc_st_pwd", amt)
        elif "female" in low or "women" in low:
            fees.setdefault("female", amt)
    return fees


# --------------------------------------------------------------------------- posts
_TOTAL = [
    re.compile(r"total\s*(?:no\.?\s*of\s*)?(?:post|posts|vacanc(?:y|ies))\s*[:\-]?\s*([\d,]{1,7})", re.I),
    re.compile(r"\btotal\s*:?\s*([\d,]{1,7})\s*(?:post|posts|vacanc(?:y|ies))\b", re.I),
]
_TITLE_POSTS = re.compile(r"\b([\d,]{1,7})\s*(?:post|posts|vacanc(?:y|ies))\b", re.I)


def _total_posts(lines, title):
    for line in lines:
        for rx in _TOTAL:
            m = rx.search(line)
            if m:
                n = int(m.group(1).replace(",", "") or 0)
                if n > 0:
                    return n
    m = _TITLE_POSTS.search(title or "")
    if m:
        n = int(m.group(1).replace(",", "") or 0)
        return n or None
    return None


# --------------------------------------------------------------------------- qualification
_QUAL = re.compile(
    r"\b(passed|pass|degree|graduat\w*|bachelor\w*|master\w*|post\s*graduat\w*|diploma|iti|10th|12th|"
    r"matric\w*|intermediate|high\s*school|class\s*(?:10|12|x|xii)\b|b\.?\s?tech|b\.?\s?e\.|b\.?\s?sc|"
    r"m\.?\s?sc|mba|b\.?\s?ed|llb|ph\.?\s?d|nursing|mbbs|ctet|tet)\b", re.I)
_QUAL_SKIP = re.compile(r"(how to|click|download|our app|join|follow|telegram|whatsapp)", re.I)


def _qualification(lines):
    seen, out = set(), []
    for line in lines:
        if not (4 <= len(line) <= 400) or line.endswith("?") or _QUAL_SKIP.search(line):
            continue
        if _QUAL.search(line) and line not in seen:
            seen.add(line)
            out.append(line)
            if len(out) >= 15:
                break
    return out


# --------------------------------------------------------------------------- links
_SKIP_HOSTS = ("youtube.", "youtu.be", "t.me", "telegram", "whatsapp", "play.google", "apps.apple",
               "instagram", "facebook", "twitter.", "x.com")
_HEADS = ["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]


def _link_kinds(ctx: str) -> list[str]:
    """A link can be two things at once, e.g. 'Download Application Form & Notification'."""
    kinds = []
    if any(w in ctx for w in ("apply online", "online apply", "apply now", "registration", "login",
                              "online form")):
        kinds.append("apply")
    elif any(w in ctx for w in ("apply offline", "offline form", "application form")):
        kinds.append("apply_offline")
    if any(w in ctx for w in ("notification", "advertisement", "advt", "information brochure", "brochure",
                              "download vacancy", "short notice", "download notice")):
        kinds.append("notification")
    if not kinds and any(w in ctx for w in ("official website", "official site", "offical website")):
        kinds.append("official_site")
    return kinds


def _own_site(host: str, href: str) -> bool:
    """Links back to the aggregator itself (homepage, other posts, its tools site)."""
    if href.lower().split("?")[0].endswith(".pdf"):
        return False
    return host in ("sarkariresult.com", "www.sarkariresult.com") or "sarkariresultportal" in host


def _links(html, base_url=None):
    soup = BeautifulSoup(html or "", "lxml")
    links = {}
    anchors = soup.find_all("a", href=True)
    # Links in the "Important Links" table win over links mentioned earlier in the text.
    marker = soup.find(string=re.compile(r"important\s+links", re.I))
    if marker is not None:
        after = marker.find_all_next("a", href=True)
        seen = set(map(id, after))
        anchors = after + [a for a in anchors if id(a) not in seen]
    for a in anchors:
        href = a["href"].strip()
        if base_url and href.startswith("/") and not href.startswith("//"):
            href = urljoin(base_url, href)
        if not href.lower().startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if any(s in host for s in _SKIP_HOSTS) or _own_site(host, href):
            continue
        own = _clean(a.get_text(" "))
        tr = a.find_parent("tr")
        near = _clean(tr.get_text(" ")) if tr else _clean(a.parent.get_text(" "))[:200]
        kinds = _link_kinds(f"{own} {near}".lower())
        if not kinds:
            ancestors = set(id(p) for p in a.parents)
            for h in a.find_all_previous(_HEADS, limit=6):
                if id(h) in ancestors:
                    continue
                kinds = _link_kinds(_clean(h.get_text(" ")).lower())
                break
        for kind in kinds:
            if kind not in links:
                links[kind] = href
                if kind == "apply" and "activ" in own.lower():
                    opens = find_date(own)
                    if opens:
                        links["apply_opens"] = opens
    return links


# --------------------------------------------------------------------------- page body
_SECTION_WORDS = ("important dates", "important links", "application fee", "age limit")
_CONTENT_CLASSES = ("entry-content", "post-content", "single-content", "the-content", "td-post-content",
                    "article-content", "post-body")


def main_content(page_html: str) -> str:
    """Cut a full web page down to the post body (drops menus, sidebars, footer)."""
    soup = BeautifulSoup(page_html or "", "lxml")
    for t in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form", "iframe", "svg"]):
        t.decompose()
    anchor = None
    for h in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "td", "th"]):
        if any(w in h.get_text(" ").lower() for w in _SECTION_WORDS):
            anchor = h
            break
    if anchor is not None:
        for p in anchor.parents:
            classes = " ".join(p.get("class") or []) if hasattr(p, "get") else ""
            if p.name in ("article", "main") or any(c in classes for c in _CONTENT_CLASSES):
                return str(p)
    for sel in ("article", "main", ".entry-content", ".post-content"):
        el = soup.select_one(sel)
        if el is not None:
            return str(el)
    return str(soup.body or soup)


# --------------------------------------------------------------------------- public API
def extract(html: str, title: str = "", base_url: str | None = None) -> dict:
    lines = html_to_lines(html)
    dates, date_notes = _dates(lines)
    out = {
        "dates": dates,
        "date_notes": date_notes,
        "age": _age(lines),
        "fees": _fees(lines),
        "total_posts": _total_posts(lines, title),
        "qualification_text": _qualification(lines),
        "links": _links(html, base_url),
    }
    # A field counts as found if the page states it, even as text ("District Wise", "NA").
    checks = {
        "last_date": dates.get("last_date") or date_notes.get("last_date"),
        "application_start": dates.get("application_start") or date_notes.get("application_start"),
        "age_max": out["age"].get("max") or out["age"].get("not_specified"),
        "notification_link": out["links"].get("notification"),
        "qualification_or_posts": out["qualification_text"] or out["total_posts"],
    }
    out["missing"] = [k for k, v in checks.items() if not v]
    out["confidence"] = round(1 - len(out["missing"]) / len(checks), 2)
    return out


_KINDS = [
    ("answer_key", ("answer key", "answerkey")),
    ("admit_card", ("admit card", "call letter", "hall ticket", "interview letter", "exam city")),
    ("result", ("result", "score card", "scorecard", "merit list", "cut off", "cutoff")),
    ("syllabus", ("syllabus", "exam pattern")),
    ("admission", ("admission", "counselling", "counseling")),
    ("certificate", ("certificate",)),
    ("job", ("online form", "apply online", "offline form", "recruitment", "vacancy", "bharti",
             "latest job", "jobs", "notification")),
]


def classify(title: str, categories=()) -> str:
    """job | admit_card | result | answer_key | syllabus | admission | certificate | other"""
    for text in ((title or "").lower(), " ".join(categories or ()).lower()):
        for kind, words in _KINDS:
            if any(w in text for w in words):
                return kind
    return "other"
