/*
 * ExamMatch eligibility engine, JavaScript version.
 *
 * A line-by-line port of eligibility.py so matching can run in the browser (and later inside
 * the mobile app). tests/golden checks that both versions give identical results on real jobs.
 * When you change a rule, change it in BOTH files and re-run the golden test.
 */
(function (root) {
  "use strict";

  // ======================================================================= vocabulary
  const LEVELS = { "8th": 0, "10th": 1, "12th": 2, diploma: 2.5, graduate: 3, postgraduate: 4, phd: 5 };

  const LEVEL_RX = [
    ["phd", /\bph\.?\s?d\b|doctorate/i],
    ["postgraduate", /post\s*-?\s*graduat\w*|\bmaster\w*|\bm\.\s?a\b|\bm\.?\s?sc\b|\bm\.?\s?com\b|\bmba\b|\bm\.?\s?tech\b|\bm\.?\s?e\.|\bmca\b|\bpg\b(?!\s*diploma)|\bm\.?\s?ed\b|\bllm\b|\bmd\b|\bms\b(?=\s)/i],
    ["graduate", /graduat\w*|bachelor\w*|\bdegree\b|\bb\.\s?a\b|\bb\.?\s?sc\b|\bb\.?\s?com\b|\bb\.?\s?tech\b|\bb\.?\s?e\b\.?|\bbca\b|\bbba\b|\bllb\b|\bmbbs\b|\bbams\b|\bbds\b|\bb\.?\s?pharm|\bshastri\b/i],
    ["diploma", /\bdiploma\b|polytechnic/i],
    ["12th", /10\s*\+\s*2|\b12th\b|intermediate|class\s*(?:12|xii)\b|higher\s*secondary|\bsenior\s*secondary/i],
    ["10th", /\b10th\b|high\s*school|matric\w*|class\s*(?:10|x)\b|secondary\s*school|\bssc\s*\//i],
    ["8th", /\b8th\b|class\s*(?:8|viii)\b|junior\s*high\s*school\s*(?:pass|exam)/i],
  ];

  const TAGS = {
    iti: /\biti\b|\bncvt\b|\bscvt\b/i,
    bed: /\bb\.?\s?ed\b/i,
    deled: /\bd\.?\s?el\.?\s?ed\b|\bbtc\b|\bjbt\b|\bd\.?\s?ed\b/i,
    tet: /\bctet\b|\b[a-z]{0,4}tet\b|teacher\s*eligibility/i,
    engineering: /\bb\.?\s?tech\b|\bb\.?\s?e\b\.?|engineering\s*degree|degree\s*in\s*\w*\s*engineering|\bbe\s*\//i,
    nursing: /nursing|\bgnm\b|\banm\b/i,
    medical: /\bmbbs\b|\bbams\b|\bbhms\b|\bbums\b|\bbds\b|dental\s*surgery|medicine\s*and\s*surgery|\bmedical\s*degree|ayurved|homo?eopath|unani|\bayush\b/i,
    pharmacy: /pharm/i,
    law: /\bllb\b|law\s*graduate|degree\s*in\s*law/i,
    ca: /\bca\b|chartered\s*accountant|\bicwa\b|\bcma\b/i,
    ccc: /\bccc\b|o\s*level|course\s*on\s*computer\s*concepts/i,
    nursery_teacher: /\bntt\b|\bdpse\b|nursery\s*teacher|pre\s*-?\s*school\s*education/i,
    special_education: /special\s*education|\brci\b/i,
    physiotherapy: /physiotherap/i,
    typing: /typing|stenograph/i,
    driving_licence: /driving\s*licen[cs]e/i,
    pet: /\bpet\b|preliminary\s*eligibility\s*test/i,
    net: /\bnet\b|\bslet\b|\bset\b(?=\s*\/|\s*exam)|\bjrf\b/i,
  };
  const TAG_NAMES = {
    iti: "ITI certificate", bed: "B.Ed", deled: "D.El.Ed / BTC", tet: "TET / CTET",
    engineering: "B.Tech / B.E", nursing: "Nursing (GNM/ANM/B.Sc)", medical: "MBBS / AYUSH degree",
    pharmacy: "Pharmacy (D/B.Pharm)", law: "LLB", ca: "CA / CMA", ccc: "CCC / O Level",
    nursery_teacher: "NTT / DPSE", special_education: "Special Education (RCI)",
    physiotherapy: "Physiotherapy", typing: "Typing / Steno", driving_licence: "Driving licence",
    pet: "UPSSSC PET scorecard", net: "NET / SET",
  };
  const HARD_TAGS = new Set(["iti", "nursing", "medical", "engineering", "pharmacy", "law", "bed", "deled", "physiotherapy", "ca"]);
  const TEACHER_TRAINING = new Set(["bed", "deled"]);
  const ALT_TAGS = new Set(["iti", "nursing", "nursery_teacher", "special_education", "physiotherapy", "pharmacy"]);

  const TITLE_RULES = [
    [/medical\s*officer|senior\s*resident|\bmbbs\b/i, "tag", "medical", null],
    [/staff\s*nurse|nursing\s*officer/i, "tag", "nursing", null],
    [/\bpharmacist\b/i, "tag", "pharmacy", null],
    [/junior\s*engineer|assistant\s*engineer|engineer\s*trainee|\bje\b(?!e)|\bae\b/i, "engineer", null, null],
    [/assistant\s*professor|\blecturer\b|\bpgt\b/i, "level", "postgraduate", "teaching post"],
    [/\btechnician\b/i, "maybe", null, "Technician posts usually need ITI or a diploma"],
    [/\bdriver\b/i, "maybe", null, "Driver posts need a valid driving licence"],
    [/upsssc(?!.*\bpet\b\s*(?:20\d\d)?\s*(?:online|admit|result|answer))/i, "pet", null, null],
  ];

  const SUBJECTS = ["hindi", "english", "sanskrit", "urdu", "mathematics", "physics", "chemistry", "zoology", "botany",
    "biology", "home science", "computer", "commerce", "economics", "history", "geography",
    "political science", "sociology", "agriculture", "civil", "mechanical", "electrical", "electronics",
    "statistics", "psychology", "fine art", "music", "physical education", "library", "veterinary",
    "nutrition", "social work", "information technology", "aeronautical"];
  const SUBJ_ALIASES = { maths: "mathematics", math: "mathematics", it: "information technology",
    cs: "computer", "computer science": "computer", pcm: "mathematics", eee: "electrical",
    ece: "electronics", cse: "computer", me: "mechanical", ce: "civil", "home sc": "home science" };
  const ANY = /\bany\s*(?:stream|discipline|subject|degree|graduat\w*|branch|faculty|field)|all\s*stream/i;
  const PCT = /(\d{2})(?:\.\d+)?\s*%/i;
  const EXP = /(\d{1,2})\s*(?:\+\s*)?years?['’]?\s*(?:of\s*)?(?:\w+\s*){0,3}?experience/i;
  const APPEARING = /appearing|final\s*year|awaiting|result\s*awaited/i;
  const OR = /\s+\bor\b\s+|\s*\/\s*(?=(?:\d+\s*years?\s*)?(?:diploma|graduat|bachelor|master|post|b\.|m\.|10|12|iti)\b)/i;

  // ======================================================================= age relaxation
  const CENTRAL_RELAX = { GEN: 0, EWS: 0, OBC: 3, SC: 5, ST: 5 };
  const CENTRAL_PWD = 10;
  const STATE_RELAX = { UP: [{ GEN: 0, EWS: 0, OBC: 5, SC: 5, ST: 5 }, 15] };

  const STATES = [
    ["UP", /\bup\b|upsssc|uppsc|upessc|uppbpb|upprpb|uttar\s*pradesh|sewayojan|\bkgbv\b|ecce\s*educator/i],
    ["BR", /bihar|\bbpsc\b|\bbssc\b|\bcsbc\b|\bbtsc\b/i],
    ["RJ", /rajasthan|\brpsc\b|rsmssb|\brssb\b/i],
    ["MP", /\bmp\b|mppsc|mpesb|madhya\s*pradesh/i],
    ["HP", /hppsc|hpssc|himachal|\bhp\b/i],
    ["UK", /uttarakhand|ukpsc|uksssc/i],
    ["HR", /haryana|\bhpsc\b|\bhssc\b/i],
    ["PB", /punjab|\bppsc\b|psssb/i],
    ["DL", /delhi|dsssb/i],
    ["JH", /jharkhand|\bjssc\b|\bjpsc\b/i],
    ["CG", /chhattisgarh|cgpsc|cg\s*vyapam/i],
    ["GJ", /gujarat|\bgpsc\b|gsssb/i],
    ["MH", /maharashtra|\bmpsc\b/i],
    ["WB", /west\s*bengal|wbpsc|wbssc/i],
    ["OD", /odisha|\bopsc\b|\bossc\b/i],
    ["TN", /tamil\s*nadu|tnpsc/i],
    ["KA", /karnataka|\bkpsc\b/i],
    ["AP", /andhra|appsc/i],
    ["TS", /telangana|tspsc|tgpsc/i],
    ["AS", /\bassam\b|\bapsc\b/i],
    ["JK", /jammu|jkssb|jkpsc/i],
  ];
  const CENTRAL_HINT = /\bupsc\b|\bssc\b|railway|\brrb\b|\brrc\b|\bibps\b|\bsbi\b|\bnta\b|army|navy|air\s*force|rifles|\bindian\b|national|central|\bnpcil\b|\bongc\b|\bpgcil\b|power\s*grid|\brites\b|\bbhu\b|supreme\s*court|\bafms\b|\bicar\b|\basrb\b|\bnielit\b|\bbsf\b|\bcrpf\b|\bcisf\b|\bitbp\b|\bssb\b|\bdrdo\b|\bisro\b|\bbhel\b|\bntpc\b|\baiims\b|\besic\b|\bkvs\b|\bnvs\b|\bindia\b/i;
  const NO_RELAX = /\bnta\b|aissee|sainik|admission|entrance|\bjee\b|\bneet\b|\bcuet\b|cadet\s*college|\bnda\b|\bcds\b|afcat|agniveer|\bnavy\b|air\s*force|territorial\s*army/i;
  const FEMALE_ONLY = /anganwadi|aanganwadi|\bmahila\b|\bkgbv\b|kasturba|female\s*only|only\s*female|women\s*only|only\s*women|female\s*candidates\s*only|\(female\)/i;

  const SPECIFIC = /(?:bachelor|master)\s*(?:'?s)?\s*of\s*([a-z]+(?:\s+[a-z]+)?)/i;
  const GENERIC_DEGREES = new Set(["arts", "science", "commerce", "any", "degree", "recognized", "recognised"]);
  const DEGREE_IN = /(?:degree|graduat\w*|diploma|bachelor\w*|master\w*|b\.?\s?sc|m\.?\s?sc|b\.?\s?a|m\.?\s?a)\.?\s*(?:\(\w+\)\s*)?in\s+([a-z]+)/i;
  const RELATED = new Set(["related", "relevant", "concerned", "respective", "appropriate"]);
  const FILLER = new Set(["any", "the", "a", "an", "all", "first", "second", "full", "regular"]);
  const DEGREE_TAG = { technology: "engineering", engineering: "engineering", education: "bed", laws: "law",
    law: "law", pharmacy: "pharmacy", dental: "medical", medicine: "medical",
    physiotherapy: "physiotherapy", nursing: "nursing" };

  const JUNK = /^step\s*\d|candidates?\s*will\s*have\s*to|how\s*to|read\s*the\s*notification|\bexam\b\s*$|selection\s*process|pay\s*scale|syllabus|recruitment\s*20\d\d|online\s*form|apply\s*online|notification|\bage\b\s*(?:limit|:)|maximum\s*age|minimum\s*age|\bfee\b|\d+\s*\/-|vacancy|post\s*date|exam\s*date|admit\s*card|salary|pay\s*level|age\s*relaxation|^(?:graduate|diploma|trade)(?:\s*[,&]\s*(?:graduate|diploma|trade))+\s*apprentice/i;

  // ======================================================================= Python-compatible helpers
  const escapeRx = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pyStr = v => (v === undefined ? "" : v === null ? "None" : typeof v === "boolean" ? (v ? "True" : "False") : String(v));
  const pyNone = v => (v === undefined || v === null ? "None" : String(v));
  const fmtG = x => String(Number(Number(x).toPrecision(6)));         // Python f"{x:g}" for normal values
  const title = s => s.replace(/\w\S*/g, w => w[0].toUpperCase() + w.slice(1).toLowerCase());
  const strip = (s, chars) => { let a = 0, b = s.length; while (a < b && chars.includes(s[a])) a++; while (b > a && chars.includes(s[b - 1])) b--; return s.slice(a, b); };
  const sorted = set => [...set].sort();
  const setEq = (a, b) => a.size === b.size && [...a].every(x => b.has(x));
  const inter = (a, b) => new Set([...a].filter(x => b.has(x)));
  const minus = (a, b) => new Set([...a].filter(x => !b.has(x)));

  function parseISO(s) { const [y, m, d] = s.split("-").map(Number); return { y, m, d }; }
  function ageOn(dob, on) {
    const a = parseISO(dob), b = parseISO(on);
    const months = (b.y - a.y) * 12 + (b.m - a.m) - (b.d < a.d ? 1 : 0);
    return [Math.floor(months / 12), ((months % 12) + 12) % 12];
  }
  function todayISO() {
    const t = new Date();
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
  }

  // ======================================================================= requirements
  function subjectsIn(text) {
    const low = text.toLowerCase();
    const found = new Set(SUBJECTS.filter(s => new RegExp(`\\b${escapeRx(s)}\\b`).test(low)));
    for (const [alias, s] of Object.entries(SUBJ_ALIASES)) {
      if (alias.length > 3 && new RegExp(`\\b${escapeRx(alias)}\\b`).test(low)) found.add(s);
    }
    return found;
  }
  function levelOf(text) { for (const [lvl, rx] of LEVEL_RX) if (rx.test(text)) return lvl; return null; }
  function tagsOf(text) { return new Set(Object.keys(TAGS).filter(t => TAGS[t].test(text))); }

  function parseOption(text) {
    const o = { text, level: levelOf(text), tags: tagsOf(text), subjects: new Set(), min_percent: null,
      appearing_ok: false, any_stream: false, specific_degree: null, unknown_field: null };
    if ((o.tags.has("engineering") || o.tags.has("law") || o.tags.has("medical")) && (o.level === null || LEVELS[o.level] < 3)) o.level = "graduate";
    if (setEq(o.tags, new Set(["engineering"])) && o.level === "diploma") o.tags = new Set();
    if (o.tags.has("iti") && o.level === null) o.level = "10th";
    o.any_stream = ANY.test(text);
    if (!o.any_stream) o.subjects = subjectsIn(text);
    let m = PCT.exec(text);
    if (m && +m[1] >= 33 && +m[1] <= 90) o.min_percent = +m[1];
    o.appearing_ok = APPEARING.test(text);
    m = SPECIFIC.exec(text);
    if (m) {
      const words = m[1].toLowerCase().split(/\s+/).filter(Boolean);
      if (words.length && !GENERIC_DEGREES.has(words[0])) {
        const tag = DEGREE_TAG[words[0]];
        if (tag) o.tags.add(tag); else o.specific_degree = words[0];
      }
    }
    m = DEGREE_IN.exec(text);
    if (m && !o.any_stream && !o.subjects.size && !o.tags.size && !o.specific_degree) {
      const word = m[1].toLowerCase();
      if (RELATED.has(word)) o.unknown_field = "the related field / trade";
      else if (!FILLER.has(word)) o.unknown_field = word;
    }
    return o;
  }

  function parseRequirements(job) {
    const ttl = job.title || "";
    const lines = (job.qualification_text || []).filter(q => !JUNK.test(q));
    const options = []; let common = new Set();
    for (const line of lines) {
      const pieces = line.split(OR).map(p => (p ? strip(p, " .,;:()") : "")).filter(Boolean);
      const lineOpts = [];
      for (const piece of pieces) {
        const o = parseOption(piece);
        if (o.level === null && !o.tags.size) {
          if (lineOpts.length) subjectsIn(piece).forEach(s => lineOpts[lineOpts.length - 1].subjects.add(s));
          continue;
        }
        lineOpts.push(o);
      }
      if (lineOpts.length && lineOpts.every(o => o.level === null)) {
        for (const o of lineOpts) {
          if (inter(o.tags, ALT_TAGS).size) {
            o.level = setEq(o.tags, new Set(["iti"])) ? "10th" : "12th";
            options.push(o);
          } else o.tags.forEach(t => common.add(t));
        }
        continue;
      }
      lineOpts.filter(o => o.level !== null).forEach(o => options.push(o));
    }
    const full = lines.join(" ");
    const exp = EXP.exec(full);
    const textAll = `${ttl} ${job.link || ""} ${(job.categories || []).join(" ")}`;
    let state = null;
    if (!CENTRAL_HINT.test(ttl)) for (const [code, rx] of STATES) if (rx.test(textAll)) { state = code; break; }
    const rules = TITLE_RULES.filter(([rx]) => rx.test(ttl)).map(([, k, v, why]) => [k, v, why]);
    const branch = ["civil", "mechanical", "electrical", "electronics", "computer"].find(b => new RegExp(`\\b${b}\\b`, "i").test(ttl)) || null;
    const tt = inter(common, TEACHER_TRAINING);
    const teacherTraining = tt.size > 1;
    if (teacherTraining) common = minus(common, TEACHER_TRAINING);
    return {
      options, common_tags: common, title_rules: rules, no_relaxation: NO_RELAX.test(ttl), branch,
      teacher_training: teacherTraining, experience_years: exp ? parseInt(exp[1], 10) : null, state,
      female_only: FEMALE_ONLY.test(`${ttl} ${full}`),
      district_level: /\bdistrict\b|\bjila\b/i.test(ttl) || ttl.toLowerCase().includes("ecce educator"),
      marks_relaxation: /relaxation\s*in\s*(?:the\s*)?minimum\s*marks|marks\s*relaxation/i.test(full),
    };
  }

  // ======================================================================= profile
  function loadProfile(d) {
    const edu = d.education || [];
    let level = -1; const percents = {}; let tags = new Set(); let subjects = new Set(); const degrees = [];
    for (const e of edu) {
      const lvl = e.level;
      if (!(lvl in LEVELS)) throw new Error(`education level must be one of ${Object.keys(LEVELS).join(", ")}, got ${lvl}`);
      level = Math.max(level, LEVELS[lvl]);
      if (e.percent !== undefined && e.percent !== null) percents[lvl] = Number(e.percent);
      let desc = ["degree", "stream", "branch", "name"].map(k => pyStr(e[k])).join(" ");
      desc += " " + (e.subjects || []).join(" ");
      degrees.push(desc.toLowerCase());
      tagsOf(desc).forEach(t => tags.add(t));
      subjectsIn(desc).forEach(s => subjects.add(s));
      (e.subjects || []).forEach(s => subjects.add(String(s).toLowerCase()));
      if (lvl === "diploma") tags.add("diploma");
      if (lvl === "phd" || lvl === "postgraduate") tags.delete("diploma");
    }
    for (const c of d.certificates || []) {
      const t = tagsOf(c);
      if (t.size) t.forEach(x => tags.add(x)); else tags.add(String(c).toLowerCase());
    }
    subjects = new Set([...subjects].map(s => SUBJ_ALIASES[s] || s));
    const pursuing = d.pursuing || {};
    const pLevel = pursuing.final_year ? (LEVELS[pursuing.level] ?? null) : null;
    if (Object.keys(pursuing).length) {
      const desc = ["degree", "stream", "branch"].map(k => pyStr(pursuing[k])).join(" ");
      subjectsIn(desc).forEach(s => subjects.add(s));
    }
    let cat = (d.category || "GEN").toUpperCase().replace(/GENERAL/g, "GEN").replace(/UR/g, "GEN");
    if (cat.startsWith("OBC")) cat = "OBC";
    if (!(cat in CENTRAL_RELAX)) throw new Error("category must be GEN, EWS, OBC, SC or ST");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d.dob || "")) throw new Error("date of birth must be YYYY-MM-DD");
    return {
      dob: d.dob, gender: (d.gender || "").toLowerCase(), category: cat, pwd: !!d.pwd, ex_serviceman: !!d.ex_serviceman,
      domicile: (d.domicile || "").toUpperCase() || null, level, pursuing_level: pLevel, tags, subjects, percents,
      experience_years: Number(d.experience_years || 0),
      tags_complete: d.certificates_complete === undefined ? true : !!d.certificates_complete,
      degrees_text: degrees.join(" "),
    };
  }

  // ======================================================================= check
  function relaxation(p, req) {
    if (req.no_relaxation) return [0, null];
    if (req.state) {
      if (p.domicile !== req.state) return [0, null];
      if (STATE_RELAX[req.state]) { const [table, pwd] = STATE_RELAX[req.state]; return [table[p.category] + (p.pwd ? pwd : 0), null]; }
      return [CENTRAL_RELAX[p.category] + (p.pwd ? CENTRAL_PWD : 0), `${req.state} relaxation rules may differ`];
    }
    return [CENTRAL_RELAX[p.category] + (p.pwd ? CENTRAL_PWD : 0), null];
  }

  function checkAge(p, job, req, today, fail, check, notes) {
    const age = job.age || {};
    let on = age.as_on || null;
    if (on === null) {
      const last = (job.dates || {}).last_date;
      on = last || today;
      if (age.min || age.max) notes.push(`Age date not stated; counted on ${on}`);
    }
    const [years, months] = ageOn(p.dob, on);
    const shown = `${years}y ${months}m on ${on}`;
    if (age.not_specified || !(age.min || age.max || (age.by_post && age.by_post.length))) {
      if (!age.not_specified) check.push("Age limit not found in the post; read the notification");
      return shown;
    }
    const ranges = (age.by_post && age.by_post.length) ? age.by_post : [{ post: null, min: age.min ?? null, max: age.max ?? null }];
    const [relax, caveat] = relaxation(p, req);
    let best = null;
    for (const r of ranges) {
      const lo = r.min ?? null, hi = r.max ?? null;
      if (lo !== null && years < lo) continue;
      if (hi === null || years <= hi) { best = [r, 0]; break; }
      if (relax && years <= hi + relax && best === null) best = [r, relax];
    }
    if (best === null) {
      const mins = ranges.filter(r => r.min !== undefined && r.min !== null).map(r => r.min);
      const maxs = ranges.filter(r => r.max !== undefined && r.max !== null).map(r => r.max);
      const loAll = mins.length ? Math.min(...mins) : null, hiAll = maxs.length ? Math.max(...maxs) : null;
      if (loAll !== null && years < loAll) { fail.push(`Too young: ${years} years on ${on}, minimum ${loAll}`); return shown; }
      const upto = age.max_upto;
      if (upto && years <= upto + relax) {
        check.push(`Age ${years}: above the ${pyNone(hiAll)}-year limit but within ${upto}${relax ? ` (+${relax})` : ""} allowed for some posts; check which posts`);
        return shown;
      }
      let msg = `Over age: ${years}y ${months}m on ${on}, maximum ${pyNone(hiAll)}`;
      if (relax) msg += ` (+${relax} relaxation = ${hiAll + relax})`;
      if (p.ex_serviceman) check.push(msg + "; ex-serviceman relaxation may apply");
      else if (req.state && p.domicile !== req.state && ["OBC", "SC", "ST"].includes(p.category)) fail.push(msg + `; category relaxation is only for ${req.state} domicile`);
      else fail.push(msg);
      return shown;
    }
    const [r, used] = best;
    if (r.post) notes.push(`Age fits: ${r.post} (${pyNone(r.min)}-${pyNone(r.max)})`);
    if (used) {
      if (caveat) check.push(`Eligible only with ${used}-year ${p.category}${p.pwd ? "/PwD" : ""} relaxation; ${caveat}`);
      else notes.push(`Uses ${used}-year ${p.category}${p.pwd ? "/PwD" : ""} age relaxation`);
    }
    return shown;
  }

  function optionResult(p, o, req) {
    const need = LEVELS[o.level], have = p.level;
    if (have < need) {
      if (o.appearing_ok && p.pursuing_level !== null && p.pursuing_level >= need) { /* final-year allowed */ }
      else if (p.pursuing_level !== null && p.pursuing_level >= need) return ["maybe", `needs ${o.level}; final-year students only if the notification allows`];
      else return ["no", `needs ${o.level}`];
    }
    if (o.level === "diploma" && !p.tags.has("diploma")) {
      if (!(have >= 3 && o.subjects.size && inter(o.subjects, p.subjects).size)) {
        return [p.tags_complete ? "no" : "maybe", "needs a diploma" + (o.subjects.size ? ` in ${sorted(o.subjects).join(", ")}` : "")];
      }
    }
    const missing = minus(minus(o.tags, p.tags), new Set(["diploma"]));
    if (missing.size) return [p.tags_complete ? "no" : "maybe", `needs ${sorted(missing).map(t => TAG_NAMES[t] || t).join(", ")}`];
    if (o.specific_degree && !p.degrees_text.includes(o.specific_degree)) return ["no", `needs Bachelor/Master of ${title(o.specific_degree)}`];
    if (o.unknown_field && !p.degrees_text.includes(o.unknown_field) && !p.subjects.has(o.unknown_field)) return ["maybe", `needs a qualification in ${o.unknown_field}`];
    if (o.subjects.size && !inter(o.subjects, p.subjects).size) return ["maybe", `needs subject: ${sorted(o.subjects).join(", ")}`];
    if (o.min_percent) {
      const got = o.level !== "diploma" ? p.percents[o.level] : p.percents.diploma;
      if (got === undefined) return ["maybe", `needs ${o.min_percent}% at ${o.level}; add your marks to check`];
      if (got < o.min_percent) {
        if ((req.marks_relaxation && ["SC", "ST", "OBC"].includes(p.category)) || p.pwd) {
          return ["maybe", `needs ${o.min_percent}% (you have ${fmtG(got)}%); reserved-category marks relaxation may apply`];
        }
        return ["no", `needs ${o.min_percent}% at ${o.level} (you have ${fmtG(got)}%)`];
      }
    }
    return ["ok", null];
  }

  function check(profile, job, today) {
    today = today || todayISO();
    const req = parseRequirements(job);
    const fail = [], chk = [], notes = [];
    const dates = job.dates || {}, links = job.links || {};
    const last = dates.last_date;
    if (last && last < today) return { status: "closed", reasons: [`Last date was ${last}`], notes: [], age: null, via: null };
    if (!last) {
      const txt = (job.date_notes || {}).last_date;
      chk.push(txt ? `Last date: ${txt}` : "Last date not found; read the notification");
    }
    if (links.apply_opens && links.apply_opens > today) notes.push(`Apply link opens on ${links.apply_opens}`);

    if (req.female_only && profile.gender === "male") fail.push("Only for female candidates");

    const ageTxt = checkAge(profile, job, req, today, fail, chk, notes);

    let via = null;
    const missingCommon = minus(req.common_tags, profile.tags);
    const hard = sorted(inter(missingCommon, HARD_TAGS));
    const soft = sorted(minus(missingCommon, HARD_TAGS));
    if (hard.length) (profile.tags_complete ? fail : chk).push(`Also needs ${hard.map(t => TAG_NAMES[t] || t).join(", ")}`);
    if (soft.length) chk.push("May also need " + soft.map(t => TAG_NAMES[t] || t).join(", "));
    if (req.teacher_training && !inter(profile.tags, TEACHER_TRAINING).size) (profile.tags_complete ? fail : chk).push("Needs teacher training (B.Ed or D.El.Ed / BTC)");
    const multi = /\band\b|&|various|multiple|\bmts\b|non\s*teaching/i.test(job.title || "");
    const strict = (profile.tags_complete && !multi) ? fail : chk;
    for (const [kind, value, why] of req.title_rules) {
      if (kind === "tag" && !profile.tags.has(value)) strict.push(`Post needs ${TAG_NAMES[value]}` + (multi ? " (for some posts)" : ""));
      else if (kind === "engineer" && !profile.tags.has("engineering") && !profile.tags.has("diploma")) strict.push("Engineer post: needs B.Tech / B.E or an engineering diploma" + (multi ? " (for some posts)" : ""));
      else if (kind === "engineer" && req.branch && !profile.subjects.has(req.branch)) strict.push(`Engineer post for ${title(req.branch)} branch`);
      else if (kind === "level" && profile.level < LEVELS[value]) (multi ? chk : fail).push(`Needs ${value} (${why})`);
      else if (kind === "pet" && !profile.tags.has("pet")) chk.push("UPSSSC posts need a valid UPSSSC PET scorecard (add 'UPSSSC PET' to certificates if you have it)");
      else if (kind === "maybe") {
        if (!(profile.tags.has("iti") || profile.tags.has("diploma")) || why.includes("licence")) {
          if (!(why.includes("licence") && profile.tags.has("driving_licence"))) chk.push(why);
        }
      }
    }
    if (!req.options.length) chk.push("Qualification not found in the post; read the notification");
    else {
      const results = req.options.map(o => [o, ...optionResult(profile, o, req)]);
      const ok = results.filter(r => r[1] === "ok").map(r => r[0]);
      const maybe = results.filter(r => r[1] === "maybe");
      if (ok.length) via = ok[0].text.slice(0, 120);
      else if (maybe.length) { chk.push(`Qualification: ${maybe[0][2]}`); via = maybe[0][0].text.slice(0, 120); }
      else {
        const levels = [...new Set(req.options.map(o => o.level))].sort((a, b) => LEVELS[a] - LEVELS[b]);
        let easiest = results[0];
        for (const r of results) if (LEVELS[r[0].level] < LEVELS[easiest[0].level]) easiest = r;
        const reason = results.length === 1 ? easiest[2] : `Qualification not met (lowest accepted: ${levels[0]}; ${easiest[2]})`;
        fail.push(reason.startsWith("Qual") ? reason : `Qualification: ${reason}`);
      }
    }

    if (req.experience_years) {
      if (profile.experience_years >= req.experience_years) notes.push(`Needs ${req.experience_years} years experience (you have ${fmtG(profile.experience_years)})`);
      else if (profile.experience_years === 0) chk.push(`Mentions ${req.experience_years} years experience; may apply to some posts only`);
      else fail.push(`Needs ${req.experience_years} years experience (you have ${fmtG(profile.experience_years)})`);
    }

    if (req.state && profile.domicile !== req.state) {
      chk.push(req.district_level ? `${req.state} district-level post: usually only for local residents`
        : `${req.state} state post: check whether other-state candidates can apply`);
    } else if (req.district_level) notes.push("District-level post: check the district residence rule");

    if (fail.length) return { status: "not_eligible", reasons: fail, notes, age: ageTxt, via };
    if (chk.length) return { status: "check_manually", reasons: chk, notes, age: ageTxt, via };
    return { status: "eligible", reasons: [], notes, age: ageTxt, via };
  }

  function match(profile, jobs, today) {
    const order = { eligible: 0, check_manually: 1, not_eligible: 2, closed: 3 };
    const out = jobs.map(j => {
      const r = check(profile, j, today), links = j.links || {}, dates = j.dates || {};
      return { status: r.status, title: j.title, link: j.link, last_date: dates.last_date ?? null,
        apply: links.apply || links.apply_offline || null, notification: links.notification ?? null,
        total_posts: j.total_posts ?? null, age: r.age, qualified_via: r.via, reasons: r.reasons, notes: r.notes };
    });
    out.sort((a, b) => (order[a.status] - order[b.status]) || ((a.last_date || "9999") < (b.last_date || "9999") ? -1 : (a.last_date || "9999") > (b.last_date || "9999") ? 1 : 0));
    return out;
  }

  const api = { LEVELS, loadProfile, parseRequirements, check, match, todayISO, ageOn };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ExamEngine = api;
})(typeof window !== "undefined" ? window : globalThis);
