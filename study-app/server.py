#!/usr/bin/env python3
"""Study Desk: a small local server (Python standard library only).

Run:   python3 server.py        then open http://127.0.0.1:8890
Your Gemini API key is stored in study-app/.env on this Mac only.
"""
import fnmatch
import json
import mimetypes
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent
TEXT = APP.parent / "text"
COURSES = APP.parent / "courses"
OVERVIEW = APP.parent / "PROGRAM_OVERVIEW.md"
STATIC = APP / "static"
DATA = APP / "data"
ENV = APP / ".env"
PORT = int(os.environ.get("PORT", 8890))
API = "https://generativelanguage.googleapis.com/v1beta"
MAX_CONTEXT_CHARS = 600_000
LOCK = threading.Lock()

DATA.mkdir(exist_ok=True)
if not (APP / "topics.json").exists():          # first run: no subjects yet, so the app can only sync and take the key
    (APP / "topics.json").write_text(json.dumps({"term": {"name": "Semester 1", "week1": date.today().isoformat()},
                                                 "subjects": {}, "exams": [], "timetable": []}, indent=2))
    print("No topics.json yet: wrote an empty one. After the first sync run:  python3 setup_topics.py --force")
CONFIG = json.loads((APP / "topics.json").read_text())


def week_start(n):
    """Monday of teaching week n (weeks that start inside a break are skipped)."""
    d, k = date.fromisoformat(CONFIG["term"]["week1"]), 1
    while True:
        in_break = any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", []))
        if not in_break:
            if k == n:
                return d
            k += 1
        d += timedelta(days=7)


def week_of(day):
    """Teaching week number of a date (None before week 1 or during a break)."""
    if "term" not in CONFIG or day < date.fromisoformat(CONFIG["term"]["week1"]):
        return None
    for n in range(1, 60):
        if week_start(n) <= day < week_start(n) + timedelta(days=7):
            return n
    return None


for _sub in CONFIG["subjects"].values():          # a topic in the weekly plan starts in its first planned week
    for _t in _sub["topics"]:
        _weeks = [w["week"] for w in _sub.get("plan", []) if _t["id"] in w["topics"]]
        if _weeks and "term" in CONFIG:
            _t["start"] = week_start(min(_weeks)).isoformat()
    _prev = CONFIG.get("term", {}).get("week1", date.today().isoformat())
    for _t in _sub["topics"]:                      # not in the plan and no date given: after the topic before it
        _prev = _t.setdefault("start", _prev)
TOPICS = {t["id"]: {**t, "subject": s} for s, sub in CONFIG["subjects"].items() for t in sub["topics"]}


# ---------- storage ----------
def load(name, default):
    try:
        return json.loads((DATA / name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, obj):
    p = DATA / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(p)


def read_env():
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def api_key():
    return os.environ.get("GEMINI_API_KEY") or read_env().get("GEMINI_API_KEY", "")


def today():
    return date.today().isoformat()


# ---------- activity log (the source of every statistic) ----------
def log_event(kind, topic=None, **extra):
    """Call while holding LOCK."""
    act = load("activity.json", {"events": [], "minutes": {}})
    act["events"].append({"ts": datetime.now().isoformat(timespec="seconds"), "type": kind,
                          **({"topic": topic} if topic else {}), **extra})
    save("activity.json", act)


# ---------- course material ----------
ALL_FILES = sorted(str(p.relative_to(TEXT)) for p in TEXT.rglob("*.txt")) if TEXT.exists() else []


def matches(f, prefixes):
    """A source is a path prefix; one with * is a pattern (teachers renumber week folders: '*Semester 1 Week 4/')."""
    return any(fnmatch.fnmatchcase(f, p + "*") if "*" in p else f.startswith(p) for p in prefixes)


# Files reach a topic by folder (topics.json "sources") or by the sorter (data/file_topics.json):
# {path: {"topic": id | "general", "by": "ai" | "you", "confidence", "reason", "date"}}. Your choice wins.
def sorted_map():
    return load("file_topics.json", {})


def topic_of(f, smap):
    """(topic id | 'general' | None, how): how is 'you', 'folder' or 'ai'."""
    e = smap.get(f)
    if e and e.get("by") == "you":
        return e["topic"], "you"
    for tid, t in TOPICS.items():
        if matches(f, t["sources"]):
            return tid, "folder"
    return (e["topic"], "ai") if e else (None, None)


def is_shared(f):
    return any(matches(f, s.get("shared", [])) for s in CONFIG["subjects"].values())


def topic_counts():
    smap = sorted_map()
    c = Counter(topic_of(f, smap)[0] for f in ALL_FILES)
    for f in ALL_FILES:
        c.update(smap.get(f, {}).get("also", []))
    return c


def topic_files(tid, shared=True):
    t = TOPICS[tid]
    smap = sorted_map()
    extra = CONFIG["subjects"][t["subject"]].get("shared", []) if shared else []
    return [f for f in ALL_FILES if topic_of(f, smap)[0] == tid or tid in smap.get(f, {}).get("also", [])
            or matches(f, extra)]


def topic_material(tid, limit=MAX_CONTEXT_CHARS):
    parts, total = [], 0
    for f in topic_files(tid):
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        if len(text) < 40:
            continue
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > limit:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def topic_label(tid):
    t = TOPICS[tid]
    return f"{t['subject']}: {t['name']}" + (f" ({t['code']})" if t.get("code") else "")


def original_for(text_rel):
    """courses/ path of the original file behind a text/ file, if it exists."""
    p = (COURSES / text_rel.removesuffix(".txt")).resolve()
    return p if str(p).startswith(str(COURSES.resolve())) and p.is_file() else None


# ---------- Gemini ----------
_model_cache = {"name": None, "chain": None}
# Preferred models, in order. gemini-3.5-flash answered in ~2 s when the newest flash took 50-75 s under load
# (tested 8 Nov 2026). Any other flash models the key offers are used as fallbacks.
PREFERRED = ["gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"]


def model_chain():
    """The models to try, in order: the one chosen in Settings (if any), then preferred, then the rest."""
    chosen = (load("progress.json", {}).get("settings", {}).get("model")
              or os.environ.get("GEMINI_MODEL") or read_env().get("GEMINI_MODEL"))
    if _model_cache["chain"] is None:
        req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": api_key()})
        with urllib.request.urlopen(req, timeout=30) as r:
            models = json.load(r).get("models", [])
        avail = [m["name"].split("/", 1)[1] for m in models
                 if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
                 and not re.search(r"image|tts|audio|live|omni|exp|8b", m["name"])]
        rest = sorted((n for n in avail if n not in PREFERRED and "preview" not in n),
                      key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)) or (0,), reverse=True)
        _model_cache["chain"] = [n for n in PREFERRED if n in avail] + rest
        if not _model_cache["chain"]:
            raise RuntimeError("No Gemini 'flash' model is available for this key. Set one in Settings.")
    chain = list(_model_cache["chain"])
    return ([chosen] + [m for m in chain if m != chosen]) if chosen else chain


def pick_model():
    return model_chain()[0]


class GeminiError(Exception):
    pass


def gemini(contents, system=None, schema=None, temperature=0.6, think=True):
    try:
        return _gemini(contents, system, schema, temperature, think)
    except ValueError:                       # cut-off or broken JSON: one more try
        try:
            return _gemini(contents, system, schema, temperature, think)
        except ValueError:
            raise GeminiError("Gemini's answer came back broken. Try again.")


def _gemini(contents, system=None, schema=None, temperature=0.6, think=True):
    """think=True lets the model reason before answering (slower, far fewer mistakes); False for simple sorting jobs."""
    if not api_key():
        raise GeminiError("No Gemini API key yet. Add it in Settings.")
    body = {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": 32000}}
    if think and _model_cache.get("thinking", True):
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "high"}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    try:
        chain = model_chain()[:3]
    except urllib.error.HTTPError as e:
        raise GeminiError(f"Gemini rejected the API key (error {e.code}). Check it in Settings.")
    except (urllib.error.URLError, TimeoutError) as e:
        raise GeminiError(f"Could not reach Gemini: {getattr(e, 'reason', e)}")
    out, last_err = None, ""
    for i, model in enumerate(chain):
        try:
            req = urllib.request.Request(
                f"{API}/models/{model}:generateContent", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key()})
            with urllib.request.urlopen(req, timeout=120 if i < len(chain) - 1 else 180) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            if e.code == 400 and "thinking" in msg.lower() and "thinkingConfig" in body["generationConfig"]:
                del body["generationConfig"]["thinkingConfig"]      # this model can't think: go on without it
                _model_cache["thinking"] = False
                return _gemini(contents, system, schema, temperature, think=False)
            if e.code in (429, 500, 503, 504):   # busy or over quota: try the next model
                last_err = ("You've hit Gemini's free-tier limit. Wait a minute (or until tomorrow for the daily limit) "
                            "and try again.") if e.code == 429 else f"Gemini is busy right now ({e.code}). Try again in a minute."
                continue
            raise GeminiError(f"Gemini error {e.code}: {msg or e.reason}")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = f"Gemini took too long or couldn't be reached ({getattr(e, 'reason', e)}). Try again."
            continue
    if out is None:
        raise GeminiError(last_err or "Gemini didn't answer. Try again.")
    cands = out.get("candidates") or []
    if not cands:
        raise GeminiError("Gemini returned no answer (it may have been blocked). Try rephrasing.")
    text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
    return json.loads(text) if schema else text


def user_parts(text, image=None):
    parts = [{"text": text}]
    if image and image.get("data"):
        parts.append({"inlineData": {"mimeType": image.get("mime", "image/jpeg"), "data": image["data"]}})
    return parts


def history_contents(messages):
    return [{"role": "user" if m["role"] == "user" else "model", "parts": user_parts(m["text"], m.get("image"))}
            for m in messages[-24:]]


STYLE = (
    "You are a patient, encouraging tutor for an International Foundation Year student "
    "(NCUK, INTO Manchester). English is their second language: use clear, simple English and short "
    "paragraphs. If the student writes in Russian, answer in Russian but keep subject terms in English too. "
    "Write maths with LaTeX: $...$ inline and $$...$$ for display. Use Markdown for structure."
)


def system_for(tid, extra=""):
    material = topic_material(tid)
    src = ("There are no lesson files for this topic yet (it may not have been taught). "
           "Use the standard UK A-level / foundation-year syllabus and say so once.") if not TOPICS[tid]["sources"] else ""
    if material:
        src += ("\nHere are the student's own course materials. Base your teaching on them, follow their notation "
                "and methods, and when you use a file mention its name in [square brackets].\n" + material)
    return f"{STYLE}\nCurrent topic: {topic_label(tid)}.\n{extra}\n\n{src}"


# ---------- progress ----------
def progress():
    p = load("progress.json", {})
    p.setdefault("topics", {})
    p.setdefault("settings", {})
    p.setdefault("exams", CONFIG["exams"])
    return p


def record_study(p, tid, score=None, max_marks=None):
    t = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
    t["last"] = today()
    if score is not None and max_marks:
        pct = max(0.0, min(1.0, score / max_marks))
        t["history"].append({"date": t["last"], "score": score, "max": max_marks})
        old = t.get("confidence", 0)
        t["confidence"] = round(pct * 5, 1) if not old else round(0.7 * old + 0.3 * pct * 5, 1)


def review_card(card, grade):
    """SM-2, simplified. grade: 0 again, 3 hard, 4 good, 5 easy"""
    if grade < 3:
        card["reps"], card["interval"] = 0, 0
    else:
        card["reps"] = card.get("reps", 0) + 1
        card["interval"] = {1: 1, 2: 3}.get(card["reps"]) or round(card.get("interval", 1) * card.get("ease", 2.5))
        if grade == 3:
            card["interval"] = max(1, round(card["interval"] * 0.6))
    card["ease"] = max(1.3, card.get("ease", 2.5) + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    card["due"] = (date.today() + timedelta(days=card["interval"])).isoformat()
    return card


# ---------- statistics (computed only from saved data) ----------
def compute_stats():
    p, cards = progress(), load("cards.json", [])
    act = load("activity.json", {"events": [], "minutes": {}})
    events, minutes = act["events"], act.get("minutes", {})
    t0 = date.today()
    tdy = t0.isoformat()

    # activity per day
    per_day = Counter(e["ts"][:10] for e in events)
    for t in p["topics"].values():
        for h in t.get("history", []):
            per_day.setdefault(h["date"], 0)
    active = {d for d, n in per_day.items() if n} | {d for d, m in minutes.items() if m}
    streak, d = 0, t0
    if tdy not in active:
        d = t0 - timedelta(days=1)
    while d.isoformat() in active:
        streak += 1
        d -= timedelta(days=1)
    best, run, prev = 0, 0, None
    for day in sorted(active):
        cur = date.fromisoformat(day)
        run = run + 1 if prev and (cur - prev).days == 1 else 1
        best, prev = max(best, run), cur
    heat = [{"date": (t0 - timedelta(days=i)).isoformat(),
             "actions": per_day.get((t0 - timedelta(days=i)).isoformat(), 0),
             "minutes": minutes.get((t0 - timedelta(days=i)).isoformat(), 0)} for i in range(111, -1, -1)]

    # practice
    attempts = []
    for tid, t in p["topics"].items():
        if tid in TOPICS:
            for h in t.get("history", []):
                attempts.append({**h, "topic": tid, "subject": TOPICS[tid]["subject"]})
    attempts.sort(key=lambda a: a["date"])
    def acc(rows):
        mx = sum(r["max"] for r in rows)
        return {"answers": len(rows), "earned": round(sum(r["score"] for r in rows), 1), "available": mx,
                "accuracy": round(100 * sum(r["score"] for r in rows) / mx) if mx else None}
    by_topic = defaultdict(list)
    for a in attempts:
        by_topic[a["topic"]].append(a)
    topic_acc = sorted(({"topic": tid, "name": TOPICS[tid]["name"], "subject": TOPICS[tid]["subject"], **acc(r)}
                        for tid, r in by_topic.items()), key=lambda x: x["accuracy"])

    # flashcards
    reviews = [e for e in events if e["type"] == "card_review"]
    recent = [e for e in reviews if e["ts"][:10] >= (t0 - timedelta(days=30)).isoformat()]
    forecast = []
    for i in range(14):
        day = (t0 + timedelta(days=i)).isoformat()
        forecast.append({"date": day, "due": sum((c["due"] <= day) if i == 0 else (c["due"] == day) for c in cards)})

    # subjects & coverage
    subjects = []
    for s, sub in CONFIG["subjects"].items():
        rows = []
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            rows.append({"taught": t["start"] <= tdy, "studied": bool(tp.get("last")), "conf": tp.get("confidence", 0),
                         "practised": bool(tp.get("history"))})
        rated = [r["conf"] for r in rows if r["taught"] and r["conf"]]
        sub_attempts = [a for a in attempts if a["subject"] == s]
        subjects.append({
            "name": s, "topics": len(rows), "taught": sum(r["taught"] for r in rows),
            "studied": sum(r["studied"] for r in rows), "rated": len(rated),
            "practised": sum(r["practised"] for r in rows),
            "confidence": round(sum(rated) / len(rated), 1) if rated else None,
            "files": sum(n for tid, n in topic_counts().items() if TOPICS.get(tid, {}).get("subject") == s),
            "cards": sum(TOPICS.get(c["topic"], {}).get("subject") == s for c in cards),
            **{f"practice_{k}": v for k, v in acc(sub_attempts).items()},
        })

    # exam readiness: topics scheduled before the exam that are rated 3+
    exams = []
    for e in sorted(p["exams"], key=lambda e: e["date"]):
        scope = [t for t in TOPICS.values() if t["subject"] == e.get("subject") and t["start"] <= e["date"]]
        st = {t["id"]: topic_standing(p, t["id"]) for t in scope}
        ready = [t for t in scope if st[t["id"]]["status"] == "confirmed"]
        exams.append({**e, "days": (date.fromisoformat(e["date"]) - t0).days, "in_scope": len(scope), "ready": len(ready),
                      "studied": sum(x["status"] == "studied" for x in st.values()),
                      "behind": sum(x["behind"] for x in st.values()),
                      "not_started": sum(not p["topics"].get(t["id"], {}).get("last") for t in scope if t["start"] <= tdy)})

    week = [(t0 - timedelta(days=i)).isoformat() for i in range(7)]
    return {
        "today": tdy,
        "activity": {"streak": streak, "best_streak": best, "active_days": len(active),
                     "minutes_total": sum(minutes.values()), "minutes_week": sum(minutes.get(d, 0) for d in week),
                     "actions_week": sum(per_day.get(d, 0) for d in week), "heat": heat,
                     "first_day": min(active) if active else None},
        "ai": {"tutor": sum(e["type"] == "tutor" for e in events), "assistant": sum(e["type"] == "assistant" for e in events),
               "generated_questions": sum(e.get("count", 0) for e in events if e["type"] in ("practice_generate", "learn_test_generate")),
               "learn_questions": sum(e["type"] == "learn_ask" for e in events)},
        "practice": {**acc(attempts), "by_topic": topic_acc,
                     "trend": [{"date": a["date"], "pct": round(100 * a["score"] / a["max"]), "subject": a["subject"],
                                "topic": TOPICS[a["topic"]]["name"]} for a in attempts if a["max"]][-40:]},
        "cards": {"total": len(cards), "due": forecast[0]["due"] if forecast else 0,
                  "new": sum(c.get("reps", 0) == 0 for c in cards),
                  "learning": sum(0 < c.get("interval", 0) < 21 for c in cards),
                  "mature": sum(c.get("interval", 0) >= 21 for c in cards),
                  "reviews_total": len(reviews), "reviews_week": sum(e["ts"][:10] in week for e in reviews),
                  "retention": round(100 * sum(e.get("grade", 0) >= 3 for e in recent) / len(recent)) if recent else None,
                  "forecast": forecast},
        "subjects": subjects,
        "exams": exams,
        "learn": learn_stats(),
    }


# ---------- figures (diagrams and graphs inside questions) ----------
_PT = {"type": "string"}  # a point name from 'points'
DIAGRAM = {"type": "object", "properties": {
    "points": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
        "show": {"type": "boolean"}}, "required": ["name", "x", "y"]}},
    "segments": {"type": "array", "items": {"type": "object", "properties": {
        "a": _PT, "b": _PT, "label": {"type": "string"}, "dashed": {"type": "boolean"},
        "arrow": {"type": "boolean"}}, "required": ["a", "b"]}},
    "angles": {"type": "array", "items": {"type": "object", "properties": {
        "a": _PT, "vertex": _PT, "b": _PT, "label": {"type": "string"},
        "right": {"type": "boolean"}}, "required": ["a", "vertex", "b"]}},
    "arcs": {"type": "array", "items": {"type": "object", "properties": {
        "center": _PT, "a": _PT, "b": _PT, "label": {"type": "string"}}, "required": ["center", "a", "b"]}},
    "circles": {"type": "array", "items": {"type": "object", "properties": {
        "center": _PT, "r": {"type": "number"}, "dashed": {"type": "boolean"}}, "required": ["center", "r"]}},
    "curves": {"type": "array", "items": {"type": "object", "properties": {
        "through": {"type": "array", "items": _PT}, "dashed": {"type": "boolean"},
        "arrow": {"type": "boolean"}}, "required": ["through"]}},
    "ground": {"type": "array", "items": {"type": "object", "properties": {"a": _PT, "b": _PT}, "required": ["a", "b"]}},
    "boxes": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"}, "h": {"type": "number"},
        "label": {"type": "string"}}, "required": ["x", "y", "w", "h"]}},
    "texts": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "text": {"type": "string"}}, "required": ["x", "y", "text"]}}}}
