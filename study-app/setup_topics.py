#!/usr/bin/env python3
"""Draft topics.json (subjects, topics, week-by-week plan, assessments, exams, breaks) from your own course files.

The setup guide runs this for you after the first IntoStudy sync. By hand:

    python3 setup_topics.py                          write topics.json from your course files
    python3 setup_topics.py --force                  redo topics.json from scratch
    python3 setup_topics.py --timetable shot.png     only read your weekly timetable from a screenshot

AI goes the same way as in the app: the Antigravity CLI (agy) if it is signed in, otherwise the Gemini API key.
Check the result on the Programme page. Topics are split into subtopics later, when you open them in Learn.
"""
import base64
import fnmatch
import json
import mimetypes
import re
import sys
import os
from datetime import date, timedelta
from pathlib import Path

import paths
import server                      # its gemini(): agy first, the API key as a fallback

server.AGY_TIMEOUT = 1800          # the whole course in one request can take a while

TEXT, OUT = paths.TEXT, paths.TOPICS
PLAN_WORDS = re.compile(r"plan|overview|scheme|schedule|syllabus|programme|program|outline|handbook|guide|calendar|"
                        r"key info|assessment|exam|timetable|calendar|brief|specification", re.I)
PLAN_CHARS, PLAN_TOTAL = 40_000, 400_000

S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
arr = lambda x: {"type": "array", "items": x}
obj = lambda req, **p: {"type": "object", "properties": p, "required": req}
CLASS = obj(["day", "start", "end", "subject"], day=I, start=S, end=S, subject=S, code=S, room=S, teacher=S)
TIMETABLE_SCHEMA = obj(["timetable"], timetable=arr(CLASS))
KIND = {"type": "string", "enum": ["maths", "science", "essay", "english"]}
SCHEMA = obj(["term", "breaks", "exams", "subjects"],
             term=obj(["name", "week1"], name=S, week1=S),
             breaks=arr(obj(["name", "start", "end"], name=S, start=S, end=S)),
             exams=arr(obj(["subject", "name", "date"], subject=S, name=S, date=S)),
             subjects=arr(obj(["name", "short", "kind", "plan_source", "shared", "topics", "plan"],
                              name=S, short=S, kind=KIND, plan_source=S, plan_file=S, shared=arr(S),
                              topics=arr(obj(["id", "code", "name", "sources"], id=S, code=S, name=S, sources=arr(S))),
                              plan=arr(obj(["week", "topics", "focus", "guess"], week=I, topics=arr(S), focus=S,
                                           guess=B, assessment=S)))))

RULES = """You set up a study tracker for an International Foundation Year student at INTO Manchester (NCUK). From the
list of their course files (and the text of plans, overviews and assessment briefs among them) produce their course
structure as JSON:

- subjects: one per top-level folder of the file list, "name" EXACTLY the folder name. Skip folders that are not a
  taught subject (welcome, student services, wellbeing, library, careers...).
  short: a short label for small spaces (e.g. "Maths" for "Technical Maths", "Business" for "Business Studies").
  kind: "maths" (maths, statistics), "science" (physics, chemistry, biology, computing, engineering), "english"
  (English for Academic Purposes and other English language/skills subjects) or "essay" (subjects assessed through
  writing: business, sociology, global studies, economics, law, politics, history, psychology...).
- topics: the subject's real taught topics in teaching order, 6-15 per subject. id = short lowercase slug with a
  subject prefix ("p-momentum", "b-marketing", "s-family"), unique across all subjects. code = the module/unit code
  used in the files if any (e.g. "AC1", "Unit 3"), else a short label. name = a clear topic name a student would
  recognise. For an English skills subject, a topic is a skill block or the week's theme (e.g. "Paraphrasing and
  citing", "Seminar skills"), in the order taught.
- sources: path prefixes from the file list that hold that topic's files, e.g. "Physics/05 AC1" (a folder) or a
  full file path. Every prefix MUST be the start of at least one listed path. A "*" works as a wildcard
  ("EAP/04 Teacher/*Week 3/") when folder numbers vary. Cover as many files as you can; each file belongs to the
  topic it teaches.
- shared: prefixes of files useful for every topic of the subject (formula sheet, data booklet, past papers,
  module handbook, essay-writing guide, marking criteria).
- plan: teaching weeks 1..N of this semester with the topic ids taught each week and a one-line focus. Take it from
  the plans/overviews/week folders when they exist (guess=false); where nothing says it, estimate from the topic
  order (guess=true). Put any assessment that happens in that week in "assessment" (coursework deadline, essay,
  presentation, test, quiz, formative assessment), with its name as the files give it, e.g. "Formative
  assessment 2: reading comprehension" or "Report deadline (30%)". plan_source says where the plan came from in a
  few words; plan_file is the path of the main plan file if there is one.
- term: name (e.g. "Semester 1") and week1 = the Monday of teaching week 1 (YYYY-MM-DD), from the files; if not
  stated, the most likely Monday given today's date.
- breaks (holidays, reading weeks) and exams (end-of-semester exams with dates) only if the files state them.
Use only what the files show; never invent teachers, codes or dates that are not in them."""


