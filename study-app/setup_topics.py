#!/usr/bin/env python3
"""Draft topics.json (subjects, topics, week-by-week plan, term dates) from your own course files, with Gemini.

Run it once after the first sync, when ../text has your course files:

    python3 setup_topics.py                          write topics.json from the files in ../text
    python3 setup_topics.py --timetable shot.png     also read your weekly timetable from a screenshot
    python3 setup_topics.py --timetable shot.png     (topics.json already there: only the timetable is replaced)
    python3 setup_topics.py --force                  redo topics.json from scratch

Needs GEMINI_API_KEY in .env (Study Desk writes it there when you add the key in Settings).
Check the result in the Programme page and fix anything odd in topics.json by hand.
"""
import base64
import fnmatch
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parent
TEXT = APP.parent / "text"
OUT = APP / "topics.json"
API = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
PLAN_WORDS = re.compile(r"plan|overview|scheme|schedule|syllabus|programme|program|outline|handbook|guide|calendar|key info",
                        re.I)
PLAN_CHARS, PLAN_TOTAL = 40_000, 400_000

S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
arr = lambda x: {"type": "array", "items": x}
obj = lambda req, **p: {"type": "object", "properties": p, "required": req}
CLASS = obj(["day", "start", "end", "subject"], day=I, start=S, end=S, subject=S, code=S, room=S, teacher=S)
TIMETABLE_SCHEMA = obj(["timetable"], timetable=arr(CLASS))
SCHEMA = obj(["term", "breaks", "exams", "subjects"],
             term=obj(["name", "week1"], name=S, week1=S),
             breaks=arr(obj(["name", "start", "end"], name=S, start=S, end=S)),
             exams=arr(obj(["subject", "name", "date"], subject=S, name=S, date=S)),
             subjects=arr(obj(["name", "plan_source", "shared", "topics", "plan"],
                              name=S, plan_source=S, shared=arr(S),
                              topics=arr(obj(["id", "code", "name", "sources"], id=S, code=S, name=S, sources=arr(S))),
                              plan=arr(obj(["week", "topics", "focus", "guess"], week=I, topics=arr(S), focus=S, guess=B)))))

RULES = """You set up a study tracker for a university foundation-year student. From the list of their course files
(and the text of any plans/overviews among them) produce their course structure as JSON:

- subjects: one per top-level folder of the file list, "name" EXACTLY the folder name. Skip folders that are not a
  taught subject (welcome, student services, wellbeing...).
- topics: the subject's real taught topics in teaching order, 6-15 per subject. id = short lowercase slug with a
  subject prefix letter ("p-momentum", "m-logs"), unique. code = the module/unit code used in the files if any
  (e.g. "AC1", "C3"), else a short label. name = a clear topic name a student would recognise.
- sources: path prefixes from the file list that hold that topic's files, e.g. "Physics/05 AC1" (a folder) or a
  full file path. Every prefix MUST be the start of at least one listed path. A "*" works as a wildcard
  ("EAP/04 Teacher/*Week 3/") when folder numbers vary. Cover as many files as you can; each file belongs to the
  topic it teaches.
- shared: prefixes of files useful for every topic of the subject (formula sheet, data booklet, past papers).
- plan: teaching weeks 1..N of this semester with the topic ids taught each week and a one-line focus. Take it from
  the plans/overviews/week folders when they exist (guess=false); where nothing says it, estimate from the topic
  order (guess=true). plan_source says where the plan came from in a few words.
- term: name (e.g. "Semester 1") and week1 = the Monday of teaching week 1 (YYYY-MM-DD), from the files; if not
  stated, the most likely Monday given today's date.
- breaks (holidays) and exams (end-of-semester exams/tests with dates) only if the files state them.
Use only what the files show; never invent teachers, codes or dates that are not in them."""


def api_key():
    for line in (APP / ".env").read_text().splitlines() if (APP / ".env").exists() else []:
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("No Gemini key: start Study Desk, add your key in Settings, then run this again.")