GRAPH = {"type": "object", "properties": {
    "x_label": {"type": "string"}, "y_label": {"type": "string"},
    "x_min": {"type": "number"}, "x_max": {"type": "number"},
    "y_min": {"type": "number"}, "y_max": {"type": "number"},
    "series": {"type": "array", "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "expr": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]}}}}},
    "marks": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "label": {"type": "string"}}, "required": ["x", "y"]}}}}
FIGURE = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["none", "diagram", "graph"]},
    "caption": {"type": "string"}, "diagram": DIAGRAM, "graph": GRAPH}, "required": ["kind"]}
FIGURE_RULES = (
    "\n\nFIGURES (important): every question (or step) about a shape, triangle, angle, bearing, sector, vector, "
    "force, motion, circuit, wave, apparatus, molecule or graph MUST have a 'figure', just like a printed exam paper; "
    "this includes: triangles and angles, sectors, vectors, force/free-body diagrams, inclined planes, cliffs and "
    "projectiles, pulleys, circuits, waves, ray diagrams, molecules, graphs. Otherwise kind 'none' (always 'none' for "
    "English/EAP). Never put the answer in the figure; the unknown is labelled with its letter (x, θ, F, ?).\n"
    "- kind 'diagram': the app draws it from a spec. Coordinates: x right, y UP, roughly 0-360 by 0-240, drawn to "
    "scale where it matters (a 55° angle should look like 55°). 'points' are named (A, B, O, P1...); show=true prints "
    "the name next to it (use false for helper points). Everything else refers to point names: 'segments' (sides, "
    "rods, strings, surfaces; label = the length/value written beside it, e.g. '6 cm'; arrow=true for a vector or force "
    "pointing from a to b; dashed for construction lines), 'angles' (angle a-vertex-b with label like '55°' or 'θ'; "
    "right=true draws the right-angle square), 'arcs' (circle arc around center from a to b anticlockwise, for "
    "sectors/arc length), 'circles', 'curves' (smooth path through points, e.g. a projectile's path; arrow=true adds a "
    "direction arrow at the end), 'ground' (hatched surface such as a floor, cliff top or slope), 'boxes' (blocks, "
    "masses, resistors, cells; label inside), 'texts' (any other short label at x, y). Forces: one arrow per force "
    "from the object's point, labelled (e.g. 'W = 450 N'), lengths roughly in proportion.\n"
    "- kind 'graph' (the app plots it exactly): for a function give series[].expr in x with explicit * "
    "(e.g. '2*sin(x) + 1', 'x^2 - 3*x', 'exp(-x)'; allowed: + - * / ^ ( ) sin cos tan asin acos atan sqrt abs ln log "
    "exp pi e; radians, or sind/cosd/tand when the axis is in degrees); for data or motion graphs (v-t, s-t, "
    "titration curve) give series[].points. Set axis ranges and labels with units; 'marks' are labelled points. "
    "A one-line 'caption' is optional.\n"
    "Every name used in segments, angles, arcs, curves or ground must be defined in 'points'. "
    "Use 'angles' for every angle (never a plain text label). A launch angle is an angle between the velocity arrow "
    "and a horizontal helper point. In physics, most questions about forces, motion, "
    "projectiles, circuits, waves or optics need a figure; draw the real situation (cliff with ground, slope, "
    "string, pulley) rather than abstract lines. Examples:\n"
    'Triangle: {"kind":"diagram","diagram":{"points":[{"name":"A","x":0,"y":0,"show":true},{"name":"B","x":200,"y":0,'
    '"show":true},{"name":"C","x":60,"y":120,"show":true}],"segments":[{"a":"A","b":"B","label":"8 cm"},{"a":"A","b":"C",'
    '"label":"x"},{"a":"B","b":"C"}],"angles":[{"a":"B","vertex":"A","b":"C","label":"63°"}]}}\n'
    'Block on a 30° slope: {"kind":"diagram","diagram":{"points":[{"name":"O","x":0,"y":0},{"name":"Q","x":260,"y":0},'
    '{"name":"T","x":260,"y":150},{"name":"P","x":150,"y":87},{"name":"W","x":150,"y":-20},{"name":"N","x":110,"y":156},'
    '{"name":"F","x":90,"y":52}],"ground":[{"a":"O","b":"Q"}],"segments":[{"a":"O","b":"T"},{"a":"P","b":"W","arrow":true,'
    '"label":"W = 50 N"},{"a":"P","b":"N","arrow":true,"label":"R"},{"a":"P","b":"F","arrow":true,"label":"F"}],'
    '"angles":[{"a":"Q","vertex":"O","b":"T","label":"30°"}]}}\n'
    'Car leaving a 45 m cliff at 15 m/s: {"kind":"diagram","diagram":{"points":[{"name":"E","x":0,"y":90},'
    '{"name":"T","x":40,"y":90},{"name":"B","x":40,"y":0},{"name":"G","x":300,"y":0},{"name":"V","x":110,"y":90},'
    '{"name":"M","x":150,"y":67.5},{"name":"L","x":260,"y":0,"show":true}],"ground":[{"a":"E","b":"T"},{"a":"B","b":"G"}],'
    '"segments":[{"a":"T","b":"B","label":"45.0 m"},{"a":"T","b":"V","arrow":true,"label":"15.0 m s⁻¹"}],'
    '"curves":[{"through":["T","M","L"],"dashed":true,"arrow":true}],"texts":[{"x":150,"y":-15,"text":"x"}]}}\n'
    'v-t graph: {"kind":"graph","graph":{"x_label":"t / s","y_label":"v / m s⁻¹","series":[{"points":[{"x":0,"y":0},'
    '{"x":4,"y":12},{"x":10,"y":12},{"x":14,"y":0}]}]}}')


def clean_figure(f):
    """Keep a figure only if it has something to draw."""
    if not isinstance(f, dict) or f.get("kind") not in ("diagram", "graph"):
        return None
    cap = str(f.get("caption", ""))[:200]
    if f["kind"] == "diagram":
        d = f.get("diagram") or {}
        if not d.get("points") and not d.get("boxes"):
            return None
        return {"kind": "diagram", "diagram": d, "caption": cap}
    g = f.get("graph") or {}
    if not any(s.get("expr") or s.get("points") for s in g.get("series", [])):
        return None
    return {"kind": "graph", "graph": g, "caption": cap}


def with_figures(items):
    for q in items:
        fig = clean_figure(q.get("figure"))
        if fig:
            q["figure"] = fig
        else:
            q.pop("figure", None)
    return items


# ---------- API handlers ----------
QUESTION_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {
    "type": "object", "properties": {"question": {"type": "string"}, "marks": {"type": "integer"},
                                     "mark_scheme": {"type": "string"}, "source": {"type": "string"},
                                     "figure": FIGURE},
    "required": ["question", "marks", "mark_scheme"]}}}, "required": ["questions"]}
MARK_SCHEMA = {"type": "object", "properties": {
    "score": {"type": "number"}, "max_marks": {"type": "number"}, "feedback": {"type": "string"},
    "model_answer": {"type": "string"}}, "required": ["score", "max_marks", "feedback", "model_answer"]}
CARDS_SCHEMA = {"type": "object", "properties": {"cards": {"type": "array", "items": {
    "type": "object", "properties": {"front": {"type": "string"}, "back": {"type": "string"}},
    "required": ["front", "back"]}}}, "required": ["cards"]}


def api_state(_):
    p, cards = progress(), load("cards.json", [])
    tdy = today()
    subjects = []
    counts = topic_counts()
    for s, sub in CONFIG["subjects"].items():
        topics = []
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            hist = tp.get("history", [])
            mx = sum(h["max"] for h in hist)
            topics.append({**{k: t[k] for k in ("id", "name", "code", "start")},
                           "files": counts[t["id"]],
                           "confidence": tp.get("confidence", 0), "last": tp.get("last"),
                           "attempts": len(hist),
                           "accuracy": round(100 * sum(h["score"] for h in hist) / mx) if mx else None,
                           "cards": sum(c["topic"] == t["id"] for c in cards),
                           "due": sum(c["topic"] == t["id"] and c["due"] <= tdy for c in cards),
                           "learn": learn_summary(t["id"]), **topic_standing(p, t["id"])})
        subjects.append({"name": s, "topics": topics})
    readiness = []
    for e in sorted(p["exams"], key=lambda e: e["date"]):
        if e["date"] < tdy:
            continue
        scope = [t for sub in subjects if sub["name"] == e.get("subject") for t in sub["topics"] if t["start"] <= e["date"]]
        readiness.append({**e, "days": (date.fromisoformat(e["date"]) - date.today()).days, "in_scope": len(scope),
                          "confirmed": sum(t["status"] == "confirmed" for t in scope),
                          "studied": sum(t["status"] == "studied" for t in scope),
                          "behind": sum(t["behind"] for t in scope)})
    return {"subjects": subjects, "exams": p["exams"], "breaks": CONFIG.get("breaks", []),
            "assessments": upcoming_assessments(), "readiness": readiness, "pass_mark": PASS_MARK,
            "tasks": tasks_for_app(),
            "timetable": CONFIG.get("timetable", []), "today": tdy, "has_key": bool(api_key()),
            "model": p["settings"].get("model", ""), "name": p["settings"].get("name", ""),
            "due_total": sum(c["due"] <= tdy for c in cards)}


def api_stats(_):
    return compute_stats()


TUTOR_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "figure": FIGURE,
    "has_problem": {"type": "boolean"}, "has_calculation": {"type": "boolean"},
    "mistake": {"type": "object", "properties": {"found": {"type": "boolean"}, "front": {"type": "string"},
                                                  "back": {"type": "string"}}, "required": ["found", "front", "back"]}},
    "required": ["answer", "figure", "has_problem", "has_calculation", "mistake"],
    "propertyOrdering": ["figure", "answer", "has_problem", "has_calculation", "mistake"]}
DRAW_SCHEMA = {"type": "object", "properties": {"figure": FIGURE}, "required": ["figure"]}


def thin_figure(f):
    """A diagram with dots but nothing drawn between them."""
    d = (f or {}).get("diagram") or {}
    return bool(f) and f.get("kind") == "diagram" and not any(d.get(k) for k in ("segments", "circles", "arcs", "curves", "boxes"))
CHECK_SCHEMA = {"type": "object", "properties": {
    "correct": {"type": "boolean"}, "issue": {"type": "string"}, "corrected_answer": {"type": "string"}},
    "required": ["correct"]}