def ask(parts, schema):
    contents = [{"role": "user", "parts": parts}]
    try:
        return server.gemini(contents, RULES, schema, 0.2)
    except server.GeminiError as e:
        sys.exit(f"The AI didn't answer: {e}")


def matches(path, prefix):
    return fnmatch.fnmatchcase(path, prefix + "*") if "*" in prefix else path.startswith(prefix)


def image_part(path):
    p = Path(path)
    return {"inlineData": {"mimeType": mimetypes.guess_type(p.name)[0] or "image/png",
                           "data": base64.b64encode(p.read_bytes()).decode()}}


def read_timetable(path, subjects):
    print("Reading the timetable screenshot…", flush=True)
    r = ask([{"text": "This is the student's weekly class timetable. List every weekly class: "
              f"day 1=Monday..5=Friday, start/end HH:MM, subject = one of {subjects} (the closest match), "
              "plus course code, room and teacher if shown."}, image_part(path)], TIMETABLE_SCHEMA)
    return [c for c in r["timetable"] if c["subject"] in subjects]


def write(cfg):
    """Atomically, keeping the previous programme in data/topics.before-update.json."""
    if OUT.exists():
        paths.DATA.mkdir(parents=True, exist_ok=True)
        (paths.DATA / "topics.before-update.json").write_text(OUT.read_text())
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    os.replace(tmp, OUT)


def valid_day(v):
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return None