def gemini(parts, schema):
    body = {"contents": [{"role": "user", "parts": parts}], "systemInstruction": {"parts": [{"text": RULES}]},
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 32000, "responseMimeType": "application/json",
                                 "responseSchema": schema, "thinkingConfig": {"thinkingLevel": "high"}}}
    err = ""
    for attempt, model in enumerate(MODELS * 2):          # second round after a pause: 503 "high demand" passes
        if attempt == len(MODELS):
            print("   all busy, waiting 30 s…")
            time.sleep(30)
        req = urllib.request.Request(f"{API}/models/{model}:generateContent", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": api_key()})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out = json.load(r)
            return json.loads(out["candidates"][0]["content"]["parts"][-1]["text"])
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e)["error"]["message"].split("\n")[0][:160]
            except Exception:
                msg = ""
            err = f"{model}: error {e.code} {msg}"
        except (KeyError, IndexError, ValueError, TimeoutError, urllib.error.URLError) as e:
            err = f"{model}: {type(e).__name__} {e}"
        print("  ", err, "- trying the next model")
    sys.exit("Gemini failed: " + err)


def matches(path, prefix):
    return fnmatch.fnmatchcase(path, prefix + "*") if "*" in prefix else path.startswith(prefix)


def image_part(path):
    p = Path(path)
    return {"inline_data": {"mime_type": mimetypes.guess_type(p.name)[0] or "image/png",
                            "data": base64.b64encode(p.read_bytes()).decode()}}


def read_timetable(path, subjects):
    print("Reading the timetable screenshot…")
    r = gemini([image_part(path), {"text": "This is the student's weekly class timetable. List every weekly class: "
                f"day 1=Monday..5=Friday, start/end HH:MM, subject = one of {subjects} (the closest match), "
                "plus course code, room and teacher if shown."}], TIMETABLE_SCHEMA)
    return [c for c in r["timetable"] if c["subject"] in subjects]


def main():
    args = sys.argv[1:]
    shot = args[args.index("--timetable") + 1] if "--timetable" in args else None
    if OUT.exists() and "--force" not in args:
        if not shot:
            sys.exit(f"{OUT.name} already exists. Use --force to redo it, or --timetable <screenshot> to add classes.")
        cfg = json.loads(OUT.read_text())
        cfg["timetable"] = read_timetable(shot, list(cfg["subjects"]))
        OUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        return print(f"Timetable updated: {len(cfg['timetable'])} weekly classes.")

    files = sorted(str(p.relative_to(TEXT)).removesuffix(".txt") for p in TEXT.rglob("*.txt")) if TEXT.exists() else []
    if not files:
        sys.exit("No course files in ../text yet. Start Study Desk, sign in to IntoStudy and let the first sync finish.")
    plans, total = [], 0
    for f in files:
        if PLAN_WORDS.search(Path(f).name) and total < PLAN_TOTAL:
            t = (TEXT / (f + ".txt")).read_text(errors="ignore")[:PLAN_CHARS]
            plans.append(f"=== {f} ===\n{t}")
            total += len(t)
    print(f"{len(files)} course files, {len(plans)} plan/overview files. Asking Gemini (takes a minute or two)…")
    prompt = (f"Today is {date.today().isoformat()}.\n\nCOURSE FILES ({len(files)}):\n" + "\n".join(files) +
              "\n\nPLANS AND OVERVIEWS:\n" + ("\n\n".join(plans) or "(none found)"))
    r = gemini([{"text": prompt}], SCHEMA)

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
        for w in s["plan"]:
            w["topics"] = [i for i in w["topics"] if i in ids]
            if not w["guess"]:
                del w["guess"]
        name = s.pop("name")
        subjects[name] = {"shared": s["shared"], "plan_source": s["plan_source"], "topics": s["topics"], "plan": s["plan"]}

    cfg = {"_note": "Drafted by setup_topics.py from the course files. 'sources' are path prefixes inside ../text "
                    "('*' = wildcard); 'shared' files go with every topic of the subject; 'plan' is the weekly plan.",
           "term": r["term"], "breaks": r["breaks"], "exams": r["exams"],
           "timetable": read_timetable(shot, list(subjects)) if shot else [], "subjects": subjects}
    OUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

    covered = {f for s in subjects.values() for t in s["topics"] for f in files
               if any(matches(f, p) for p in t["sources"] + s["shared"])}
    print(f"\nWrote {OUT.name}: term week 1 = {cfg['term']['week1']}")
    for n, s in subjects.items():
        print(f"  {n}: {len(s['topics'])} topics, {len(s['plan'])} weeks planned")
    print(f"  {len(covered)} of {len(files)} files are linked to a topic"
          f"{'; the rest you can sort in Course files' if len(covered) < len(files) else ''}.")
    if dropped:
        print("  Ignored (did not match any file): " + "; ".join(dropped[:15]))
    if not shot:
        print("\nAdd your classes later with:  python3 setup_topics.py --timetable <screenshot of your timetable>")


if __name__ == "__main__":
    main()