TUTOR_RULES = (
    "\nHOW TO TEACH (you are as careful as the best human tutor):\n"
    "- Explaining: start from what the idea means in plain words, then the rule or formula with every symbol named, "
    "then ONE fully worked example where each line says what you did and why, then the mistake students usually make. "
    "Finish with one short problem for the student to try (do not solve it).\n"
    "- Examples must be concrete, with real numbers and units, in the style of the student's exam questions.\n"
    "- When the student is solving a problem, do NOT give the solution. 'Give me a hint' = one small nudge. "
    "'Show the next step' = only the next line of working. 'Show the full solution' = the whole worked solution with marks-style steps.\n"
    "- When the student sends their working (typed or a photo): read it line by line, say what is right, quote the "
    "FIRST line that is wrong, explain exactly why it is wrong and what rule fixes it, then ask them to redo it from "
    "that line. Do not just give the right answer. If you found a mistake set mistake.found = true, mistake.front = a "
    "short question (one sentence, maths in $...$) that tests the exact thing they got wrong, mistake.back = ONLY the "
    "correct rule or method in 1-3 lines (two separate fields, final wording only, no drafts) "
    "(it becomes a flashcard). Always fill 'mistake'; when there is no student working or no error, mistake.found = false.\n"
    "- Before you answer, work every calculation through yourself and check the final number a second way "
    "(substitute back, check units and size). Never guess a number.\n"
    "- 'answer' is Markdown with LaTeX and MUST be laid out for reading, never one block of text: short paragraphs "
    "separated by blank lines (\\n\\n), '### ' headings for the parts (for example: The idea, The formula, Worked example, "
    "Common mistake, Your turn), each step of working on its own line as a numbered list, inline maths and units in single $...$ (never $$ inside a sentence), display maths in $$...$$ alone on "
    "its own line, **bold** for key terms. has_problem = true when your answer ends with a problem the student must now "
    "solve or they are in the middle of one. has_calculation = true when your answer contains worked numbers or algebra.\n"
    "- 'figure': draw one whenever a picture helps the explanation or the problem (it appears under your answer); "
    "refer to it in the text ('in the diagram below'). Otherwise kind 'none'. A figure must show the whole situation "
    "the way a textbook would, never just two labelled points: e.g. circular motion = the circle (circles), the object "
    "on it, the radius segment labelled r, a velocity arrow along the tangent labelled v and a force arrow pointing to "
    "the centre labelled F; a triangle = all three sides with the known lengths, every given angle as an 'angles' entry with its label (30°) and the right angle with right=true; forces = every force as a "
    "labelled arrow. Example, object on a circle: "
    '{"kind":"diagram","diagram":{"points":[{"name":"O","x":150,"y":120,"show":true},{"name":"P","x":250,"y":120},'
    '{"name":"V","x":250,"y":200},{"name":"F","x":195,"y":120}],"circles":[{"center":"O","r":100}],'
    '"segments":[{"a":"O","b":"P","label":"r","dashed":true},{"a":"P","b":"V","arrow":true,"label":"v"},'
    '{"a":"P","b":"F","arrow":true,"label":"F"}]}}')


def tidy_math(text):
    """Repair the LaTeX delimiters the model sometimes gets wrong, so formulas always render."""
    text = re.sub(r"\$[|\]]", "$$", text)                       # "$|" typed instead of "$$"
    for ch, letter in (("\t", "t"), ("\f", "f"), ("\r", "r"), ("\b", "b")):   # "\text" sent as TAB + "ext", etc.
        text = re.sub(re.escape(ch) + r"(?=[a-z])", lambda m: "\\" + letter, text)
    out = []
    for line in text.split("\n"):
        if "$$" in line and re.sub(r"\$\$.+?\$\$", "", line).strip(" .,;:"):   # $$..$$ inside a sentence: inline
            line = line.replace("$$", "$")
        out.append(line)
    text = "\n".join(out)
    if text.count("$$") % 2:                                     # an unclosed display formula
        text = text.replace("$$", "$")
    return text


def tutor_reply(tid, messages, system):
    """One tutor answer: structured (text + figure), double-checked when it contains calculations."""
    contents = history_contents(messages)
    r = gemini(contents, system + FIGURE_RULES + TUTOR_RULES, TUTOR_SCHEMA, 0.4)
    answer, checked = str(r.get("answer", "")).strip(), False
    if not answer:
        raise GeminiError("Gemini returned an empty answer. Try again.")
    if (r.get("has_calculation") or r.get("has_problem")) and TOPICS[tid]["subject"] != "EAP":
        try:                                   # a second, independent pass over the numbers
            question = messages[-1].get("text", "") if messages else ""
            c = gemini([{"role": "user", "parts": user_parts(
                "You are checking a tutor's answer before a student sees it. Solve the problem yourself from scratch, "
                "then compare. Check every number, sign, unit, formula, algebra step and any word that makes no sense. If the tutor sets a problem for the student, solve "
                "it too: it must be fully specified, physically possible and give a sensible answer (no negative "
                "force, mass or length, no impossible triangle); if not, change its numbers so it works. correct = true only if "
                "everything is right. If anything is wrong: issue = what is wrong (one line), corrected_answer = the "
                "full answer rewritten correctly in the same style, language and Markdown/LaTeX.\n\n"
                f"STUDENT ASKED:\n{question[:3000]}\n\nTUTOR'S ANSWER:\n{answer}",
                messages[-1].get("image") if messages else None)}],
                f"{STYLE}\nTopic: {topic_label(tid)}.", CHECK_SCHEMA, 0.1)
            checked = True
            if not c.get("correct") and len(str(c.get("corrected_answer", ""))) > 40:
                answer = c["corrected_answer"].strip()
        except GeminiError:
            pass                               # over quota or busy: show the first answer unchecked
    figure = clean_figure(r.get("figure"))
    if thin_figure(figure):                    # the drawing came out empty: ask for it on its own
        try:
            figure = clean_figure(gemini([{"role": "user", "parts": [{"text":
                "Draw the ONE figure that best illustrates this tutor's answer (the situation in its worked example "
                "or its problem), complete like a textbook diagram: every line, circle, arrow, angle and label.\n\n"
                f"TUTOR'S ANSWER:\n{answer}"}]}], "You draw exam-paper figures." + FIGURE_RULES, DRAW_SCHEMA, 0.2).get("figure"))
        except GeminiError:
            figure = None
        if thin_figure(figure):
            figure = None
    out = {"answer": tidy_math(answer), "figure": figure, "problem": bool(r.get("has_problem")),
           "checked": checked, "card_added": False}
    m = r.get("mistake") or {}
    if m.get("found") and "Back:" in str(m.get("front", "")):     # both sides landed in one field: take the last pair
        tail = str(m["front"]).rsplit("Front:", 1)[-1]
        m["front"], m["back"] = (x.strip() for x in tail.split("Back:", 1))
    if m.get("found") and m.get("front") and m.get("back"):
        with LOCK:
            cards = load("cards.json", [])
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": str(m["front"])[:600],
                          "back": str(m["back"])[:1200], "ease": 2.5, "interval": 0, "reps": 0, "due": today(),
                          "created": today(), "from": "mistake"})
            save("cards.json", cards)
            log_event("cards_from_mistakes", tid, count=1)
        out["card_added"] = True
    return out


def api_chat(b):
    tid = b["topic"]
    out = tutor_reply(tid, b.get("messages", []), system_for(tid))
    with LOCK:
        p = progress()
        record_study(p, tid)
        save("progress.json", p)
        log_event("tutor", tid)
    return out


def assistant_context(current_topic=None):
    s = compute_stats()
    p = progress()
    lines = []
    counts = topic_counts()
    for subj, sub in CONFIG["subjects"].items():
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            hist = tp.get("history", [])
            mx = sum(h["max"] for h in hist)
            lines.append(f"- [{subj}] {t['name']} {t.get('code', '')} | class starts {t['start']} | "
                         f"confidence {tp.get('confidence', 0)}/5 | last studied {tp.get('last') or 'never'} | "
                         f"practice {len(hist)} answers" + (f", {round(100 * sum(h['score'] for h in hist) / mx)}% marks" if mx else "") +
                         f" | {counts[t['id']]} course files" +
                         (lambda ls: f" | learn mode: {ls['passed']}/{ls['subs']} subtopics passed (pass = {PASS_MARK}%+ in mini test)"
                          if ls["built"] else " | learn mode: not started")(learn_summary(t["id"])))
    overview = OVERVIEW.read_text() if OVERVIEW.exists() else ""
    summary = {k: s[k] for k in ("activity", "practice", "cards", "exams")}
    summary["activity"] = {k: v for k, v in summary["activity"].items() if k != "heat"}
    summary["practice"] = {k: v for k, v in summary["practice"].items() if k != "trend"}
    summary["cards"] = {k: v for k, v in summary["cards"].items() if k != "forecast"}
    days = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    tt = "\n".join(f"- {days[c['day']]} {c['start']}-{c['end']} {c['subject']} ({c['code']}), room {c['room']}, teacher {c['teacher']}"
                   for c in CONFIG.get("timetable", []))
    ctx = (f"Today is {today()} ({date.today().strftime('%A')}).\n\nWEEKLY CLASS TIMETABLE (no classes during breaks: "
           f"{json.dumps(CONFIG.get('breaks', []))}):\n{tt}\n\nPROGRAMME OVERVIEW (from the student's course files):\n{overview}\n\n"
           f"WHERE THE CLASS IS NOW:\n{week_now_text()}\n\n"
           f"THE STUDENT'S STANDING (studied = ticked after studying in Gemini; confirmed = passed a check):\n{brief_text()}\n\n"
           f"TOPICS AND THE STUDENT'S PROGRESS:\n" + "\n".join(lines) +
           f"\n\nSTUDY STATISTICS (JSON, saved data only):\n{json.dumps(summary, ensure_ascii=False)}")
    if current_topic in TOPICS:
        ctx += f"\n\nThe student currently has this topic open: {topic_label(current_topic)}.\n" + topic_material(current_topic, 250_000)
    return ctx


def api_assistant(b):
    system = (STYLE + "\nYou are the student's personal study assistant inside the Study Desk app. You know their "
              "programme, schedule, exams and progress (below). Help them plan, decide what to study, explain things "
              "and stay motivated. Base any claim about their progress ONLY on the data below; if the data is empty, "
              "say so plainly. Keep answers focused and practical. The app has: Today page, subject pages, topic pages "
              "(Tutor, Practice questions, Flashcards, Files), Flashcards review, Profile (statistics), Settings.\n\n"
              + assistant_context(b.get("topic")))
    answer = gemini(history_contents(b.get("messages", [])), system, temperature=0.5)
    with LOCK:
        log_event("assistant", b.get("topic") if b.get("topic") in TOPICS else None)
    return {"answer": answer}


def api_insights(_):
    system = (STYLE + "\nYou are analysing a student's study data. Use ONLY the numbers and facts provided. Never "
              "invent scores, times or habits. If there is too little data for a conclusion, say what data is missing "
              "and how to get it (e.g. 'mark some practice answers').")
    prompt = ("Write a short progress report in Markdown with these sections:\n"
              "### Where you stand\n### Biggest risks before the exams\n### Plan for the next 7 days\n"
              "Make the plan concrete: day by day, naming topics and actions available in the app "
              "(tutor, practice questions, flashcards). Max ~250 words.\n\n" + assistant_context())
    answer = gemini([{"role": "user", "parts": [{"text": prompt}]}], system, temperature=0.4)
    with LOCK:
        log_event("insights")
    return {"answer": answer}


def api_practice_generate(b):
    tid, n = b["topic"], max(1, min(int(b.get("count", 3)), 8))
    prompt = (f"Write {n} new practice questions on {topic_label(tid)} at {b.get('difficulty', 'exam')} difficulty. "
              "Copy the style, wording and mark allocation of the exam questions and past-paper questions in the "
              "materials (but do not copy them word for word). Mix short and multi-step questions. For each, give a "
              "detailed mark scheme listing what earns each mark (M1, A1, B1 style for maths), and the name of the "
              "material it is modelled on as 'source'." + FIGURE_RULES)
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], system_for(tid), QUESTION_SCHEMA, 0.9)
    with_figures(out.get("questions", []))
    with LOCK:
        log_event("practice_generate", tid, count=len(out.get("questions", [])))
    return out


def api_practice_mark(b):
    tid = b["topic"]
    prompt = (f"Mark this answer strictly but fairly, like an exam marker using the mark scheme.\n\n"
              f"QUESTION ({b['marks']} marks):\n{b['question']}\n\nMARK SCHEME:\n{b['mark_scheme']}\n\n"
              f"STUDENT ANSWER:\n{b.get('answer') or '(see attached photo of working)'}\n\n"
              "Give score (can be partial), max_marks, feedback (what was right, what lost marks and why, one tip), "
              "and a full model_answer.")
    result = gemini([{"role": "user", "parts": user_parts(prompt, b.get("image"))}], system_for(tid), MARK_SCHEMA, 0.2)
    with LOCK:
        p = progress()
        record_study(p, tid, result["score"], result["max_marks"])
        save("progress.json", p)
        log_event("practice_mark", tid, score=result["score"], max=result["max_marks"])
        result["confidence"] = p["topics"][tid]["confidence"]
    return result


def api_cards_generate(b):
    tid, n = b["topic"], max(3, min(int(b.get("count", 10)), 25))
    existing = [c["front"] for c in load("cards.json", []) if c["topic"] == tid]
    prompt = (f"Make {n} flashcards for {topic_label(tid)} from the materials: key definitions, formulae, rules, "
              "methods and common exam facts. Front: a short question or prompt. Back: a concise answer (LaTeX "
              "allowed). One idea per card."
              + (f"\nDo not repeat these existing cards: {json.dumps(existing[:200], ensure_ascii=False)}" if existing else ""))
    new = gemini([{"role": "user", "parts": [{"text": prompt}]}], system_for(tid), CARDS_SCHEMA, 0.5)["cards"]
    with LOCK:
        cards = load("cards.json", [])
        for c in new:
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": c["front"], "back": c["back"],
                          "ease": 2.5, "interval": 0, "reps": 0, "due": today(), "created": today()})
        save("cards.json", cards)
        log_event("cards_generate", tid, count=len(new))
    return {"added": len(new)}


def api_cards_due(b):
    cards = [c for c in load("cards.json", []) if c["due"] <= today() and (not b.get("topic") or c["topic"] == b["topic"])]
    for c in cards:
        c["topic_name"] = topic_label(c["topic"])
        c["subject"] = TOPICS[c["topic"]]["subject"]
    return {"cards": cards}


def api_cards_all(b):
    return {"cards": sorted((c for c in load("cards.json", []) if c["topic"] == b["topic"]), key=lambda c: c["due"])}


def api_cards_review(b):
    with LOCK:
        cards = load("cards.json", [])
        for c in cards:
            if c["id"] == b["id"]:
                review_card(c, int(b["grade"]))
                p = progress()
                record_study(p, c["topic"])
                save("progress.json", p)
                log_event("card_review", c["topic"], grade=int(b["grade"]))
        save("cards.json", cards)
    return {"ok": True}


def api_cards_delete(b):
    with LOCK:
        save("cards.json", [c for c in load("cards.json", []) if c["id"] != b["id"]])
    return {"ok": True}


def api_confidence(b):
    with LOCK:
        p = progress()
        p["topics"].setdefault(b["topic"], {"confidence": 0, "history": []})["confidence"] = float(b["level"])
        save("progress.json", p)
        log_event("confidence", b["topic"], level=float(b["level"]))
    return {"ok": True}


def api_ping(_):
    """One call per active minute from the open app: real study time."""
    with LOCK:
        act = load("activity.json", {"events": [], "minutes": {}})
        act.setdefault("minutes", {})[today()] = act["minutes"].get(today(), 0) + 1
        save("activity.json", act)
    return {"ok": True}


def api_topic_files(b):
    files = []
    for f in topic_files(b["topic"], shared=False):
        parts = f.split("/")
        size = (TEXT / f).stat().st_size
        files.append({"path": f, "name": parts[-1].removesuffix(".txt"),
                      "folder": " / ".join(re.sub(r"^\d+\s+", "", x) for x in parts[1:-1]),
                      "readable": size > 60, "original": bool(original_for(f))})
    return {"files": files}


def api_topic_search(b):
    """Find a file of this topic by its name or by words in its text (all words must be there)."""
    words = [w for w in re.findall(r"\w+", str(b.get("q", "")).lower()) if len(w) > 1][:6]
    out = []
    if not words:
        return {"results": out}
    for f in topic_files(b["topic"], shared=False):
        name = f.rsplit("/", 1)[-1].removesuffix(".txt")
        in_name = all(w in name.lower() for w in words)
        text = (TEXT / f).read_text(errors="ignore")
        low = text.lower()
        if not in_name and not all(w in low for w in words):
            continue
        i = low.find(words[0])
        snippet = re.sub(r"\s+", " ", text[max(0, i - 70):i + 130]).strip() if i >= 0 else ""
        out.append({"path": f, "name": name, "in_name": in_name, "hits": sum(low.count(w) for w in words),
                    "snippet": snippet, "folder": " / ".join(re.sub(r"^\d+\s+", "", x) for x in f.split("/")[1:-1]),
                    "original": bool(original_for(f))})
    out.sort(key=lambda r: (not r["in_name"], -r["hits"]))
    return {"results": out[:40], "words": words}