def main():
    args = sys.argv[1:]
    shot = args[args.index("--timetable") + 1] if "--timetable" in args else None
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old.get("subjects") and "--force" not in args:
        if not shot:
            sys.exit(f"{OUT.name} already exists. Use --force to redo it, or --timetable <screenshot> to add classes.")
        old["timetable"] = read_timetable(shot, list(old["subjects"]))
        write(old)
        return print(f"Timetable updated: {len(old['timetable'])} weekly classes.")

    files = sorted(str(p.relative_to(TEXT)).removesuffix(".txt") for p in TEXT.rglob("*.txt")) if TEXT.exists() else []
    if not files:
        sys.exit("No course files yet. Connect IntoStudy in the setup guide and let the first sync finish.")
    plans, total = [], 0
    for f in files:
        if PLAN_WORDS.search(Path(f).name) and total < PLAN_TOTAL:
            t = (TEXT / (f + ".txt")).read_text(errors="ignore")[:PLAN_CHARS]
            plans.append(f"=== {f} ===\n{t}")
            total += len(t)
    print(f"{len(files)} course files, {len(plans)} plan/overview files. Asking the AI (takes a few minutes)…", flush=True)
    prompt = (f"Today is {date.today().isoformat()}.\n\nCOURSE FILES ({len(files)}):\n" + "\n".join(files) +
              "\n\nPLANS, OVERVIEWS AND ASSESSMENT BRIEFS:\n" + ("\n\n".join(plans) or "(none found)"))
    r = ask([{"text": prompt}], SCHEMA)

    subjects, dropped = {}, []
    folders = {f.split("/")[0] for f in files}
    for s in r["subjects"]:
        if s["name"] not in folders:
            dropped.append(f"subject {s['name']!r} (no such folder)")
            continue
        for t in s["topics"]:
            ok = [p for p in t["sources"] if any(matches(f, p) for f in files)]
            dropped += [f"{t['id']}: {p}" for p in t["sources"] if p not in ok]
            t["sources"] = ok
        s["shared"] = [p for p in s["shared"] if any(matches(f, p) for f in files)]
        ids = {t["id"] for t in s["topics"]}
        s["plan"] = [w for w in s["plan"] if isinstance(w.get("week"), int) and 1 <= w["week"] <= 59]   # no "week 0"
        for w in s["plan"]:
            w["topics"] = [i for i in w["topics"] if i in ids]
            if not w.get("guess"):
                w.pop("guess", None)
            if not str(w.get("assessment") or "").strip():
                w.pop("assessment", None)
        entry = {"short": s.get("short") or s["name"], "kind": s.get("kind") or "essay", "shared": s["shared"],
                 "plan_source": s["plan_source"], "topics": s["topics"], "plan": s["plan"]}
        if s.get("plan_file") and s["plan_file"] in files:
            entry["plan_file"] = s["plan_file"]
        if entry["kind"] == "english":
            entry["english"] = True                # English skills subject (EAP): no figures or calculations
        subjects[s["name"]] = entry
    if not subjects:                               # keep the programme we have rather than write an empty one
        sys.exit("The AI's answer had none of your subject folders. " + "; ".join(dropped[:5]))

    monday = date.today() - timedelta(days=date.today().weekday())
    week1 = valid_day(r.get("term", {}).get("week1"))
    week1 = date.fromisoformat(week1) if week1 else monday
    r["term"] = {"name": str(r.get("term", {}).get("name") or "Semester 1"),
                 "week1": (week1 - timedelta(days=week1.weekday())).isoformat()}   # weeks start on Monday
    r["breaks"] = [{**b, "start": valid_day(b.get("start")), "end": valid_day(b.get("end"))} for b in r.get("breaks", [])
                   if valid_day(b.get("start")) and valid_day(b.get("end")) and valid_day(b["start"]) <= valid_day(b["end"])]
    cfg = {"_note": "Drafted by setup_topics.py from the course files. 'sources' are path prefixes inside text/ "
                    "('*' = wildcard); 'shared' files go with every topic of the subject; 'plan' is the weekly plan; "
                    "'kind' (maths, science, essay, english) decides how questions and notes are written.",
           "term": r["term"], "breaks": r["breaks"],
           "exams": [e for e in r["exams"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", "")) and valid_day(e["date"])],
           "timetable": read_timetable(shot, list(subjects)) if shot else old.get("timetable", []),
           "subjects": subjects}
    for k in ("intostudy_courses",):           # keep what the setup guide already chose
        if old.get(k):
            cfg[k] = old[k]
    write(cfg)

    covered = {f for s in subjects.values() for t in s["topics"] for f in files
               if any(matches(f, p) for p in t["sources"] + s["shared"])}
    print(f"\nWrote {OUT.name}: term week 1 = {cfg['term']['week1']}")
    for n, s in subjects.items():
        ass = sum(1 for w in s["plan"] if w.get("assessment"))
        print(f"  {n} ({s['kind']}): {len(s['topics'])} topics, {len(s['plan'])} weeks planned, {ass} assessments")
    print(f"  {len(cfg['exams'])} exams, {len(cfg['breaks'])} breaks")
    print(f"  {len(covered)} of {len(files)} files are linked to a topic"
          f"{'; the rest are sorted by the AI in the background' if len(covered) < len(files) else ''}.")
    if dropped:
        print("  Ignored (did not match any file): " + "; ".join(dropped[:15]))


if __name__ == "__main__":
    main()