def api_settings(b):
    with LOCK:
        if b.get("key"):
            key = b["key"].strip()
            if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,300}", key):
                raise GeminiError("That doesn't look like a Gemini API key.")
            env = read_env()
            env["GEMINI_API_KEY"] = key
            ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
            os.chmod(ENV, 0o600)
            _model_cache["chain"] = None
        p = progress()
        if "model" in b:
            p["settings"]["model"] = b["model"].strip()
            _model_cache["chain"] = None
        if "name" in b:
            p["settings"]["name"] = str(b["name"]).strip()[:40]
        if isinstance(b.get("exams"), list):
            p["exams"] = [{"subject": e.get("subject", ""), "name": str(e["name"])[:80], "date": e["date"]}
                          for e in b["exams"] if e.get("name") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", ""))]
        save("progress.json", p)
    if b.get("key"):
        try:
            return {"ok": True, "model": pick_model()}
        except urllib.error.HTTPError as e:
            raise GeminiError(f"Google rejected this key (error {e.code}). Check you copied the whole key.")
        except urllib.error.URLError as e:
            raise GeminiError(f"Key saved, but Google couldn't be reached to check it: {e.reason}")
    return {"ok": True}


# =====================================================================
# LEARN MODE: subtopics, guided lessons, notes, slides, ask, mini tests
# =====================================================================
LEARN_DIR = DATA / "learn"
LEARN_DIR.mkdir(exist_ok=True)
PASS_MARK = 80  # percent needed in a mini test to pass a subtopic
DIFFICULTY = {
    "easy": "easy: recall of definitions and one-step questions",
    "medium": "medium: standard textbook questions with two or three steps",
    "hard": "hard: multi-step problems and tricky distractors",
    "exam": "exam style: exactly like the NCUK IFY exam papers in the materials, mixed difficulty",
}


def learn_load(tid):
    try:
        return json.loads((LEARN_DIR / f"{tid}.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"outline": None, "subs": {}}


def learn_save(tid, obj):
    p = LEARN_DIR / f"{tid}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(p)


def learn_summary(tid):
    L = learn_load(tid)
    outline = L.get("outline") or []
    subs = L.get("subs", {})
    passed = [s for s in outline if subs.get(s["id"], {}).get("passed")]
    started = [s for s in outline if subs.get(s["id"], {}).get("step") or subs.get(s["id"], {}).get("tests")]
    # next = the unpassed subtopic you worked on most recently, otherwise the first unpassed one
    open_subs = [s for s in outline if not subs.get(s["id"], {}).get("passed")]
    worked = sorted((s for s in open_subs if subs.get(s["id"], {}).get("last")),
                    key=lambda s: subs[s["id"]]["last"], reverse=True)
    nxt = worked[0] if worked else (open_subs[0] if open_subs else None)
    tests = [t for s in subs.values() for t in s.get("tests", [])]
    return {"built": bool(outline), "subs": len(outline), "passed": len(passed), "started": len(started),
            "next": {"id": nxt["id"], "title": nxt["title"]} if nxt else None,
            "complete": bool(outline) and len(passed) == len(outline),
            "tests": len(tests), "last_learned": max((s.get("last", "") for s in subs.values()), default="") or None}


def find_sub(tid, sid):
    L = learn_load(tid)
    for i, s in enumerate(L.get("outline") or []):
        if s["id"] == sid:
            return L, s, i
    raise KeyError(f"subtopic {sid} (build the lesson plan first)")


def sub_material(tid, sub, limit=350_000):
    """The subtopic's own files first, then the rest of the topic, up to the limit."""
    wanted = [w.lower() for w in sub.get("files", []) if w]
    files = topic_files(tid)
    first = [f for f in files if any(w in f.lower() for w in wanted)]
    parts, total = [], 0
    for f in first + [f for f in files if f not in first]:
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        if len(text) < 40:
            continue
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > limit:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def learn_system(tid, sub=None, extra=""):
    material = sub_material(tid, sub) if sub else topic_material(tid, 350_000)
    no_lessons = "" if TOPICS[tid]["sources"] else (
        "There are no lesson files for this topic yet (it may not have been taught). Use the standard UK A-level / "
        "NCUK foundation-year syllabus.\n")
    focus = (f"\nCurrent subtopic: {sub['title']}. Summary: {sub.get('summary', '')}. "
             f"Objectives: {'; '.join(sub.get('objectives', []))}.") if sub else ""
    return (f"{STYLE}\nTopic: {topic_label(tid)}.{focus}\n{extra}\n{no_lessons}"
            + (f"\nThe student's course materials (base everything on them and follow their notation and methods):\n{material}" if material else ""))


OUTLINE_SCHEMA = {"type": "object", "properties": {"subtopics": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "objectives": {"type": "array", "items": {"type": "string"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "minutes": {"type": "integer"}}, "required": ["title", "summary", "objectives"]}}}, "required": ["subtopics"]}
MCQ = {"type": "object", "properties": {
    "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
    "answer_index": {"type": "integer"}, "explanation": {"type": "string"}, "figure": FIGURE},
    "required": ["question", "options", "answer_index", "explanation"]}
LESSON_SCHEMA = {"type": "object", "properties": {
    "intro": {"type": "string"},
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "explain": {"type": "string"}, "figure": FIGURE, "check": MCQ}, "required": ["title", "explain", "check"]}},
    "summary": {"type": "string"}}, "required": ["intro", "steps", "summary"]}
SLIDES_SCHEMA = {"type": "object", "properties": {"slides": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string"}}, "required": ["title", "bullets"]}}}, "required": ["slides"]}
TEST_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {**MCQ, "properties": {
    **MCQ["properties"], "skill": {"type": "string"}}}}}, "required": ["questions"]}


def clean_mcqs(items):
    out = []
    for q in items:
        opts = [str(o) for o in q.get("options", [])][:5]
        ai = q.get("answer_index")
        if len(opts) >= 2 and isinstance(ai, int) and 0 <= ai < len(opts):
            if not any(re.search(r"\b(above|below|both|neither|all of|none of)\b", o, re.I) for o in opts):
                order = random.sample(range(len(opts)), len(opts))     # the model likes to put the answer first
                opts, ai = [opts[i] for i in order], order.index(ai)
            out.append({**q, "options": opts, "answer_index": ai})
    return with_figures(out)


def slugify(s, i):
    return f"s{i + 1}-" + (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "part")


def api_learn_outline(b):
    tid = b["topic"]
    L = learn_load(tid)
    if L.get("outline") and not b.get("refresh"):
        return {"outline": L["outline"], "subs": L.get("subs", {}), "summary": learn_summary(tid), "pass_mark": PASS_MARK}
    t = TOPICS[tid]
    folders = sorted({" / ".join(f.split("/")[1:-1]) for f in topic_files(tid, shared=False)})
    prompt = (f"Split the topic '{t['name']}' ({t['subject']}{', ' + t['code'] if t.get('code') else ''}) into 3 to 8 "
              "lesson-sized subtopics in the order they should be learned, the way a teacher would plan separate lessons. "
              "Each needs a short clear title, a one-sentence summary, 2-4 measurable learning objectives, the names of the "
              "course files that cover it (exact file names from the materials, max 6), and an estimate of minutes to learn it. "
              "Cover everything in the materials that could be examined. Skip admin, homework logistics and teacher-only notes."
              + (f"\nCourse folders for this topic: {json.dumps(folders, ensure_ascii=False)}" if folders else ""))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid), OUTLINE_SCHEMA, 0.3)
    outline = [{"id": slugify(s["title"], i), "title": s["title"].strip(), "summary": s.get("summary", ""),
                "objectives": s.get("objectives", [])[:5], "files": s.get("files", [])[:6],
                "minutes": int(s.get("minutes") or 20)} for i, s in enumerate(out["subtopics"][:8])]
    with LOCK:
        L = learn_load(tid)
        L["outline"] = outline
        L.setdefault("subs", {})
        learn_save(tid, L)
        log_event("learn_outline", tid, count=len(outline))
    return {"outline": outline, "subs": L["subs"], "summary": learn_summary(tid), "pass_mark": PASS_MARK}


def _cached_part(b, part, make):
    tid, sid = b["topic"], b["sub"]
    L, sub, _ = find_sub(tid, sid)
    cached = L["subs"].get(sid, {}).get(part)
    if cached and not b.get("refresh"):
        return {part: cached}
    value = make(tid, sub)
    with LOCK:
        L = learn_load(tid)
        L["subs"].setdefault(sid, {})[part] = value
        learn_save(tid, L)
        log_event(f"learn_{part}", tid, sub=sid)
    return {part: value}


def make_lesson(tid, sub):
    prompt = ("Create a guided lesson for this subtopic, like a patient one-to-one tutor. 4 to 7 steps in a logical "
              "order. Each step: a short title; 'explain' in Markdown (100-220 words) that teaches ONE idea, with a worked "
              "example where useful; and one multiple-choice 'check' question (4 options, answer_index 0-3, explanation "
              "of why the answer is right and why the most tempting wrong option is wrong) that tests that step. "
              "Also a 2-3 sentence 'intro' (why this matters, what they'll be able to do) and a short 'summary'."
              + FIGURE_RULES)
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), LESSON_SCHEMA, 0.5)
    steps = []
    for s in out.get("steps", []):
        check = clean_mcqs([s["check"]])
        if check:
            steps.append({**with_figures([s])[0], "check": check[0]})
    return {"intro": out.get("intro", ""), "steps": steps, "summary": out.get("summary", "")}


def make_notes(tid, sub):
    prompt = ("Write revision notes for this subtopic in Markdown, ready to revise from before the exam:\n"
              "## Key ideas\n## Definitions\n## Formulas and rules (with units)\n## Method, step by step\n"
              "## Worked example\n## Common mistakes\n## Exam tips\nSkip a section only if it truly doesn't apply. "
              "Be concise and precise; use tables where they help.")
    return gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), temperature=0.4)


def make_slides(tid, sub):
    prompt = ("Make a teaching slide deck for this subtopic: 6 to 12 slides. First slide: title and what you'll learn. "
              "Then one idea per slide, max 5 short bullets each (LaTeX allowed), including worked-example slides. "
              "Last slide: summary and 3 quick self-check questions. Add brief speaker 'notes' to each slide "
              "explaining it in more detail.")
    return gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), SLIDES_SCHEMA, 0.5)["slides"]


def api_learn_lesson(b):
    return _cached_part(b, "lesson", make_lesson)


def api_learn_notes(b):
    return _cached_part(b, "notes", make_notes)


def api_learn_slides(b):
    return _cached_part(b, "slides", make_slides)


def api_learn_ask(b):
    tid = b["topic"]
    _, sub, _ = find_sub(tid, b["sub"])
    focus = b.get("focus") or ""
    extra = ("The student is in the middle of learning this subtopic and has paused to ask a question. Answer it directly "
             "(keep it focused on what they asked), then (if useful) check their understanding with one short question. "
             + (f"\nThey are currently looking at:\n{focus[:4000]}" if focus else ""))
    out = tutor_reply(tid, b.get("messages", []), learn_system(tid, sub, extra))
    with LOCK:
        L = learn_load(tid)
        L["subs"].setdefault(sub["id"], {})["last"] = today()
        learn_save(tid, L)
        p = progress()
        record_study(p, tid)
        save("progress.json", p)
        log_event("learn_ask", tid, sub=sub["id"])
    return out


SOLVE_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "index": {"type": "integer"}}, "required": ["n", "index"]}}}, "required": ["answers"]}


def verify_mcqs(tid, qs):
    """A second pass solves every question without seeing the key; questions whose key it disagrees with are dropped."""
    if TOPICS[tid]["subject"] == "EAP" or len(qs) < 3:
        return qs
    listing = "\n\n".join(f"Q{i}. {q['question']}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(q["options"]))
                          + (f"\n  [figure: {json.dumps(q['figure'], ensure_ascii=False)[:1500]}]" if q.get("figure") else "")
                          for i, q in enumerate(qs))
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            "Solve each multiple-choice question carefully yourself, with full working in your head. For each give "
            "'index' = the number of the one correct option, or -1 if no option is correct, more than one is correct, "
            f"or the question cannot be answered as written.\n\n{listing}"}]}],
            f"You are a careful examiner for {topic_label(tid)}.", SOLVE_SCHEMA, 0.1)
    except GeminiError:
        return qs                              # over quota or busy: keep the test as written
    solved = {a.get("n"): a.get("index") for a in r.get("answers", [])}
    good = [q for i, q in enumerate(qs) if solved.get(i, q["answer_index"]) == q["answer_index"]]
    return good if len(good) >= max(3, len(qs) // 2) else qs


def api_learn_test(b):
    tid = b["topic"]
    _, sub, _ = find_sub(tid, b["sub"])
    n = max(3, min(int(b.get("count", 10)), 25))
    level = b.get("difficulty", "medium")
    prompt = (f"Write a {n + 2}-question multiple-choice test on this subtopic. Difficulty: {DIFFICULTY.get(level, DIFFICULTY['medium'])}. "
              "Cover every learning objective. 4 options each with exactly one correct answer (answer_index 0-3), "
              "plausible distractors based on common mistakes, and an explanation of the correct answer. Vary where the "
              "correct option sits. Put the objective being tested in 'skill'. Calculations should need working, not guessing."
              + FIGURE_RULES)
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), TEST_SCHEMA, 0.8)
    qs = verify_mcqs(tid, clean_mcqs(out.get("questions", [])))[:n]
    if not qs:
        raise GeminiError("Gemini didn't return usable questions. Try again.")
    with LOCK:
        log_event("learn_test_generate", tid, sub=sub["id"], count=len(qs))
    return {"questions": qs, "pass_mark": PASS_MARK}


def api_learn_submit(b):
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if total <= 0 or not 0 <= correct <= total:
        raise ValueError("bad score")
    pct = round(100 * correct / total)
    with LOCK:
        L, sub, _ = find_sub(tid, b["sub"])
        st = L["subs"].setdefault(sub["id"], {})
        st.setdefault("tests", []).append({"date": today(), "difficulty": b.get("difficulty", "medium"),
                                           "total": total, "correct": correct, "pct": pct})
        st["best"] = max(st.get("best", 0), pct)
        st["last"] = today()
        newly = pct >= PASS_MARK and not st.get("passed")
        if pct >= PASS_MARK:
            st["passed"] = st.get("passed") or today()
        learn_save(tid, L)
        p = progress()
        record_study(p, tid, correct, total)  # feeds topic confidence the same way as marked practice
        save("progress.json", p)
        log_event("learn_test", tid, sub=sub["id"], score=correct, max=total, pct=pct, difficulty=b.get("difficulty"))
    return {"pct": pct, "passed": pct >= PASS_MARK, "newly_passed": newly, "pass_mark": PASS_MARK,
            "summary": learn_summary(tid), "confidence": p["topics"][tid]["confidence"]}


def api_learn_save(b):
    """Saves lesson position and personal notes."""
    tid = b["topic"]
    with LOCK:
        L, sub, _ = find_sub(tid, b["sub"])
        st = L["subs"].setdefault(sub["id"], {})
        if "step" in b:
            st["step"] = max(0, int(b["step"]))
            st["last"] = today()
            if b.get("lesson_done") and not st.get("lesson_done"):
                st["lesson_done"] = today()
                log_event("learn_lesson_done", tid, sub=sub["id"])
        if "my_notes" in b:
            st["my_notes"] = str(b["my_notes"])[:50_000]
        learn_save(tid, L)
    return {"ok": True}


def api_learn_mistakes(b):
    """Turns wrong test answers into flashcards (no AI needed)."""
    tid = b["topic"]
    items = b.get("items", [])[:30]
    with LOCK:
        cards = load("cards.json", [])
        for it in items:
            back = f"**{it['answer']}**\n\n{it.get('explanation', '')}".strip()
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": it["question"], "back": back,
                          "ease": 2.5, "interval": 0, "reps": 0, "due": today(), "created": today(), "from": "mistake"})
        save("cards.json", cards)
        log_event("cards_from_mistakes", tid, count=len(items))
    return {"added": len(items)}


def learn_stats():
    rows, tests = [], []
    for tid, t in TOPICS.items():
        s = learn_summary(tid)
        rows.append({"topic": tid, "name": t["name"], "subject": t["subject"], **s})
        for sid, st in learn_load(tid).get("subs", {}).items():
            tests += [{**x, "topic": tid, "subject": t["subject"]} for x in st.get("tests", [])]
    tests.sort(key=lambda x: x["date"])
    built = [r for r in rows if r["built"]]
    return {"topics_built": len(built), "subs_total": sum(r["subs"] for r in built),
            "subs_passed": sum(r["passed"] for r in built), "topics_complete": sum(r["complete"] for r in built),
            "tests": len(tests), "tests_passed": sum(x["pct"] >= PASS_MARK for x in tests),
            "avg_test": round(sum(x["pct"] for x in tests) / len(tests)) if tests else None,
            "by_subject": {s: {"subs": sum(r["subs"] for r in built if r["subject"] == s),
                               "passed": sum(r["passed"] for r in built if r["subject"] == s)} for s in CONFIG["subjects"]},
            "recent_tests": [{**x, "name": TOPICS[x["topic"]]["name"]} for x in tests[-12:]], "pass_mark": PASS_MARK}


# ---------- study in Gemini: a pack (prompt + course files) for a session in the Gemini app ----------
PACKS = APP.parent / "Study packs"
PACK_MAX_PDFS = 7          # Gemini takes up to 10 files per message: PDFs + 1 notes file + room to spare
PACK_TEXT_LIMIT = 250_000


def pack_rank(f):
    n = f.rsplit("/", 1)[-1].lower()
    if re.search(r"notes|^pp |slides|worked|example|lesson|summary", n):
        return 0
    if re.search(r"exam|question|\bms\b|mark scheme|worksheet|practice", n):
        return 1
    return 2


def api_handoff(b):
    import shutil
    import subprocess
    tid = b["topic"]
    t = TOPICS[tid]
    L = learn_load(tid)
    sub = next((s for s in L.get("outline") or [] if s["id"] == b.get("sub")), None)
    files = [f for f in topic_files(tid) if (TEXT / f).stat().st_size > 60]
    shared = [f for f in files if matches(f, CONFIG["subjects"][t["subject"]].get("shared", []))]   # formula sheet etc.
    wanted = [w.lower() for w in (sub or {}).get("files", []) if w]
    first = [f for f in files if any(w in f.lower() for w in wanted) and f not in shared]
    rest = sorted((f for f in files if f not in first and f not in shared), key=pack_rank)
    ordered = first + shared + rest

    # folder: Study packs/<Subject> - <Topic>[ - <Subtopic>]
    name = re.sub(r'[\\/:*?"<>|]+', " ", f"{SHORT_SUBJECT.get(t['subject'], t['subject'])} - {t['name']}"
                  + (f" - {sub['title']}" if sub else ""))
    name = re.sub(r"\s+", " ", name).strip()[:110]
    folder = (PACKS / name).resolve()
    if not str(folder).startswith(str(PACKS.resolve())):
        raise ValueError("bad name")
    if folder.exists():
        shutil.rmtree(folder)       # a pack is rebuilt every time; nothing else lives there
    folder.mkdir(parents=True)

    import hashlib
    pdfs, seen = [], set()
    for f in ordered:
        o = original_for(f)
        if o and o.suffix.lower() == ".pdf" and len(pdfs) < PACK_MAX_PDFS and o.stat().st_size < 40_000_000:
            h = hashlib.sha1(o.read_bytes()).hexdigest()
            if h in seen:           # the same file posted in two teachers' folders
                continue
            seen.add(h)
            pdfs.append(o)
            shutil.copy2(o, folder / f"{len(pdfs):02d} {o.name}")
    parts, total, seen_text = [], 0, set()
    for f in ordered:
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        h = hashlib.sha1(text.encode()).hexdigest()
        if h in seen_text:
            continue
        seen_text.add(h)
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > PACK_TEXT_LIMIT:
            break
        parts.append(chunk)
        total += len(chunk)
    notes_name = "00 Course notes (text of all files).txt"
    (folder / notes_name).write_text(f"Course files for {topic_label(tid)}" + (f" / {sub['title']}" if sub else "")
                                     + " (text extracted from the student's IntoStudy files)." + "".join(parts))

    prompt = handoff_prompt(tid, sub, L, [notes_name] + [f"{i:02d} {o.name}" for i, o in enumerate(pdfs, 1)])
    (folder / "Prompt.md").write_text(prompt)
    with LOCK:
        log_event("gemini_handoff", tid, sub=(sub or {}).get("id"))
    if not b.get("no_open"):
        subprocess.run(["open", str(folder)], check=False)
    return {"prompt": prompt, "folder": str(folder), "files": [notes_name] + [f"{i:02d} {o.name}" for i, o in enumerate(pdfs, 1)],
            "sub": (sub or {}).get("title")}


SHORT_SUBJECT = {"Technical Maths": "Maths"}


def handoff_prompt(tid, sub, L, files):
    t = TOPICS[tid]
    p = progress()
    tp = p["topics"].get(tid, {})
    hist = tp.get("history", [])
    mx = sum(h["max"] for h in hist)
    exams = sorted((e for e in p["exams"] if e.get("subject") == t["subject"] and e["date"] >= today()), key=lambda e: e["date"])
    exam = (f"{exams[0]['name']} on {exams[0]['date']} ({(date.fromisoformat(exams[0]['date']) - date.today()).days} days away)"
            if exams else "end of Semester 1 exams")
    lines = [
        "You are my personal tutor for this study session. Use Guided Learning: teach me step by step, one idea at a time, "
        "and make me think. Do not dump everything at once.",
        "",
        "## Who I am",
        f"- Student on the NCUK International Foundation Year at INTO Manchester. Subject: {t['subject']}.",
        f"- Next exam: {exam}.",
        f"- Where my class is now: {next((l for l in week_now_text().splitlines() if l.startswith('- ' + t['subject'] + ':')), 'unknown').removeprefix('- ')}",
        "- English is my second language: explain in simple, clear English (if I write in Russian, answer in Russian, but "
        "keep the subject terms in English as they appear in my exam).",
        "",
        "## What we are studying",
        f"- Topic: **{t['name']}**" + (f" ({t['code']})" if t.get("code") else "") + f", taught in class from {t['start']}.",
    ]
    if sub:
        lines += [f"- Today's part: **{sub['title']}**. {sub.get('summary', '')}",
                  "- By the end I must be able to:"] + [f"  - {o}" for o in sub.get("objectives", [])]
    outline = L.get("outline") or []
    if not sub and outline:
        lines += ["- We cover the whole topic: go through the parts below in order, starting with the first one I haven't passed."]
    elif not sub:
        lines += ["- We cover the whole topic. First, from my files, list the 4-7 things I must be able to do for the exam "
                  "on this topic (short objectives), then teach them in that order."]
    if outline:
        lines += ["", "## The whole topic and my progress (pass = 80%+ in a mini test)"]
        for s in outline:
            st = L.get("subs", {}).get(s["id"], {})
            state = (f"passed {st['passed']}" if st.get("passed") else f"best test {st['best']}%" if st.get("tests")
                     else "started" if st.get("step") else "not started")
            lines.append(f"- {'→ ' if sub and s['id'] == sub['id'] else ''}{s['title']}: {state}")
    lines += ["", "## How I'm doing (from my study app)",
              f"- My confidence in this topic: {tp.get('confidence') or 'not rated'}/5"
              + (f"; marked practice answers: {round(100 * sum(h['score'] for h in hist) / mx)}% of marks over {len(hist)} answers" if mx else "; no marked practice yet") + "."]
    if sub:
        tests = L.get("subs", {}).get(sub["id"], {}).get("tests", [])
        if tests:
            lines.append("- My mini tests on this part: " + ", ".join(f"{x['pct']}% ({x['difficulty']}, {x['date']})" for x in tests[-5:]) + ".")
    cards = [c for c in load("cards.json", []) if c["topic"] == tid and c.get("ease", 2.5) < 2.3]
    if cards:
        lines.append("- Flashcards I keep getting wrong: " + "; ".join(c["front"][:120] for c in cards[:8]) + ".")
    lines += [
        "",
        "## My course files (attached)",
        f"I've attached {len(files)} files from my course: " + ", ".join(files) + ". "
        "The .txt file has the text of all my lesson notes, slides, worksheets, exam questions and the formula/data "
        "sheet for this topic; the PDFs are the originals with diagrams. Teach from THESE materials: follow their notation, methods, formula sheet and "
        "the exam style of their questions. If something isn't in them, say so before using outside knowledge.",
        "",
        "## Plan for the session",
        "1. Start with 2 quick diagnostic questions to find what I already know. Wait for my answers.",
        "2. Teach each objective in order: a short explanation, a worked example from my files' style, then one question "
        "for me to try. Wait for my answer, check it, and explain my mistakes before moving on.",
        "3. Use diagrams or tables when they help (triangles, graphs, forces...).",
        "4. When I'm ready, give me one full exam-style question with marks, then mark my answer like an examiner "
        "(M1/A1/B1 style for maths) and show the model answer.",
        "5. Finish with a 5-question mini test. At the end tell me my score as “Score: X/5”, list what I still need to "
        "revise, and give me 3 flashcards (question → answer) for my weak points.",
        "6. Last, write a block titled “Notes for Study Desk”: 8 to 12 short plain-text bullets with what I learned in "
        "this session, the mistakes I made and how to avoid them. I paste it into my study app so I don't have to "
        "search this chat later.",
        "",
        "I can interrupt at any time with a question. Start with step 1 now.",
    ]
    return "\n".join(lines)


def api_handoff_open(b):
    import subprocess
    folder = Path(b["folder"]).resolve()
    if not str(folder).startswith(str(PACKS.resolve()) + os.sep) or not folder.is_dir():
        raise ValueError("unknown folder")
    subprocess.run(["open", str(folder)], check=False)
    return {"ok": True}


def api_handoff_result(b):
    """Score from the Gemini session's final mini test, entered by the student."""
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if not 0 < total <= 100 or not 0 <= correct <= total:
        raise ValueError("bad score")
    minutes = max(0, min(int(b.get("minutes") or 0), 300))
    out = {}
    if b.get("sub"):
        out = api_learn_submit({"topic": tid, "sub": b["sub"], "total": total, "correct": correct, "difficulty": "gemini"})
    with LOCK:
        p = progress()
        if not b.get("sub"):
            record_study(p, tid, correct, total)
        tp = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
        tp.setdefault("studied", today())                       # a Gemini session counts as studied
        if not b.get("sub") and 100 * correct / total >= PASS_MARK:
            tp["confirmed"] = tp.get("confirmed") or today()    # 80%+ in Gemini's final test on the whole topic
        save("progress.json", p)
        act = load("activity.json", {"events": [], "minutes": {}})
        if minutes:
            act.setdefault("minutes", {})[today()] = act["minutes"].get(today(), 0) + minutes
            save("activity.json", act)
        log_event("gemini_session", tid, sub=b.get("sub"), score=correct, max=total, minutes=minutes)
    return {"pct": round(100 * correct / total), **{k: out[k] for k in ("passed", "newly_passed") if k in out},
            "status": topic_standing(progress(), tid)["status"]}



# ---------- course file sync (sync.py, run in .venv with Playwright) ----------
SYNC = {"proc": None}
VENV_PY = APP / ".venv" / "bin" / "python"
SYNC_EVERY_H = 2


def reload_files():
    global ALL_FILES
    ALL_FILES = sorted(str(p.relative_to(TEXT)) for p in TEXT.rglob("*.txt")) if TEXT.exists() else []


def sync_running():
    p = SYNC["proc"]
    return p is not None and p.poll() is None


def start_sync(mode="sync"):
    import subprocess
    if sync_running():
        return False
    if not VENV_PY.exists():
        raise ValueError("Sync isn't installed: run  python3 -m venv .venv && .venv/bin/pip install playwright pypdf python-pptx python-docx  in study-app")
    args = [str(VENV_PY), str(APP / "sync.py")] + (["login"] if mode == "login" else [])
    log = open(DATA / "sync.log", "a")
    SYNC["proc"] = subprocess.Popen(args, cwd=str(APP), stdout=log, stderr=log)

    def wait():
        SYNC["proc"].wait()
        log.close()
        reload_files()
        try:
            auto_sort()
        except Exception as e:
            print("sort:", e)
    threading.Thread(target=wait, daemon=True).start()
    return True


def topic_for_path(path):
    tid = topic_of(path + ".txt" if path + ".txt" in ALL_FILES else path, sorted_map())[0]
    return tid if tid in TOPICS else None


def api_sync_status(_):
    st = load("sync_status.json", {})
    if sync_running() and st.get("state") not in ("running", "login_window"):
        st["state"] = "running"
    elif not sync_running() and st.get("state") in ("running", "login_window"):
        st["state"] = "idle"   # the process ended without updating the status
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent = [x for x in load("sync_log.json", []) if x.get("date", "") >= cutoff][::-1][:60]
    for x in recent:
        x["topic"] = topic_for_path(x["path"])
        x["name"] = x["path"].rsplit("/", 1)[-1]
        txt = x["path"] + ".txt"
        x["text"] = txt if txt in ALL_FILES else None
    return {**st, "running": sync_running(), "recent": recent, "every_hours": SYNC_EVERY_H,
            "installed": VENV_PY.exists()}


def api_sync_start(b):
    started = start_sync("login" if b.get("mode") == "login" else "sync")
    return {"started": started}


def auto_sync_loop():
    """Check IntoStudy for new files on start and then every few hours while the app is open."""
    time.sleep(20)
    while True:
        try:
            st = load("sync_status.json", {})
            last = st.get("last_ok") or st.get("finished") or ""
            due = not last or datetime.fromisoformat(last) < datetime.now() - timedelta(hours=SYNC_EVERY_H)
            if VENV_PY.exists() and due and st.get("state") not in ("login", "login_window") and not sync_running():
                start_sync()
        except Exception as e:
            print("auto sync:", e)
        time.sleep(1200)


# ---------- sorter: put files that no folder rule covers into the right topic ----------
SORT_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "topic": {"type": "string"}, "also": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}},
    "required": ["n", "topic", "confidence"]}}}, "required": ["items"]}
SORT_LOCK = threading.Lock()


def auto_sort(paths=None):
    """Ask Gemini where each unsorted file belongs. Returns how many files it sorted."""
    if not api_key() or not SORT_LOCK.acquire(blocking=False):
        return 0
    try:
        smap = sorted_map()
        todo = [f for f in (paths or ALL_FILES) if f in ALL_FILES and topic_of(f, smap)[0] is None and not is_shared(f)]
        done = 0
        for subj, sub in CONFIG["subjects"].items():
            mine = [f for f in todo if f.startswith(subj + "/")]
            ids = [t["id"] for t in sub["topics"]]
            menu = "\n".join(f"- {t['id']}: {t['name']} ({t.get('code', '')}), class starts {t['start']}" for t in sub["topics"])
            for i in range(0, len(mine), 40):
                chunk = mine[i:i + 40]
                listing = "\n".join(
                    f"{n}. {f.removesuffix('.txt')}\n   begins: " +
                    re.sub(r"\s+", " ", (TEXT / f).read_text(errors="ignore"))[:500]
                    for n, f in enumerate(chunk, 1))
                prompt = (f"Sort these {subj} course files into the topic each one mainly teaches or practises.\n"
                          f"TOPICS:\n{menu}\n- general: the whole subject or many topics at once (syllabus, module "
                          "overview, timetable, past papers or mock exams covering several topics, revision checklist, "
                          "lab safety, study skills, booking posters)\n\n"
                          "Judge by the file name, the folder and the beginning of the text. Class-note numbers like "
                          "'7.3' are lesson numbers, not topics: go by the title and content. Use 'general' only when "
                          "no single topic fits. If a file clearly covers two or more topics (unit notes can cover a whole unit) (e.g. 'Exam Qs Quadratics and Cubics'), "
                          "put the main one in 'topic' and the others in 'also', so no topic misses it. "
                          "confidence: high if obvious, medium if fairly sure, low if guessing. "
                          "reason: a few words.\n\nFILES:\n" + listing)
                out = gemini([{"role": "user", "parts": [{"text": prompt}]}], None, SORT_SCHEMA, 0.1, think=False)
                with LOCK:
                    smap = sorted_map()
                    for it in out.get("items", []):
                        n, tid = it.get("n"), it.get("topic")
                        if isinstance(n, int) and 1 <= n <= len(chunk) and (tid in ids or tid == "general"):
                            if smap.get(chunk[n - 1], {}).get("by") == "you":
                                continue
                            also = [a for a in it.get("also") or [] if a in ids and a != tid][:8]
                            smap[chunk[n - 1]] = {"topic": tid, "also": also, "by": "ai", "confidence": it.get("confidence", "low"),
                                                  "reason": str(it.get("reason", ""))[:120], "date": today()}
                            done += 1
                    save("file_topics.json", smap)
        return done
    finally:
        SORT_LOCK.release()


def api_files_sort(_):
    return {"sorted": auto_sort()}


def api_files_assign(b):
    path, tid = b["path"], b["topic"]
    if path not in ALL_FILES or not (tid in TOPICS or tid == "general"):
        raise ValueError("unknown file or topic")
    with LOCK:
        smap = sorted_map()
        also = [a for a in smap.get(path, {}).get("also", []) if a != tid] if tid != "general" else []
        smap[path] = {"topic": tid, "also": also, "by": "you", "date": today()}
        save("file_topics.json", smap)
    return {"ok": True}


def api_files_check(_):
    smap = sorted_map()
    manifest = load("sync_manifest.json", {})
    st = load("sync_status.json", {})
    missing = sorted(v["path"] for v in manifest.values() if not (COURSES / v["path"]).exists())
    readable_ext = {".pdf", ".pptx", ".docx"}
    unreadable_kind = sorted(str(f.relative_to(COURSES)) for f in COURSES.rglob("*")
                             if f.is_file() and f.suffix.lower() not in readable_ext
                             and not f.name.startswith(("_", ".")) and f.suffix.lower() not in (".txt", ".md"))
    rows, how = [], Counter()
    for f in ALL_FILES:
        tid, by = topic_of(f, smap)
        if is_shared(f):
            tid, by = "general", "shared"
        how[by or "none"] += 1
        size = (TEXT / f).stat().st_size
        e = smap.get(f, {})
        if by in ("ai", "you", None) or size <= 60:
            rows.append({"path": f, "topic": tid, "by": by, "confidence": e.get("confidence"), "also": e.get("also", []),
                         "reason": e.get("reason", ""), "readable": size > 60, "subject": f.split("/")[0]})
    counts = topic_counts()
    topics = [{"id": t["id"], "files": counts[t["id"]]} for t in TOPICS.values()]
    return {"portal_files": len(manifest), "missing": missing, "failed": st.get("failed", []),
            "last_sync": st.get("last_ok"), "text_files": len(ALL_FILES), "how": how, "rows": rows,
            "topics": topics, "no_text": unreadable_kind, "sorting": SORT_LOCK.locked()}



# ---------- programme: where you are in each subject, week by week ----------
COMPASS_SCHEMA = {"type": "object", "properties": {
    "subject": {"type": "string"}, "week": {"type": "integer"},
    "topics": {"type": "array", "items": {"type": "string"}},
    "next_topics": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
    "assessments": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "date": {"type": "string"}}, "required": ["name", "date"]}},
    "todo": {"type": "array", "items": {"type": "string"}},
    "lessons": {"type": "array", "items": {"type": "object", "properties": {
        "weekday": {"type": "integer"}, "topic": {"type": "string"}, "what": {"type": "string"}},
        "required": ["weekday", "topic", "what"]}}},
    "required": ["subject", "week", "topics", "summary"]}


def fresh_on_intostudy(days=8, edited_only=False):
    """{topic id: newest date} for files teachers changed (or, unless edited_only, opened to you) recently."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = {}
    seen = [((v.get("modified") or "")[:10], v["path"]) for v in load("sync_manifest.json", {}).values()]
    if not edited_only:      # teachers often open old files ahead of time: shown, but not used for the pace
        seen += [(x.get("date", "")[:10], x["path"]) for x in load("sync_log.json", [])]
    for d, path in seen:
        if d >= cutoff:
            tid = topic_for_path(path)
            if tid and d > out.get(tid, ""):
                out[tid] = d
    return out


def api_program(_):
    if "term" not in CONFIG:
        return {"subjects": []}
    tdy = date.today()
    cur = week_of(tdy)
    fresh, edited = fresh_on_intostudy(), fresh_on_intostudy(edited_only=True)
    compass = load("compass.json", {})
    weeks_all = sorted({w["week"] for sub in CONFIG["subjects"].values() for w in sub.get("plan", [])})
    subjects = []
    for name, sub in CONFIG["subjects"].items():
        plan = {w["week"]: w for w in sub.get("plan", [])}
        week_of_topic = {}
        for w in sub.get("plan", []):
            for tid in w["topics"]:
                week_of_topic.setdefault(tid, w["week"])
        c = compass.get(name)
        c = c if c and cur and c.get("week") == cur else None
        now = (c["topics"] if c else plan.get(cur, {}).get("topics", [])) if cur else []
        nxt = (c.get("next_topics") if c and c.get("next_topics") else
               plan.get(cur + 1, {}).get("topics", [])) if cur else []
        fresh_here = [t for t in fresh if TOPICS[t]["subject"] == name]
        # are the newest uploads ahead of or behind the plan?
        fw = [week_of_topic[t] for t in edited if t in week_of_topic]
        pace = None
        if cur and fw and plan:
            pace = "ahead" if max(fw) > cur else "behind" if max(fw) < cur - 1 else "on plan"
        # did the teacher replace the plan file after Study Desk first downloaded it?
        changed = max((x["date"][:10] for x in load("sync_log.json", []) if x.get("kind") == "updated"
                       and x.get("path") == sub.get("plan_file")), default=None)
        subjects.append({
            "name": name, "plan_source": sub.get("plan_source"), "plan_note": sub.get("plan_note"), "plan_changed": changed,
            "weeks": [{**plan[n], "start": week_start(n).isoformat()} for n in sorted(plan)],
            "now": now, "next": nxt, "source": "compass" if c else "plan" if plan else None,
            "compass": c, "fresh": {t: fresh[t] for t in fresh_here}, "pace": pace})
    return {"week": cur, "week_start": week_start(cur).isoformat() if cur else None,
            "term": CONFIG["term"]["name"], "weeks": weeks_all, "subjects": subjects}


def week_now_text():
    """A few lines for AI prompts: this week's topics in each subject."""
    d = api_program(None)
    if not d.get("week"):
        return "Not in a teaching week (break or before term)."
    out = [f"Teaching week {d['week']} of {d['term']} (w/c {d['week_start']})."]
    for s in d["subjects"]:
        if s["now"] or s["next"]:
            names = lambda ids: ", ".join(TOPICS[i]["name"] for i in ids) or "nothing listed"
            src = "Compass" if s["source"] == "compass" else "course plan"
            out.append(f"- {s['name']}: now {names(s['now'])}; next {names(s['next'])} (from the {src})"
                       + (f". Compass says: {s['compass']['summary']} {'; '.join(s['compass'].get('items', []))}" if s["compass"] else ""))
    return "\n".join(out)


def api_compass(b):
    """Read a pasted Compass weekly programme (text or screenshot) and save which topics it covers."""
    text, image = (b.get("text") or "").strip(), b.get("image")
    if not text and not (image and image.get("data")):
        raise ValueError("Paste the text from Compass or add a screenshot.")
    cur = week_of(date.today())
    topics = "\n".join(f"{tid}: {t['subject']}: {t['name']}" for tid, t in TOPICS.items())
    plans = json.dumps({n: s.get("plan", []) for n, s in CONFIG["subjects"].items()}, ensure_ascii=False)
    prompt = (f"Today is {today()}, teaching week {cur} (week 1 began {CONFIG['term']['week1']}).\n"
              f"A student copied this weekly programme from their school's Compass page. Work out which subject it is "
              f"for (one of {list(CONFIG['subjects'])}), which teaching week it describes (use dates in it; if none, "
              f"assume this week), which of the topic ids below it covers this week, and which come next if it says. "
              f"Use ONLY these topic ids:\n{topics}\n\nThe course plans, for reference:\n{plans}\n\n"
              f"summary: one short sentence on what this week is about. items: the lessons, homework, tests and "
              f"deadlines it lists, each a short line with its day or date if given. lessons: one per class day it lists, weekday 1 = Monday ... 5 = Friday, the topic id taught that day and what is covered (short). assessments: every quiz, test, assessment or homework deadline it gives a day or date for, date as YYYY-MM-DD. todo: each thing the teacher asks the student to DO (complete a homework, read, bring, prepare), one short line each, keeping names like Hwk10; empty if none.\n\nCOMPASS TEXT:\n{text or '(see the screenshot)'}")
    r = gemini([{"role": "user", "parts": user_parts(prompt, image)}], schema=COMPASS_SCHEMA, temperature=0.2, think=False)
    if r.get("subject") not in CONFIG["subjects"]:
        raise ValueError("Couldn't tell which subject this is. Paste a bit more of the page.")
    ok = lambda ids: [t for t in ids or [] if t in TOPICS and TOPICS[t]["subject"] == r["subject"]]
    entry = {"week": r.get("week") or cur, "topics": ok(r.get("topics")), "next_topics": ok(r.get("next_topics")),
             "summary": r.get("summary", ""), "items": (r.get("items") or [])[:20], "date": today(),
             "assessments": [{"name": str(a["name"])[:120], "date": a["date"]} for a in r.get("assessments") or []
                             if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(a.get("date", "")))][:10],
             "lessons": [{"date": (week_start(r.get("week") or cur) + timedelta(days=l["weekday"] - 1)).isoformat(),
                          "topic": l["topic"], "what": l.get("what", "")}
                         for l in r.get("lessons") or [] if 1 <= l.get("weekday", 0) <= 7 and ok([l.get("topic")])],
             "text": text[:5000]}
    with LOCK:
        c = load("compass.json", {})
        c[r["subject"]] = entry
        save("compass.json", c)
    todo = [str(x).strip() for x in r.get("todo") or [] if str(x).strip()][:8]
    added = add_tasks("\n".join(todo), "compass", r["subject"]) if todo else []
    return {"subject": r["subject"], **entry, "tasks_added": [t["text"] for t in added]}



# ---------- standing: what you have studied, confirmed, and where you are behind ----------
CHECK_QUESTIONS = 8


def topic_standing(p, tid):
    """Status of one topic.
    studied   = you marked it done (after a Gemini session), or you worked on it here.
    confirmed = you proved it: 80%+ in the check test here or in Gemini's final test, or every subtopic passed.
    behind    = its class weeks are over and you have not studied it.  to_confirm = over and studied, not confirmed."""
    tp, ls, t = p["topics"].get(tid, {}), learn_summary(tid), TOPICS[tid]
    confirmed = tp.get("confirmed") or (ls["last_learned"] if ls["complete"] else None)
    studied = tp.get("studied") or (tp.get("last") if (tp.get("history") or ls["started"] or ls["passed"]) else None)
    status = "confirmed" if confirmed else "studied" if studied else "none"
    weeks = [w["week"] for w in CONFIG["subjects"][t["subject"]].get("plan", []) if tid in w["topics"]]
    ended = bool(weeks) and "term" in CONFIG and week_start(max(weeks)) + timedelta(days=7) <= date.today()
    checks = tp.get("checks", [])
    return {"status": status, "studied_on": studied, "confirmed_on": confirmed,
            "weeks": [min(weeks), max(weeks)] if weeks else None, "ended": ended,
            "behind": ended and status == "none", "to_confirm": ended and status == "studied",
            "check_best": max((c["pct"] for c in checks), default=None), "checks": len(checks)}


def standing():
    """Everything the Today page, the assistant and the Telegram brief need about where you stand."""
    p = progress()
    rows = [{"id": tid, "name": t["name"], "subject": t["subject"], **topic_standing(p, tid)} for tid, t in TOPICS.items()]
    ended = [r for r in rows if r["ended"]]
    return {"rows": rows,
            "behind": sorted((r for r in rows if r["behind"]), key=lambda r: r["weeks"][1]),
            "to_confirm": sorted((r for r in rows if r["to_confirm"]), key=lambda r: r["weeks"][1]),
            "confirmed": sum(r["status"] == "confirmed" for r in ended), "ended": len(ended)}


def api_topic_mark(b):
    """You tick a topic as studied (or untick it)."""
    tid = b["topic"]
    with LOCK:
        p = progress()
        tp = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
        if b.get("studied", True):
            tp["studied"] = today()
            tp["last"] = today()
        else:
            tp.pop("studied", None)
        save("progress.json", p)
        log_event("mark_studied" if b.get("studied", True) else "unmark_studied", tid)
        return topic_standing(p, tid)


def api_check_test(b):
    """A short test over the whole topic: pass it to confirm you know the topic."""
    tid = b["topic"]
    n = CHECK_QUESTIONS
    prompt = (f"Write a {n + 2}-question multiple-choice check on the WHOLE topic, to confirm the student really knows it "
              "after studying it elsewhere. Spread the questions over all the main ideas and skills of the topic in the "
              "course materials; exam-style, most need working or real understanding, not recall of a definition. "
              "4 options each with exactly one correct answer (answer_index 0-3), distractors from common mistakes, and "
              "an explanation of the correct answer. Vary where the correct option sits. 'skill' = the idea being tested."
              + FIGURE_RULES)
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid), TEST_SCHEMA, 0.8)
    qs = verify_mcqs(tid, clean_mcqs(out.get("questions", [])))[:n]
    if len(qs) < 4:
        raise GeminiError("Gemini didn't return enough usable questions. Try again.")
    with LOCK:
        log_event("check_generate", tid, count=len(qs))
    return {"questions": qs, "pass_mark": PASS_MARK}


def api_check_submit(b):
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if total <= 0 or not 0 <= correct <= total:
        raise ValueError("bad score")
    pct = round(100 * correct / total)
    with LOCK:
        p = progress()
        record_study(p, tid, correct, total)
        tp = p["topics"][tid]
        tp.setdefault("checks", []).append({"date": today(), "pct": pct, "correct": correct, "total": total})
        tp.setdefault("studied", today())
        newly = pct >= PASS_MARK and not tp.get("confirmed")
        if pct >= PASS_MARK:
            tp["confirmed"] = tp.get("confirmed") or today()
        save("progress.json", p)
        log_event("check", tid, score=correct, max=total, pct=pct)
        return {"pct": pct, "passed": pct >= PASS_MARK, "newly_confirmed": newly, "pass_mark": PASS_MARK,
                **topic_standing(p, tid)}


def upcoming_assessments():
    """Dated things to prepare for: exams (Settings), assessments in the course plans, and what Compass listed."""
    tdy, out = today(), []
    for e in progress()["exams"]:
        out.append({"date": e["date"], "name": e["name"], "subject": e.get("subject", ""), "kind": "exam"})
    for name, sub in CONFIG["subjects"].items():
        for w in sub.get("plan", []):
            if w.get("assessment") and "term" in CONFIG:
                out.append({"date": week_start(w["week"]).isoformat(), "week": w["week"], "name": w["assessment"],
                            "subject": name, "kind": "plan"})
    for name, c in load("compass.json", {}).items():
        for a in c.get("assessments", []):
            out.append({"date": a["date"], "name": a["name"], "subject": name, "kind": "compass"})
    # a plan assessment is "in the week of": keep it until that week is over
    keep = lambda a: (date.fromisoformat(a["date"]) + timedelta(days=6 if a["kind"] == "plan" else 0)).isoformat() >= tdy
    return sorted((a for a in out if keep(a)), key=lambda a: a["date"])


def brief_text():
    """Plain-text status for Telegram (Russian labels, topic names as in the course)."""
    tdy = date.today()
    s, prog = standing(), api_program(None)
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    short = lambda name: SHORT_SUBJECT.get(name, name)
    in_break = lambda d: any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", []))

    def day_topic(subject, d):
        sub = next((x for x in prog.get("subjects", []) if x["name"] == subject), None)
        if not sub:
            return ""
        for l in (sub.get("compass") or {}).get("lessons", []):
            if l["date"] == d.isoformat():
                name = TOPICS[l["topic"]]["name"]
                same = l.get("what", "").lower().replace(" and ", " & ") in name.lower()
                return name if not l.get("what") or same else f"{name} ({l['what']})"
        w = next((w for w in sub["weeks"] if w["week"] == week_of(d)), None)
        return ", ".join(TOPICS[i]["name"] for i in w["topics"]) if w else ""

    def classes(d):
        if in_break(d):
            return ["пар нет (каникулы)"]
        cs = sorted((c for c in CONFIG.get("timetable", []) if c["day"] == d.isoweekday()), key=lambda c: c["start"])
        return [f"{c['start']}–{c['end']} {short(c['subject'])}, {c['room']}: {day_topic(c['subject'], d) or 'тема не указана'}"
                for c in cs] or ["пар нет"]

    nxt = tdy + timedelta(days=1)
    while not [c for c in CONFIG.get("timetable", []) if c["day"] == nxt.isoweekday()] and (nxt - tdy).days < 7:
        nxt += timedelta(days=1)
    L = [f"Study Desk · {days[tdy.weekday()]} {tdy.day}.{tdy.month:02d}"
         + (f" · неделя {prog['week']} ({prog['term']})" if prog.get("week") else "")]
    L += ["", "СЕГОДНЯ"] + [f"• {x}" for x in classes(tdy)]
    L += ["", f"ДАЛЬШЕ ({days[nxt.weekday()]} {nxt.day}.{nxt.month:02d})"] + [f"• {x}" for x in classes(nxt)]
    tasks = open_tasks()
    if tasks:
        L += ["", f"ЗАДАЧИ ({len(tasks)})"] + [f"• {task_line(t)}" for t in tasks[:10]] + \
             ([f"• и ещё {len(tasks) - 10}"] if len(tasks) > 10 else [])
    beh = s["behind"][::-1]                                # most recent first
    L += ["", f"ОТСТАЁШЬ: пройдено в классе, не изучено ({len(beh)})"] + \
         ([f"• {short(r['subject'])}: {r['name']} (неделя {r['weeks'][1]})" for r in beh[:8]] or ["• ничего"]) + \
         ([f"• и ещё {len(beh) - 8} более старых"] if len(beh) > 8 else [])
    L += ["", f"ИЗУЧЕНО, НЕ ПОДТВЕРЖДЕНО ({len(s['to_confirm'])})"] + \
         ([f"• {short(r['subject'])}: {r['name']}" for r in s["to_confirm"][:8]] or ["• ничего"])
    L += ["", f"ПОДТВЕРЖДЕНО: {s['confirmed']} из {s['ended']} законченных тем"]
    cutoff = (datetime.now() - timedelta(hours=26)).isoformat(timespec="seconds")
    new = [x for x in load("sync_log.json", []) if x.get("date", "") >= cutoff]
    if new:
        L += ["", new_files_text(new, "НОВЫЕ ФАЙЛЫ за сутки")]
    st = load("sync_status.json", {})
    if st.get("signed_in") is False or st.get("state") == "login":
        L += ["", "⚠ IntoStudy просит войти заново: открой Study Desk и нажми Connect IntoStudy."]
    elif st.get("last_ok") and st["last_ok"] < (datetime.now() - timedelta(hours=30)).isoformat():
        L += ["", f"⚠ IntoStudy не проверялся с {st['last_ok'][:16].replace('T', ' ')}."]
    ass = [a for a in upcoming_assessments() if (date.fromisoformat(a["date"]) - tdy).days <= 21 and a["kind"] != "exam"]
    if ass:
        L += ["", "ОЦЕНИВАНИЯ СКОРО"] + [f"• {short(a['subject'])}: {a['name']} ("
                                         + (f"неделя с {a['date'][8:]}.{a['date'][5:7]}" if a["kind"] == "plan" else f"{a['date'][8:]}.{a['date'][5:7]}") + ")" for a in ass]
    exams = [e for e in compute_stats()["exams"] if e["days"] >= 0]
    if exams:
        L += ["", "ЭКЗАМЕНЫ"] + [f"• {e['name']}: через {e['days']} дн., подтверждено {e['ready']} из {e['in_scope']} тем"
                                 + (f", отстаёшь по {e['behind']}" if e.get("behind") else "") for e in exams]
    if prog.get("week") and tdy.isoweekday() <= 5 and any(x["name"] == "Physics" and x["source"] != "compass" for x in prog.get("subjects", [])):
        L += ["", "Физика: расписание этой недели из Compass ещё не добавлено. Пришли сюда текст поста, я сохраню."]
    return "\n".join(L)


def api_brief(_):
    return {"text": brief_text()}



# ---------- review notes: one detailed sheet per topic, to recall everything at a glance ----------
REVIEWS = DATA / "review"
REVIEW_BUSY = set()
REVIEW_CHECK = {"type": "object", "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}}},
                "required": ["ok", "problems"]}


def review_load(tid):
    try:
        return json.loads((REVIEWS / f"{tid}.json").read_text())
    except (OSError, ValueError):
        return {}


def review_save(tid, obj):
    REVIEWS.mkdir(exist_ok=True)
    tmp = REVIEWS / f"{tid}.tmp"
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(REVIEWS / f"{tid}.json")


def review_sig(tid):
    return sorted(topic_files(tid, shared=False))


def make_review(tid):
    """Write the review sheet (Markdown for the app, plain text for Telegram), checked once against the files."""
    eap = TOPICS[tid]["subject"] == "EAP"
    prompt = (
        "Write REVIEW NOTES for the WHOLE topic, so the student can recall everything in one look before a class test or "
        "the exam and not re-read their chat history. Use ONLY the course materials: their notation, formulas, methods "
        "and exam-question style. Markdown, exactly these sections:\n"
        "## In one minute\n3 to 5 bullets: what this topic is about and what you must be able to do.\n"
        "## Key ideas and definitions\nEach term in **bold** with its meaning in one line, in the wording an examiner accepts.\n"
        + ("## Useful language and structures\nThe phrases, sentence frames and structures taught, with one example each.\n"
           if eap else
           "## Formulas\nA table: Formula | Symbols and units | When to use it. Every formula must be in the materials or "
           "the formula sheet; say which ones are given in the exam and which must be memorised if the materials say.\n")
        + "## How to do the typical exam questions\nFor each question type in the materials: the steps as a numbered "
        "list, then one short worked example with real numbers" + (" or a real sentence" if eap else " and units") + ".\n"
        "## Mistakes that lose marks\nBullets, each with the fix.\n"
        "## Check yourself\n5 short questions covering the topic, then 'Answers:' with the 5 answers.\n"
        "Rules: complete but compact (700 to 1100 words), no introduction or closing words, short lines, inline maths "
        "in $...$ and display maths in $$...$$ alone on its own line. Work every example through and check its numbers.")
    sys_ = learn_system(tid)
    md = gemini([{"role": "user", "parts": [{"text": prompt}]}], sys_, temperature=0.3)
    try:    # one independent look for wrong formulas, numbers or missing parts; fix once if needed
        c = gemini([{"role": "user", "parts": [{"text":
            "Check these review notes against the course materials. List only real problems: a wrong or invented "
            "formula, a wrong number or unit in an example, a wrong definition, or a main part of the topic that is "
            "missing. ok = true if there are none.\n\nNOTES:\n" + md}]}], sys_, REVIEW_CHECK, 0.1)
        if not c.get("ok") and c.get("problems"):
            md = gemini([{"role": "user", "parts": [{"text": prompt + "\n\nA checker found these problems in your last "
                         "draft; fix them all and return the full corrected notes:\n- " + "\n- ".join(c["problems"][:12])
                         + "\n\nLAST DRAFT:\n" + md}]}], sys_, temperature=0.2)
    except GeminiError:
        pass
    md = tidy_math(md.strip())
    return {"md": md, "tg": make_tg(md), "made": today(), "files": review_sig(tid)}


def make_tg(md):
    """The same notes as one plain-text Telegram message (Telegram shows no LaTeX or tables)."""
    tg = gemini([{"role": "user", "parts": [{"text":
        "Rewrite these review notes as ONE compact Telegram message in plain text, HARD LIMIT 3000 characters. No "
        "Markdown, no tables, no LaTeX: write formulas with Unicode (v², ½mv², √, θ, ω, Δ, ×, ÷, →, ≈). Section titles "
        "in CAPITALS on their own line, items start with '• ', numbered steps as '1) '. Keep: every formula with a "
        "short key to its symbols (one line per formula, no 'when to use' column), the definitions shortened to a few "
        "words each, the steps for each question type with a one-line example, the mistakes (one short line each) and "
        "the 5 check questions with one-line answers. Cut wording, never cut a section.\n\nNOTES:\n" + md}]}],
        "You convert study notes to clean, compact plain text.", temperature=0.2, think=False).strip()
    if len(tg) > 3800:                                   # never cut in the middle of a line
        tg = tg[:3800].rsplit("\n", 1)[0] + "\n… (полная версия в Study Desk)"
    return tg



def build_review(tid):
    """Generate and store; one at a time per topic. Returns the stored review (old one if generation fails)."""
    if tid in REVIEW_BUSY:
        return review_load(tid)
    REVIEW_BUSY.add(tid)
    try:
        new = make_review(tid)
        with LOCK:
            r = review_load(tid)
            r.update(new)
            review_save(tid, r)
            log_event("review_made", tid)
        return r
    finally:
        REVIEW_BUSY.discard(tid)


def review_state(tid):
    r = review_load(tid)
    return {"md": r.get("md"), "tg": r.get("tg"), "made": r.get("made"), "mine": r.get("mine", ""),
            "stale": bool(r.get("md")) and r.get("files") != review_sig(tid), "generating": tid in REVIEW_BUSY}


def api_review(b):
    """Review notes for a topic. Writes them the first time (about a minute); refresh=true rewrites."""
    tid = b["topic"]
    r = review_load(tid)
    if (not r.get("md") or b.get("refresh")) and tid not in REVIEW_BUSY:
        if b.get("wait", True):
            build_review(tid)
        else:
            threading.Thread(target=lambda: _quiet(build_review, tid), daemon=True).start()
            time.sleep(0.2)
    return review_state(tid)


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        print("background:", type(e).__name__, e)


def api_review_mine(b):
    """Your own notes for a topic (for example the summary Gemini gives at the end of a session)."""
    tid = b["topic"]
    with LOCK:
        r = review_load(tid)
        r["mine"] = str(b.get("text", ""))[:20000]
        review_save(tid, r)
    return {"ok": True}


def review_warmup():
    """In the background, write review notes for the topics already taught (newest first), a few per run."""
    time.sleep(90)
    while True:
        try:
            todo = sorted((t for t in TOPICS.values() if t["start"] <= today()), key=lambda t: t["start"], reverse=True)
            for t in todo:
                r = review_load(t["id"])
                if api_key() and (not r.get("md") or r.get("files") != review_sig(t["id"])) and topic_files(t["id"], shared=False):
                    build_review(t["id"])
                    time.sleep(20)
        except GeminiError as e:            # over the free limit: try again later
            print("review warm-up paused:", e)
        except Exception as e:
            print("review warm-up:", type(e).__name__, e)
        time.sleep(3600)



# ---------- tasks: small things from teachers that must not be forgotten ----------
TASK_SCHEMA = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "subject": {"type": "string"}, "due": {"type": "string"}},
    "required": ["text", "subject", "due"]}}}, "required": ["tasks"]}


def next_class(subject, start=None):
    """(date, 'HH:MM') of the next class of a subject that has not begun yet, or None."""
    now = datetime.now()
    d = start or date.today()
    for _ in range(40):
        if not any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", [])):
            cs = sorted(c["start"] for c in CONFIG.get("timetable", []) if c["day"] == d.isoweekday() and c["subject"] == subject)
            cs = [c for c in cs if d > now.date() or c > now.strftime("%H:%M")]
            if cs:
                return d, cs[0]
        d += timedelta(days=1)
    return None


def parse_tasks(text, subject=None):
    """Turn a free-text note ('по физике доделать Hwk10 к пятнице') into tasks with a subject and a date."""
    tdy = date.today()
    tt = "; ".join(f"{['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][c['day']]} {c['start']} {c['subject']}"
                   for c in CONFIG.get("timetable", []))
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"Today is {tdy.isoformat()} ({tdy.strftime('%A')}). A student wrote a note of things to do so as not to "
            f"forget (maybe in Russian). Split it into separate tasks. For each: text = the task itself, short and "
            f"clear, in the language he wrote it, keeping names like Hwk10 exactly; subject = one of "
            f"{list(CONFIG['subjects'])} if it is clear from the note, else \"\"; due = the date as YYYY-MM-DD only if "
            f"he gave a day or date ('к пятнице', 'до 14-го', 'завтра'), else \"\" (\"на следующий урок\" = \"\").\n"
            f"His weekly classes: {tt}.\n\nNOTE:\n{text[:2000]}"}]}], None, TASK_SCHEMA, 0.0, think=False)
        tasks = [t for t in r.get("tasks", []) if str(t.get("text", "")).strip()]
    except GeminiError:
        tasks = []
    if not tasks:
        tasks = [{"text": text.strip()[:300], "subject": "", "due": ""}]
    out = []
    for t in tasks[:10]:
        subj = t.get("subject") if t.get("subject") in CONFIG["subjects"] else (subject or "")
        due = t["due"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t.get("due", ""))) else ""
        kind = "date" if due else ""
        if not due and subj and next_class(subj):
            due, kind = next_class(subj)[0].isoformat(), "class"       # by the next class of that subject
        out.append({"text": str(t["text"]).strip()[:300], "subject": subj, "due": due or None, "due_kind": kind or None})
    return out


def add_tasks(text, source, subject=None):
    new = parse_tasks(text, subject)
    with LOCK:
        tasks = load("tasks.json", [])
        norm = lambda x: re.sub(r"\W+", " ", x.lower()).strip()
        have = {norm(t["text"]) for t in tasks if not t.get("done") or t["done"] >= (date.today() - timedelta(days=14)).isoformat()}
        added = []
        for t in new:
            if norm(t["text"]) in have:
                continue
            t.update(id=uuid.uuid4().hex[:8], created=today(), done=None, source=source)
            tasks.append(t)
            added.append(t)
            have.add(norm(t["text"]))
        save("tasks.json", tasks)
        if added:
            log_event("task_add", count=len(added), source=source)
    return added


def open_tasks():
    key = lambda t: (t.get("due") or "9999", t["created"])
    return sorted((t for t in load("tasks.json", []) if not t.get("done")), key=key)


def task_when(t, tdy=None):
    """Short Russian label: when the task is due."""
    tdy = tdy or date.today()
    if not t.get("due"):
        return "без срока"
    d = date.fromisoformat(t["due"])
    day = f"{['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'][d.weekday()]} {d.day}.{d.month:02d}"
    if d < tdy:
        return f"ПРОСРОЧЕНО, было {'к паре ' if t.get('due_kind') == 'class' else 'до '}{day}"
    return ("к паре " if t.get("due_kind") == "class" else "до ") + ("сегодня" if d == tdy else "завтра" if d == tdy + timedelta(days=1) else day)


def task_line(t):
    return f"{SHORT_SUBJECT.get(t['subject'], t['subject']) + ': ' if t.get('subject') else ''}{t['text']} ({task_when(t)})"


def api_tasks(b):
    """The app's task list: add (free text), done / undo, delete."""
    act = b.get("action", "list")
    if act == "add":
        if not str(b.get("text", "")).strip():
            raise ValueError("Write the task first.")
        add_tasks(str(b["text"]), "app")
    elif act in ("done", "undo", "delete"):
        with LOCK:
            tasks = load("tasks.json", [])
            for t in tasks:
                if t["id"] == b.get("id"):
                    t["done"] = today() if act == "done" else None
            if act == "delete":
                tasks = [t for t in tasks if t["id"] != b.get("id")]
            save("tasks.json", tasks)
    return {"tasks": tasks_for_app()}


def tasks_for_app():
    recent = (date.today() - timedelta(days=2)).isoformat()
    done = [t for t in load("tasks.json", []) if t.get("done") and t["done"] >= recent]
    return [{**t, "when": task_when(t), "overdue": bool(t.get("due")) and t["due"] < today() and not t.get("done")}
            for t in open_tasks() + done]


def match_tasks(q):
    """Open tasks a free-text phrase means ('сделал домашку по физике')."""
    tasks = open_tasks()
    if not tasks:
        return []
    low = str(q or "").lower()
    if re.search(r"\b(все|всё|all)\b", low):
        return tasks
    listing = "\n".join(f"{t['id']}: {task_line(t)}" for t in tasks)
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"A student says which of his to-do tasks he has finished (maybe in Russian): \"{str(q)[:300]}\".\n"
            f"Return the ids of the tasks he means; none if it is unclear.\n\n{listing}"}]}], None, MATCH_SCHEMA, 0.0, think=False)
        ids = set(r.get("ids", []))
    except GeminiError:
        ids = {t["id"] for t in tasks if any(w in t["text"].lower() for w in re.findall(r"\w{4,}", low))}
    return [t for t in tasks if t["id"] in ids]


def remind_text():
    """Evening reminder: what must be ready for tomorrow's classes. Empty when there is nothing."""
    tmr = date.today() + timedelta(days=1)
    due = [t for t in open_tasks() if t.get("due") and t["due"] <= tmr.isoformat()]
    if not due:
        return ""
    classes = {c["subject"]: c for c in sorted(CONFIG.get("timetable", []), key=lambda c: c["start"], reverse=True)
               if c["day"] == tmr.isoweekday()}
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    L = [f"Напоминание на завтра ({days[tmr.weekday()]} {tmr.day}.{tmr.month:02d})"]
    for subj in list(classes) + [""]:
        mine = [t for t in due if (t.get("subject") or "") == subj or (subj == "" and t.get("subject") not in classes)]
        if not mine:
            continue
        c = classes.get(subj)
        L.append("")
        L.append(f"{SHORT_SUBJECT.get(subj, subj)} в {c['start']}, {c['room']} — нужно было:" if c else "Ещё не сделано:")
        L += [f"• {t['text']}" + (f" ({task_when(t)})" if not c else "") for t in mine]
    L += ["", "Сделал — напиши «сделал …», я сниму."]
    return "\n".join(L)


# ---------- for the Telegram agent (Hermes): plain-text answers ----------
MATCH_SCHEMA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}


def find_topics(q):
    """Topic ids that a free-text request means ('статика', 'moments', 'trig', 'p-statics')."""
    q = str(q or "").strip()
    low = q.lower()
    if low in TOPICS:
        return [low]
    exact = [tid for tid, t in TOPICS.items() if low and (low == t["name"].lower() or low in t["name"].lower())]
    if len(exact) == 1:
        return exact
    listing = "\n".join(f"{tid}: {t['subject']}: {t['name']}" for tid, t in TOPICS.items())
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"A student asks about a topic in their own words (maybe in Russian): \"{q[:300]}\".\nWhich of these course "
            f"topics do they mean? Return the id of the one best match; two or three ids only if it is truly unclear; "
            f"none if nothing fits.\n\n{listing}"}]}], None, MATCH_SCHEMA, 0.0, think=False)
        return [i for i in r.get("ids", []) if i in TOPICS][:3]
    except GeminiError:
        return exact[:3]


def topic_line(r):
    wk = f"wk {r['weeks'][0]}" + (f"-{r['weeks'][1]}" if r["weeks"][1] != r["weeks"][0] else "") if r.get("weeks") else "all term"
    st = ("подтверждена" if r["status"] == "confirmed" else "изучена, не подтверждена" if r["status"] == "studied"
          else "ОТСТАЁШЬ" if r["behind"] else "не изучена")
    return f"{r['name']} ({wk}): {st}"


def api_agent(b):
    """One entry point for the Telegram agent. Every answer is ready-to-send text."""
    act = b.get("action")
    if act == "status":
        return {"text": brief_text()}
    if act == "new":                                   # only what appeared since the last call (for alerts)
        with LOCK:
            st = load("agent_state.json", {})
            since = st.get("announced") or (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
            new = [x for x in load("sync_log.json", []) if x.get("date", "") > since]
            st["announced"] = max([x["date"] for x in new] + [since])
            save("agent_state.json", st)
        return {"text": new_files_text(new, "Новые файлы на IntoStudy") if new else ""}
    if act == "topics":
        rows = standing()["rows"]
        subj = str(b.get("subject") or "").lower()
        out = []
        for name in CONFIG["subjects"]:
            if subj and subj not in name.lower() and subj not in SHORT_SUBJECT.get(name, name).lower():
                continue
            out.append(f"{name.upper()}")
            out += [f"• {topic_line(r)}" for r in rows if r["subject"] == name]
            out.append("")
        return {"text": "\n".join(out).strip() or "Нет такого предмета. Предметы: " + ", ".join(CONFIG["subjects"])}
    if act in ("review", "mark"):
        ids = find_topics(b.get("topic"))
        if len(ids) != 1:
            names = [TOPICS[i]["name"] for i in ids] or [t["name"] for t in TOPICS.values()]
            return {"text": ("Уточни тему: " if ids else "Не понял, какая тема. Темы: ") + "; ".join(names)}
        tid = ids[0]
        label = f"{TOPICS[tid]['subject']}: {TOPICS[tid]['name']}"
        if act == "mark":
            st = api_topic_mark({"topic": tid, "studied": b.get("studied", True)})
            return {"text": f"{label}: " + ("отмечена как изученная. Подтверди её проверкой в Study Desk."
                                             if st["status"] != "none" else "отметка снята.")}
        r = api_review({"topic": tid})                 # the first time this writes the notes (about a minute)
        if not r["tg"]:
            return {"text": f"Конспект по теме «{label}» ещё пишется. Спроси ещё раз через минуту."}
        st = topic_standing(progress(), tid)
        mine = f"\n\nТВОИ ЗАМЕТКИ\n{r['mine'][:1200]}" if r["mine"].strip() else ""
        return {"text": f"REVIEW · {label}\nСтатус: {topic_line({**st, 'name': TOPICS[tid]['name']}).split(': ', 1)[1]}\n\n{r['tg']}{mine}"}
    if act == "compass":
        r = api_compass({"text": b.get("text", "")})
        days = "; ".join(f"{l['date'][5:]} {TOPICS[l['topic']]['name']}" for l in r.get("lessons", []))
        return {"text": f"Сохранил расписание из Compass: {r['subject']}, неделя {r['week']}. "
                        + (f"По дням: {days}. " if days else "") + (r.get("summary") or "")
                        + ("\nДобавил задачи:\n" + "\n".join(f"• {x}" for x in r["tasks_added"]) if r.get("tasks_added") else "")}
    if act == "task_add":
        added = add_tasks(str(b.get("text", "")), "telegram")
        if not added:
            return {"text": "Такая задача уже записана."}
        return {"text": "Записал:\n" + "\n".join(f"• {task_line(t)}" for t in added)
                        + "\nНапомню вечером накануне и в утренней сводке. Видно и в Study Desk (Today)."}
    if act == "task_list":
        tasks = open_tasks()
        return {"text": (f"ЗАДАЧИ ({len(tasks)})\n" + "\n".join(f"• {task_line(t)}" for t in tasks)) if tasks else "Открытых задач нет."}
    if act == "task_done":
        hit = match_tasks(b.get("text"))
        if not hit:
            tasks = open_tasks()
            return {"text": "Не понял, какую задачу снять. Открытые:\n" + "\n".join(f"• {task_line(t)}" for t in tasks)
                            if tasks else "Открытых задач нет."}
        with LOCK:
            tasks = load("tasks.json", [])
            for t in tasks:
                if t["id"] in {h["id"] for h in hit}:
                    t["done"] = today()
            save("tasks.json", tasks)
        return {"text": "Готово, снял:\n" + "\n".join(f"• {t['text']}" for t in hit)
                        + (f"\nОсталось задач: {len(open_tasks())}." if open_tasks() else "\nЗадач больше нет.")}
    if act == "remind":
        return {"text": remind_text()}
    if act == "ask":
        q = str(b.get("question", "")).strip()
        if not q:
            raise ValueError("empty question")
        ids = find_topics(q) if len(q) < 400 else []
        system = (STYLE + "\nYou answer the student's question in a Telegram chat. Plain text only: no Markdown tables, "
                  "no LaTeX (write formulas with Unicode: v², ½mv², √, θ). Be direct and complete but short (under 1800 "
                  "characters unless they ask for detail). Base facts about their progress, plan and files ONLY on the "
                  "data below; if it isn't there, say so.\n\n" + assistant_context(ids[0] if len(ids) == 1 else None))
        return {"text": gemini([{"role": "user", "parts": [{"text": q}]}], system, temperature=0.4).strip()}
    raise ValueError("unknown action")


def new_files_text(new, title):
    by = defaultdict(list)
    for x in new:
        tid = topic_for_path(x["path"])
        by[f"{SHORT_SUBJECT.get(x['course'], x['course'])}: {TOPICS[tid]['name'] if tid else 'без темы'}"].append(x["path"].rsplit("/", 1)[-1])
    return "\n".join([f"{title} ({len(new)})"] + [f"• {k}: {', '.join(v[:4])}{' …' if len(v) > 4 else ''}" for k, v in list(by.items())[:10]])


ROUTES = {
    ("POST", "/api/tasks"): api_tasks,
    ("POST", "/api/topic/search"): api_topic_search,
    ("POST", "/api/review"): api_review,
    ("POST", "/api/review/mine"): api_review_mine,
    ("POST", "/api/agent"): api_agent,
    ("GET", "/api/brief"): api_brief,
    ("POST", "/api/topic/mark"): api_topic_mark,
    ("POST", "/api/check/test"): api_check_test,
    ("POST", "/api/check/submit"): api_check_submit,
    ("GET", "/api/program"): api_program,
    ("POST", "/api/compass"): api_compass,
    ("POST", "/api/handoff"): api_handoff,
    ("POST", "/api/handoff/result"): api_handoff_result,
    ("POST", "/api/handoff/open"): api_handoff_open,
    ("POST", "/api/files/sort"): api_files_sort,
    ("POST", "/api/files/assign"): api_files_assign,
    ("GET", "/api/files/check"): api_files_check,
    ("GET", "/api/sync"): api_sync_status,
    ("POST", "/api/sync"): api_sync_start,
    ("POST", "/api/learn/outline"): api_learn_outline,
    ("POST", "/api/learn/lesson"): api_learn_lesson,
    ("POST", "/api/learn/notes"): api_learn_notes,
    ("POST", "/api/learn/slides"): api_learn_slides,
    ("POST", "/api/learn/ask"): api_learn_ask,
    ("POST", "/api/learn/test"): api_learn_test,
    ("POST", "/api/learn/submit"): api_learn_submit,
    ("POST", "/api/learn/save"): api_learn_save,
    ("POST", "/api/learn/mistakes"): api_learn_mistakes,
    ("GET", "/api/state"): api_state,
    ("GET", "/api/stats"): api_stats,
    ("POST", "/api/chat"): api_chat,
    ("POST", "/api/assistant"): api_assistant,
    ("POST", "/api/insights"): api_insights,
    ("POST", "/api/practice/generate"): api_practice_generate,
    ("POST", "/api/practice/mark"): api_practice_mark,
    ("POST", "/api/cards/generate"): api_cards_generate,
    ("POST", "/api/cards/due"): api_cards_due,
    ("POST", "/api/cards/all"): api_cards_all,
    ("POST", "/api/cards/review"): api_cards_review,
    ("POST", "/api/cards/delete"): api_cards_delete,
    ("POST", "/api/confidence"): api_confidence,
    ("POST", "/api/ping"): api_ping,
    ("POST", "/api/topic/files"): api_topic_files,
    ("POST", "/api/settings"): api_settings,
}
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # never prints request bodies
        print(time.strftime("%H:%M:%S"), self.command, self.path.split("?")[0])

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, method):
        fn = ROUTES.get((method, self.path.split("?")[0]))
        if not fn:
            return self.send(404, {"error": "not found"})
        try:
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length", 0))
                if n > 25_000_000:
                    return self.send(413, {"error": "Too large (max ~18 MB image)."})
                body = json.loads(self.rfile.read(n) or b"{}")
                if body.get("topic") and body["topic"] not in TOPICS and fn not in (api_assistant, api_files_assign, api_agent):
                    return self.send(400, {"error": "Unknown topic"})
            self.send(200, fn(body))
        except GeminiError as e:
            self.send(502, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            self.send(400, {"error": f"Bad request: {e}"})
        except Exception as e:  # keep the server alive
            self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def serve_original(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel = (q.get("p") or [""])[0]
        f = original_for(rel) if rel in ALL_FILES else None
        if not f:
            return self.send(404, b"File not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        disp = "inline" if ctype in ("application/pdf",) or ctype.startswith("image/") else "attachment"
        self.send(200, f.read_bytes(), ctype,
                  {"Content-Disposition": f"{disp}; filename*=UTF-8''{urllib.parse.quote(f.name)}"})

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.handle_api("GET")
        if self.path.startswith("/file?"):
            return self.serve_original()
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        f = (STATIC / rel).resolve()
        if not str(f).startswith(str(STATIC)) or not f.is_file():
            f = STATIC / "index.html"
        self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def do_POST(self):
        self.handle_api("POST")


if __name__ == "__main__":
    print(f"Study Desk running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    print(f"Course text: {TEXT} ({len(ALL_FILES)} files)")
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(5), auto_sort()), daemon=True).start()
    threading.Thread(target=review_warmup, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
